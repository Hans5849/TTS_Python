from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
import signal
import threading
from speech_common.coordination import FileLock
from speech_common.credentials import RedactingFormatter
from .config import load_config
from .database import JobStore
from .factory import build_runtime
from .watcher import Watcher


def run(config_path=None) -> None:
    config = load_config(config_path, required=True)
    # A second worker must not recover jobs owned by the live worker.
    with FileLock(config.paths.state / "worker.lock"):
        config.paths.logs.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(config.paths.logs / "tts-python.log", maxBytes=5_000_000,
                                      backupCount=3, encoding="utf-8")
        console = logging.StreamHandler()
        formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        for output in (handler, console):
            output.setFormatter(formatter)
        logging.basicConfig(level=logging.INFO, handlers=[handler, console], force=True)
        store = JobStore(config.paths.state / "jobs.sqlite3")
        store.recover_interrupted()
        processor, lifecycle = build_runtime(config)
        watcher = Watcher(config, processor, store)
        old_handlers = {}
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                old_handlers[signum] = signal.signal(signum, lambda *_: watcher.stop.set())
        try:
            watcher.run(idle_callback=lifecycle.unload_idle)
        finally:
            lifecycle.shutdown()
            for signum, previous in old_handlers.items():
                signal.signal(signum, previous)
            handler.close()
