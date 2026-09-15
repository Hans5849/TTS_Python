from __future__ import annotations
import logging
from .config import load_config
from .database import JobStore
from .factory import build_processor
from .watcher import Watcher


def run(config_path=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(config_path)
    store = JobStore(config.paths.state / "jobs.sqlite3")
    Watcher(config, build_processor(config), store).run()
