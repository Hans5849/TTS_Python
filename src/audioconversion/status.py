from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from .config import AppConfig
from .database import JobStore
from .gpu import GPUError, discover_gpus, resolve_gpu


def service_state() -> str:
    if not shutil.which("systemctl"):
        return "UNAVAILABLE (systemctl not installed)"
    result = subprocess.run(
        ["systemctl", "is-active", "audioconversion.service"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    )
    state = result.stdout.strip() or "unknown"
    return state.upper()


def gpu_status(config: AppConfig | None = None) -> str:
    try:
        devices = discover_gpus()
        if not devices:
            return "No NVIDIA GPU detected (nvidia-smi unavailable or no devices)."
        selected = resolve_gpu(config.tts.local.gpu, devices) if config else None
        rows = []
        for gpu in devices:
            marker = " SELECTED" if selected == gpu else ""
            logical = "hidden" if gpu.cuda_index is None else f"cuda:{gpu.cuda_index}"
            rows.append(f"Physical {gpu.physical_index} {gpu.uuid} {gpu.pci_bus_id} | {logical} | "
                        f"{gpu.name} | {gpu.memory_free_mib}/{gpu.memory_total_mib} MiB free{marker}")
        return "\n".join(rows)
    except GPUError as exc:
        return f"GPU status unavailable: {exc}"


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
  Provider:  {getattr(config.tts, config.tts.preferred).provider} / {getattr(config.tts, config.tts.preferred).model or 'default'}
  Files:     {inbox_count(config)} in inbox | {active} active | {counts.get('completed', 0)} completed | {counts.get('failed', 0)} failed
  Queue:     {counts.get('waiting', 0)} waiting | {counts.get('queued', 0)} queued | {counts.get('retry_wait', 0)} retry wait | {counts.get('cancelled', 0)} cancelled"""
