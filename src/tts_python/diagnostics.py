from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess

from speech_common.credentials import openai_key, CredentialError
from .config import AppConfig
from .gpu import GPUError, discover_gpus, resolve_gpu


GPU_PROVIDERS = {"cuda", "coqui-cuda", "piper-cuda"}


@dataclass(frozen=True)
class Check:
    ok: bool
    message: str


def run_diagnostics(config: AppConfig) -> list[Check]:
    checks = [Check(True, "configuration is valid")]
    local = config.tts.local
    cloud = config.tts.cloud
    if local.provider in {"espeak", "espeak-ng"}:
        executable = shutil.which("espeak-ng")
        if not executable:
            checks.append(Check(False, "espeak-ng not found; install the espeak-ng package"))
        else:
            result = subprocess.run([executable, "--version"], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, check=False)
            checks.append(Check(result.returncode == 0, f"espeak-ng executable: {executable}"))
    if config.output_format != "wav":
        ffmpeg = shutil.which("ffmpeg")
        checks.append(Check(bool(ffmpeg), f"FFmpeg: {ffmpeg or 'missing; install ffmpeg'}"))
    if cloud.provider == "openai":
        checks.append(Check(bool(cloud.model), "OpenAI model is configured" if cloud.model else "OpenAI model is missing"))
        try:
            present = bool(openai_key("tts", required=False))
            checks.append(Check(present, "OpenAI credential is available" if present else
                                "OpenAI credential is unavailable in this process; check service credentials or OPENAI_API_KEY_FILE"))
        except CredentialError as exc:
            checks.append(Check(False, str(exc)))
    if local.provider in GPU_PROVIDERS:
        try:
            devices = discover_gpus()
            selected = resolve_gpu(local.gpu, devices)
            checks.append(Check(True, f"GPU physical {selected.physical_index} {selected.uuid}; "
                                      f"CUDA logical cuda:{selected.cuda_index}; {selected.name}"))
        except GPUError as exc:
            checks.append(Check(False, str(exc)))
    else:
        checks.append(Check(True, f"local provider {local.provider or 'none'} does not require NVIDIA GPU discovery"))
    return checks
