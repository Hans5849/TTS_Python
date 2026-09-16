from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from audioconversion.cli import main
from audioconversion.config import load_config
from audioconversion.database import JobStore, SourceIdentity
from audioconversion.diagnostics import run_diagnostics
from audioconversion.gpu import GPUDevice, GPUError, _apply_cuda_visibility, discover_gpus, resolve_gpu
from audioconversion.lifecycle import LifecycleManager, ManagedTTSEngine
from audioconversion.stability import StabilityTracker
from audioconversion.watcher import Watcher


def write_config(root: Path, *, retries=2, checks=1, interval=0) -> Path:
    config = root / "config.toml"
    config.write_text(f'''[paths]
inbox="{root}/inbox"
outbox="{root}/outbox"
processed="{root}/processed"
archive="{root}/archive"
failed="{root}/failed"
state="{root}/state"
logs="{root}/logs"
[processing]
mode="local"
max_retries={retries}
retry_initial_seconds=2
retry_max_seconds=3
[llm]
preferred="local"
fallback="none"
[tts]
preferred="local"
fallback="none"
[tts.local]
provider="espeak-ng"
idle_timeout_seconds=10
gpu="auto"
[output]
format="wav"
[watcher]
poll_interval_seconds=1
stability_interval_seconds={interval}
stability_checks={checks}
''')
    return config


@dataclass
class Result:
    audio: Path
    digest: str = "a" * 64


class Processor:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = 0

    def convert(self, source, private=False):
        self.calls += 1
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        return Result(source.with_suffix(".wav"))


def test_stability_requires_repeated_unchanged_observations(tmp_path):
    source = tmp_path / "copy.txt"
    source.write_text("partial")
    tracker = StabilityTracker(3, 2)
    assert not tracker.observe(source, 0)
    assert not tracker.observe(source, 1)  # interval not reached
    source.write_text("partial plus more")
    assert not tracker.observe(source, 2)  # signature changed; counter reset
    assert not tracker.observe(source, 4)
    assert tracker.observe(source, 6)


def test_partial_copy_not_queued_until_stable(tmp_path):
    config = load_config(write_config(tmp_path, checks=2, interval=1))
    source = config.paths.inbox / "normal" / "copy.txt"
    source.parent.mkdir(parents=True)
    source.write_text("part")
    now = [0.0]
    store = JobStore(config.paths.state / "jobs.sqlite3")
    watcher = Watcher(config, Processor(), store, clock=lambda: now[0])
    assert watcher.discover() == 0
    source.write_text("part two")
    now[0] = 1
    assert watcher.discover() == 0
    now[0] = 2
    assert watcher.discover() == 1


def test_completed_identity_suppressed_but_modified_source_is_new(tmp_path):
    config = load_config(write_config(tmp_path))
    source = config.paths.inbox / "normal" / "notes.txt"
    source.parent.mkdir(parents=True)
    source.write_text("same")
    identity = SourceIdentity.from_path(source)
    store = JobStore(config.paths.state / "jobs.sqlite3")
    row, _ = store.enqueue(source)
    store.update(row["id"], "completed")
    # Simulate the same version arriving after daemon restart.
    source.unlink()
    source.write_text("same")
    os.utime(source, ns=(identity.mtime_ns, identity.mtime_ns))
    watcher = Watcher(config, Processor(), store)
    assert watcher.discover() == 0
    assert not source.exists()
    source.write_text("changed")
    assert watcher.discover() == 1


def test_interrupted_processing_recovers_to_queue(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    source = tmp_path / "notes.txt"
    source.write_text("hello")
    row, _ = store.enqueue(source)
    store.start_attempt(row["id"])
    assert store.recover_interrupted() == 1
    assert store.get(row["id"])["status"] == "queued"


def test_transient_retry_backoff_then_success(tmp_path):
    config = load_config(write_config(tmp_path, retries=2))
    source = config.paths.inbox / "normal" / "notes.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello")
    now = [10.0]
    processor = Processor([ConnectionError("offline"), TimeoutError("busy"), None])
    store = JobStore(config.paths.state / "jobs.sqlite3")
    watcher = Watcher(config, processor, store, clock=lambda: now[0])
    watcher.scan_once()
    row = store.list()[0]
    assert row["status"] == "retry_wait" and row["attempts"] == 1 and row["next_attempt_at"] == 12
    now[0] = 11
    assert watcher.process_due() == 0
    now[0] = 12
    watcher.process_due()
    row = store.get(row["id"])
    assert row["status"] == "retry_wait" and row["attempts"] == 2 and row["next_attempt_at"] == 15
    now[0] = 15
    watcher.process_due()
    completed = store.get(row["id"])
    assert completed["status"] == "completed"
    assert completed["speech_sha256"] == "a" * 64
    assert completed["completion_time"]
    assert processor.calls == 3


def test_permanent_failure_moves_source_without_retry(tmp_path):
    config = load_config(write_config(tmp_path, retries=5))
    source = config.paths.inbox / "private" / "bad.txt"
    source.parent.mkdir(parents=True)
    source.write_text("bad")
    store = JobStore(config.paths.state / "jobs.sqlite3")
    Watcher(config, Processor([ValueError("malformed input")]), store).scan_once()
    row = store.list()[0]
    assert row["status"] == "failed" and row["attempts"] == 1
    assert (config.paths.failed / "private" / "bad.txt").exists()


