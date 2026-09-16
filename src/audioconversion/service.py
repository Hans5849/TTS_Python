from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from .config import load_config
from .database import JobStore
from .factory import build_runtime
from .watcher import Watcher


def run(config_path=None) -> None:
    config = load_config(config_path)
    config.paths.logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(config.paths.logs / "audioconversion.log", maxBytes=5_000_000,
                                  backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[handler, logging.StreamHandler()])
    store = JobStore(config.paths.state / "jobs.sqlite3")
    recovered = store.recover_interrupted()
    if recovered:
        logging.getLogger("audioconversion.service").warning("recovered %d interrupted job(s)", recovered)
    processor, lifecycle = build_runtime(config)
    try:
        Watcher(config, processor, store).run(idle_callback=lifecycle.unload_idle)
    finally:
        lifecycle.shutdown()
