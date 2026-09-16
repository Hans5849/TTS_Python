from __future__ import annotations

from dataclasses import dataclass, replace
import os
import shutil
import subprocess


class GPUError(RuntimeError):
    pass


@dataclass(frozen=True)
class GPUDevice:
    physical_index: int
    uuid: str
    pci_bus_id: str
    name: str
    memory_total_mib: int
    memory_free_mib: int
    cuda_index: int | None = None


def discover_gpus() -> list[GPUDevice]:
    if not shutil.which("nvidia-smi"):
        return []
    result = subprocess.run([
        "nvidia-smi", "--query-gpu=index,uuid,pci.bus_id,name,memory.total,memory.free",
        "--format=csv,noheader,nounits",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode:
        raise GPUError(f"nvidia-smi failed: {result.stderr.strip() or 'unknown error'}")
    devices = []
    for line in result.stdout.splitlines():
        fields = [value.strip() for value in line.split(",", 5)]
        if len(fields) != 6:
            raise GPUError(f"unexpected nvidia-smi output: {line}")
        try:
            devices.append(GPUDevice(int(fields[0]), fields[1], fields[2], fields[3],
                                     int(fields[4]), int(fields[5])))
        except ValueError as exc:
            raise GPUError(f"invalid nvidia-smi numeric value: {line}") from exc
    return _apply_cuda_visibility(devices, os.getenv("CUDA_VISIBLE_DEVICES"))


def _apply_cuda_visibility(devices: list[GPUDevice], visible: str | None) -> list[GPUDevice]:
    if visible is None:
        return [replace(gpu, cuda_index=gpu.physical_index) for gpu in devices]
    tokens = [token.strip() for token in visible.split(",") if token.strip()]
    mapping: dict[int, int] = {}
    for logical, token in enumerate(tokens):
        matches = [gpu for gpu in devices if token in {str(gpu.physical_index), gpu.uuid}
                   or gpu.uuid.startswith(token)]
        if len(matches) == 1:
            mapping[matches[0].physical_index] = logical
    return [replace(gpu, cuda_index=mapping.get(gpu.physical_index)) for gpu in devices]


def resolve_gpu(selection: str | int, devices: list[GPUDevice] | None = None) -> GPUDevice:
    devices = discover_gpus() if devices is None else devices
    visible = [gpu for gpu in devices if gpu.cuda_index is not None]
    if not visible:
        raise GPUError("no process-visible NVIDIA GPU found; check nvidia-smi and CUDA_VISIBLE_DEVICES")
    if selection == "auto":
        return max(visible, key=lambda gpu: gpu.memory_free_mib)
    if isinstance(selection, int) or isinstance(selection, str) and selection.isdigit():
        physical = int(selection)
        matches = [gpu for gpu in devices if gpu.physical_index == physical]
    elif isinstance(selection, str) and selection.startswith("GPU-"):
        matches = [gpu for gpu in devices if gpu.uuid == selection]
    else:
        raise GPUError("gpu must be 'auto', a physical NVIDIA index, or a full GPU UUID")
    if not matches:
        raise GPUError(f"configured physical GPU {selection!r} was not reported by nvidia-smi")
    if matches[0].cuda_index is None:
        raise GPUError(f"physical GPU {selection!r} is hidden by CUDA_VISIBLE_DEVICES")
    return matches[0]
