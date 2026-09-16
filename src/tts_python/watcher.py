from __future__ import annotations

import logging
from pathlib import Path
import shutil
import time
import threading
from speech_common.credentials import redact
from speech_common.retry import retry_delay
from collections.abc import Callable

from .config import AppConfig
from .database import JobStore
from .errors import is_retryable
from .processor import Processor
from .stability import StabilityTracker


POLICIES = {"normal": False, "private": True, "cloud": False}


class Watcher:
    """Discover stable sources, persist work, and execute due jobs."""

    def __init__(self, config: AppConfig, processor: Processor, store: JobStore,
                 *, clock: Callable[[], float] = time.time):
        self.config, self.processor, self.store, self.clock = config, processor, store, clock
        self.log = logging.getLogger("tts_python.watcher")
        self.stop = threading.Event()
        self.stability = StabilityTracker(config.stability_checks, config.stability_seconds)

    def discover(self) -> int:
        queued = 0
        now = self.clock()
        for policy in POLICIES:
            folder = self.config.paths.inbox / policy
            folder.mkdir(parents=True, exist_ok=True)
            for source in sorted(folder.glob("*")):
                if not source.is_file() or source.suffix.lower() not in {".txt", ".md"}:
                    continue
                try:
                    if not self.stability.observe(source, now):
                        continue
                    job, created = self.store.enqueue(source, policy)
                    if created:
                        queued += 1
                        self.log.info("job queued: %s (%s)", job["id"], source.name)
                    elif job["status"] == "completed":
                        # A byte-version already completed before restart. Archive the
                        # duplicate arrival without paying for or overwriting output.
                        self._move_unique(source, self.config.paths.archive / policy)
                    self.stability.forget(source)
                except FileNotFoundError:
                    self.stability.forget(source)
        return queued

    def process_due(self) -> int:
        processed = 0
        for row in self.store.due(self.clock()):
            if self.stop.is_set():
                break
            source = Path(row["source"])
            if not source.is_file():
                self.store.update(row["id"], "failed", error="source disappeared before processing")
                continue
            attempt = self.store.start_attempt(row["id"])
            self.log.info("processing started: %s attempt %d", row["id"], attempt["attempts"])
            private = POLICIES.get(row["policy"], False)
            try:
                result = self.processor.convert(source, private=private)
                archived = self._move_unique(source, self.config.paths.archive / row["policy"])
                self.store.update(
                    row["id"], "completed", output=result.audio, speech_sha256=result.digest,
                    provider=getattr(result, "provider", self.config.tts.preferred),
                    model=getattr(result, "model", ""),
                    source_path=archived,
                )
                self.log.info("job completed: %s", row["id"])
            except Exception as exc:
                retries_used = attempt["attempts"] - 1
                if is_retryable(exc) and retries_used < self.config.max_retries:
                    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
                    delay = retry_delay(retries_used, initial=self.config.retry_initial_seconds,
                                        maximum=self.config.retry_max_seconds,
                                        retry_after=headers.get("retry-after"), jitter=False)
                    self.store.update(row["id"], "retry_wait", error=redact(str(exc))[:1600],
                                      next_attempt_at=self.clock() + delay)
                    self.log.warning("retry scheduled: %s in %.1f seconds (%s)", row["id"], delay, exc)
                else:
                    failed_path = self._move_unique(source, self.config.paths.failed / row["policy"])
                    self.store.update(row["id"], "failed", error=redact(str(exc))[:1600], source_path=failed_path)
                    self.log.error("permanent failure: %s (%s)", row["id"], exc)
            processed += 1
        return processed

    def scan_once(self) -> int:
        self.discover()
        return self.process_due()

    @staticmethod
    def _move_unique(source: Path, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / source.name
        counter = 1
        while destination.exists():
            destination = directory / f"{source.stem}.{counter}{source.suffix}"
            counter += 1
        return Path(shutil.move(source, destination))

    def run(self, idle_callback: Callable[[], None] | None = None) -> None:
        while not self.stop.is_set():
            try:
                self.scan_once()
                if idle_callback:
                    idle_callback()
            except Exception:
                self.log.exception("watcher scan failed; continuing")
            self.stop.wait(self.config.watcher_interval)