def test_transient_source_moves_only_after_final_attempt(tmp_path):
    config = load_config(write_config(tmp_path, retries=1))
    source = config.paths.inbox / "normal" / "busy.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello")
    now = [0.0]
    store = JobStore(config.paths.state / "jobs.sqlite3")
    watcher = Watcher(config, Processor([ConnectionError("down"), ConnectionError("down")]),
                      store, clock=lambda: now[0])
    watcher.scan_once()
    assert source.exists() and not (config.paths.failed / "normal" / source.name).exists()
    now[0] = 2
    watcher.process_due()
    row = store.list()[0]
    assert row["status"] == "failed" and row["attempts"] == 2
    assert not source.exists() and Path(row["source"]).exists()


class ModelEngine:
    is_cloud = False

    def __init__(self):
        self.loads = self.unloads = 0
        self._loaded = False
        self._last_used = None

    @property
    def is_loaded(self): return self._loaded
    @property
    def last_used(self): return self._last_used
    def load(self): self.loads += 1; self._loaded = True
    def unload(self): self.unloads += 1; self._loaded = False
    def synthesize(self, text, output): output.write_bytes(b"audio")


def test_idle_unload_reload_and_zero_timeout(tmp_path):
    now = [0.0]
    engine = ModelEngine()
    managed = ManagedTTSEngine(engine, clock=lambda: now[0])
    managed.synthesize("one", tmp_path / "one.wav")
    assert engine.loads == 1
    now[0] = 11
    assert managed.unload_if_idle(10)
    assert engine.unloads == 1
    managed.synthesize("two", tmp_path / "two.wav")
    assert engine.loads == 2
    now[0] = 1000
    assert not managed.unload_if_idle(0)
    assert engine.is_loaded


def test_lightweight_provider_needs_no_lifecycle(tmp_path):
    class Light:
        is_cloud = False
        def synthesize(self, text, output): output.write_bytes(b"ok")
    light = Light()
    light.synthesize("x", tmp_path / "x.wav")
    assert (tmp_path / "x.wav").read_bytes() == b"ok"


def gpu(index, uuid, free=100, logical=None):
    return GPUDevice(index, uuid, f"0000:{index:02x}:00.0", f"GPU {index}", 1000, free, logical)


def test_gpu_resolution_and_cuda_visible_remapping():
    devices = _apply_cuda_visibility([gpu(0, "GPU-A"), gpu(1, "GPU-B", 900)], "GPU-B,0")
    assert devices[0].cuda_index == 1 and devices[1].cuda_index == 0
    assert resolve_gpu(1, devices).cuda_index == 0
    assert resolve_gpu("GPU-B", devices).physical_index == 1
    assert resolve_gpu("auto", devices).physical_index == 1


def test_gpu_hidden_and_no_gpu_diagnostics():
    devices = _apply_cuda_visibility([gpu(0, "GPU-A"), gpu(1, "GPU-B")], "0")
    with pytest.raises(GPUError, match="hidden"):
        resolve_gpu(1, devices)
    with pytest.raises(GPUError, match="no process-visible"):
        resolve_gpu("auto", [])


def test_discover_gpu_parses_mocked_nvidia_smi(monkeypatch):
    monkeypatch.setattr("audioconversion.gpu.shutil.which", lambda name: "/usr/bin/nvidia-smi")
    result = SimpleNamespace(returncode=0, stderr="", stdout="1, GPU-B, 0000:02:00.0, Test GPU, 8192, 4096\n")
    monkeypatch.setattr("audioconversion.gpu.subprocess.run", lambda *args, **kwargs: result)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    found = discover_gpus()
    assert found[0].physical_index == 1 and found[0].cuda_index == 1


def test_cli_jobs_filter_and_reset_completed(tmp_path, capsys):
    config_path = write_config(tmp_path)
    config = load_config(config_path)
    store = JobStore(config.paths.state / "jobs.sqlite3")
    failed = store.add(tmp_path / "failed.txt")
    completed = store.add(tmp_path / "done.txt")
    store.update(failed, "failed", error="bad")
    store.update(completed, "completed")
    assert main(["--config", str(config_path), "jobs", "failed"]) == 0
    assert failed in capsys.readouterr().out
    assert main(["--config", str(config_path), "reset", "--completed"]) == 0
    assert store.get(completed) is None and store.get(failed) is not None


def test_cli_retries_failed_job_without_sqlite_edits(tmp_path):
    config_path = write_config(tmp_path)
    config = load_config(config_path)
    failed_source = config.paths.failed / "private" / "retry.txt"
    failed_source.parent.mkdir(parents=True)
    failed_source.write_text("retry me")
    store = JobStore(config.paths.state / "jobs.sqlite3")
    job = store.add(tmp_path / "original" / "retry.txt", "private")
    store.update(job, "failed", error="temporary", source_path=failed_source)
    assert main(["--config", str(config_path), "retry", job]) == 0
    assert (config.paths.inbox / "private" / "retry.txt").exists()
    assert store.get(job)["status"] == "cancelled"


def test_doctor_is_provider_aware_and_never_prints_secret(tmp_path, monkeypatch):
    config = load_config(write_config(tmp_path))
    monkeypatch.setattr("audioconversion.diagnostics.shutil.which", lambda name: None)
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    checks = run_diagnostics(config)
    text = "\n".join(check.message for check in checks)
    assert "espeak-ng not found" in text
    assert "super-secret-value" not in text
    assert "does not require NVIDIA GPU" in text
