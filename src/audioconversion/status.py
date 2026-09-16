from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from .config import AppConfig
from .database import JobStore


def service_state() -> str:
    if not shutil.which("systemctl"):
        return "UNAVAILABLE (systemctl not installed)"
    result = subprocess.run(
        ["systemctl", "is-active", "audioconversion.service"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    )
    state = result.stdout.strip() or "unknown"
    return state.upper()


def gpu_status() -> str:
    if not shutil.which("nvidia-smi"):
        return "No NVIDIA GPU detected (nvidia-smi unavailable)."
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
         "--format=csv,noheader,nounits"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if result.returncode:
        return f"GPU status unavailable: {result.stderr.strip()}"
    rows = []
    for line in result.stdout.splitlines():
        index, name, usage, used, total = (part.strip() for part in line.split(",", 4))
        rows.append(f"GPU {index}: {name} | {usage}% | {used}/{total} MiB")
    return "\n".join(rows) or "No NVIDIA GPU detected."


def inbox_count(config: AppConfig) -> int:
    return sum(1 for policy in ("normal", "private", "cloud")
               for path in (config.paths.inbox / policy).glob("*") if path.is_file())


def recent_logs(config: AppConfig, lines: int = 30) -> str:
    log = config.paths.logs / "audioconversion.log"
    if not log.exists():
        return f"No application log exists at {log}."
    return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def dashboard(config: AppConfig, store: JobStore) -> str:
    counts = store.counts()
    active = counts.get("processing", 0)
    return f"""Audio Conversion
================================================
Program:   {Path(__file__).resolve().parents[2]}
Inbox:     {config.paths.inbox}
Outbox:    {config.paths.outbox}
Processed: {config.paths.processed}
Failed:    {config.paths.failed}
Service:   {service_state()}
Mode:      {config.processing_mode} | LLM: {config.llm.preferred} | TTS: {config.tts.preferred}
Files:     {inbox_count(config)} in inbox | {active} active | {counts.get('completed', 0)} completed | {counts.get('failed', 0)} failed"""
