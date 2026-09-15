from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

from .config import ConfigError, default_config_path, load_config
from .database import JobStore
from .factory import build_processor
from .service import run
from .status import dashboard


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tts", description="Convert documents through the shared Audioconversion pipeline")
    root.add_argument("--config", type=Path)
    commands = root.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("convert")
    convert.add_argument("files", type=Path, nargs="+")
    convert.add_argument("--private", action="store_true")
    commands.add_parser("status")
    commands.add_parser("queue")
    commands.add_parser("jobs")
    config = commands.add_parser("config")
    config.add_argument("action", choices=["show", "path", "validate"])
    for name in ("engines", "voices", "models", "logs", "errors"):
        commands.add_parser(name)
    service = commands.add_parser("service")
    service.add_argument("action", choices=["run", "status", "start", "stop", "restart"])
    return root


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    path = args.config or default_config_path()
    try:
        if args.command == "config" and args.action == "path":
            print(path)
            return 0
        config = load_config(path)
        if args.command == "config":
            if args.action == "show":
                print(json.dumps(_jsonable(asdict(config)), indent=2))
            else:
                print(f"Configuration valid: {path}")
            return 0
        store = JobStore(config.paths.state / "jobs.sqlite3")
        if args.command == "convert":
            processor = build_processor(config)
            for source in args.files:
                result = processor.convert(source, private=args.private)
                print(f"{source} -> {result.audio}\n  speech-ready: {result.speech_ready}")
        elif args.command == "status":
            print(dashboard(config, store))
        elif args.command in {"queue", "jobs"}:
            for job in store.list():
                print(f"{job['id']}  {job['status']:<10} {job['policy']:<7} {job['source']}")
        elif args.command in {"engines", "voices", "models"}:
            print(json.dumps(_jsonable({"llm": asdict(config.llm), "tts": asdict(config.tts)}), indent=2))
        elif args.command in {"logs", "errors"}:
            rows = [row for row in store.list() if args.command == "logs" or row["status"] == "failed"]
            for row in rows:
                print(f"{row['updated_at']} {row['status']} {row['source']} {row['error'] or ''}")
        elif args.command == "service":
            if args.action == "run":
                run(path)
            else:
                result = subprocess.run(["systemctl", args.action, "audioconversion.service"], check=False)
                return result.returncode
        return 0
    except (ConfigError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"tts: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
