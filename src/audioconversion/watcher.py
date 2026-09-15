from __future__ import annotations
import logging
from pathlib import Path
import shutil
import time
from .config import AppConfig
from .database import JobStore
from .processor import Processor


POLICIES = {"normal": False, "private": True, "cloud": False}


class Watcher:
    def __init__(self, config: AppConfig, processor: Processor, store: JobStore):
        self.config, self.processor, self.store = config, processor, store
        self.log = logging.getLogger("audioconversion.watcher")
        self.seen: set[Path] = set()

    def scan_once(self) -> int:
        processed = 0
        for policy, private in POLICIES.items():
            folder = self.config.paths.inbox / policy
            folder.mkdir(parents=True, exist_ok=True)
            for source in sorted(folder.glob("*")):
                if source in self.seen or not source.is_file() or source.suffix.lower() not in {".txt", ".md"}:
                    continue
                age = time.time() - source.stat().st_mtime
                if age < self.config.stability_seconds:
                    continue
                self.seen.add(source)
                job_id = self.store.add(source, policy)
                self.store.update(job_id, "processing")
                try:
                    result = self.processor.convert(source, private=private)
                    destination = self.config.paths.archive / policy / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(source, destination)
                    self.store.update(job_id, "completed", output=result.audio)
                except Exception as exc:
                    self.log.exception("job %s failed", job_id)
                    destination = self.config.paths.failed / policy / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(source, destination)
                    self.store.update(job_id, "failed", error=str(exc)[:1600])
                processed += 1
        return processed

    def run(self) -> None:
        while True:
            self.scan_once()
            time.sleep(self.config.watcher_interval)
