from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path


class TTSEngine(ABC):
    is_cloud = False

    @abstractmethod
    def synthesize(self, text: str, output_file: Path) -> None: ...

    def health(self) -> str:
        return "configured"
