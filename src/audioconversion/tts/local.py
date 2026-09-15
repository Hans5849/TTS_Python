from __future__ import annotations
from pathlib import Path
import subprocess
from .base import TTSEngine


class EspeakEngine(TTSEngine):
    def __init__(self, executable: str = "espeak-ng", voice: str = "default"):
        self.executable, self.voice = executable, voice

    def synthesize(self, text: str, output_file: Path) -> None:
        if output_file.suffix.lower() != ".wav":
            raise ValueError("espeak-ng local synthesis supports WAV output")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        command = [self.executable, "-w", str(output_file)]
        if self.voice != "default":
            command += ["-v", self.voice]
        subprocess.run(command, input=text, text=True, check=True)
