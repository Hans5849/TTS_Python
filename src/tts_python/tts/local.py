from __future__ import annotations
from pathlib import Path
import os
import subprocess
import tempfile
from .base import TTSEngine


class EspeakEngine(TTSEngine):
    def __init__(self, executable: str = "espeak-ng", voice: str = "default"):
        self.executable, self.voice = executable, voice

    def synthesize(self, text: str, output_file: Path) -> None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        is_wav = output_file.suffix.lower() == ".wav"
        temporary = None
        if is_wav:
            wav_path = output_file
        else:
            handle, name = tempfile.mkstemp(prefix=".espeak-", suffix=".wav", dir=output_file.parent)
            os.close(handle)
            temporary = wav_path = Path(name)
        command = [self.executable, "-w", str(wav_path)]
        if self.voice != "default":
            command += ["-v", self.voice]
        try:
            subprocess.run(command, input=text, text=True, check=True)
            if not is_wav:
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i",
                                str(wav_path), str(output_file)], check=True)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
