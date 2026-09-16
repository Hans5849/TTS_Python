from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import shutil

from .config import ConfigError, default_config_path, load_config
from .database import JobStore
from .factory import build_processor
from .service import run
from .status import dashboard, gpu_status, recent_logs
from .diagnostics import run_diagnostics


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tts", description="Convert documents through the shared Audioconversion pipeline")
    root.add_argument("--config", type=Path)
    commands = root.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("convert")
    convert.add_argument("files", type=Path, nargs="+")
    convert.add_argument("--private", action="store_true")
    commands.add_parser("status")
    commands.add_parser("dashboard")
    commands.add_parser("queue")
    jobs = commands.add_parser("jobs")
    jobs.add_argument("state", nargs="?", choices=["waiting", "queued", "processing", "retry_wait",
                                                       "completed", "failed", "cancelled"])
    config = commands.add_parser("config")
    config.add_argument("action", choices=["show", "path", "validate"])
    for name in ("engines", "voices", "models", "logs", "errors", "gpu", "doctor"):
        commands.add_parser(name)
    service = commands.add_parser("service")
    service.add_argument("action", choices=["run", "status", "start", "stop", "restart"])
    retry = commands.add_parser("retry")
    retry.add_argument("job_or_source", nargs="?")
    retry.add_argument("--failed", action="store_true")
    reset = commands.add_parser("reset")
    reset.add_argument("--completed", action="store_true")
    return root


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _retry(store: JobStore, config, job_or_source: str) -> Path:
    row = store.find(job_or_source)
    if row is None or row["status"] != "failed":
        raise ValueError(f"failed job not found: {job_or_source}")
    failed_path = config.paths.failed / row["policy"] / Path(row["source"]).name
    if not failed_path.is_file():
        raise ValueError(f"failed source is no longer present: {failed_path}")
    destination = config.paths.inbox / row["policy"] / failed_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f"inbox destination already exists: {destination}")
    shutil.move(failed_path, destination)
    store.update(row["id"], "cancelled", error="requeued by operator")
    return destination


def _reset(store: JobStore, config) -> int:
    removed = store.reset_terminal()
    for log in config.paths.logs.glob("audioconversion.log*"):
        if log.is_file():
            if log.name == "audioconversion.log":
                log.write_text("", encoding="utf-8")
            else:
                log.unlink()
    return removed


def _interactive_dashboard(config, store: JobStore) -> None:
    while True:
        print("\033[2J\033[H", end="")
        print(dashboard(config, store))
        print("\n[1] Service status  [2] Recent logs  [3] GPU status")
        print("[4] Retry failed file  [5] Refresh  [6] Reset completed/failed")
        print("[q] Quit")
        choice = input("\nChoice: ").strip().lower()
        if choice == "q":
            return
        if choice == "1":
            subprocess.run(["systemctl", "status", "audioconversion.service", "--no-pager"], check=False)
        elif choice == "2":
            print("\n" + recent_logs(config))
        elif choice == "3":
            print("\n" + gpu_status(config))
        elif choice == "4":
            failed = store.failed()
            if not failed:
                print("\nNo failed files are available to retry.")
            else:
                for index, row in enumerate(failed, 1):
                    print(f"{index}. {Path(row['source']).name}: {row['error'] or 'unknown error'}")
                selection = input("File number (blank to cancel): ").strip()
                if selection:
                    try:
                        print(f"Queued: {_retry(store, config, failed[int(selection) - 1]['id'])}")
                    except (ValueError, IndexError) as exc:
                        print(f"Unable to retry: {exc}")
        elif choice == "6":
            answer = input("Clear completed/failed history and application logs? [y/N] ").strip().lower()
            if answer == "y":
                print(f"Cleared {_reset(store, config)} job records. Source and audio files were not deleted.")
        if choice not in {"5"}:
            input("\nPress Enter to continue...")


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
        elif args.command in {"status", "dashboard"}:
            print(dashboard(config, store))
            if args.command == "dashboard" and sys.stdin.isatty() and sys.stdout.isatty():
                _interactive_dashboard(config, store)
        elif args.command in {"queue", "jobs"}:
            state = getattr(args, "state", None)
            for job in store.list(status=state):
                print(f"{job['id']}  {job['status']:<10} tries={job['attempts']} "
                      f"{job['policy']:<7} {job['source']} {job['error'] or ''}")
        elif args.command in {"engines", "voices", "models"}:
            print(json.dumps(_jsonable({"llm": asdict(config.llm), "tts": asdict(config.tts)}), indent=2))
        elif args.command == "logs":
            print(recent_logs(config))
        elif args.command == "errors":
            for row in store.failed():
                print(f"{row['id']} {row['updated_at']} {row['source']}\n  {row['error'] or 'unknown error'}")
        elif args.command == "retry":
            if args.failed:
                failures = list(store.failed())
                for row in failures:
                    print(f"Queued: {_retry(store, config, row['id'])}")
                print(f"Requeued {len(failures)} failed job(s).")
            elif args.job_or_source:
                print(f"Queued: {_retry(store, config, args.job_or_source)}")
            else:
                raise ValueError("provide a job/source or use --failed")
        elif args.command == "reset":
            if args.completed:
                print(f"Cleared {store.reset({'completed'})} completed job record(s).")
            else:
                print(f"Cleared {_reset(store, config)} completed/failed job records and application logs.")
        elif args.command == "gpu":
            print(gpu_status(config))
        elif args.command == "doctor":
            checks = run_diagnostics(config)
            for check in checks:
                print(f"{'PASS' if check.ok else 'FAIL'}  {check.message}")
            return 0 if all(check.ok for check in checks) else 1
        elif args.command == "service":
            if args.action == "run":
                run(path)
            else:
                if not shutil.which("systemctl") or not Path("/run/systemd/system").is_dir():
                    raise RuntimeError(
                        "systemd is not running; on WSL enable systemd in /etc/wsl.conf, "
                        "or use 'tts service run' in a long-lived terminal"
                    )
                result = subprocess.run(["systemctl", args.action, "audioconversion.service"], check=False)
                return result.returncode
        return 0
    except (ConfigError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"tts: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
