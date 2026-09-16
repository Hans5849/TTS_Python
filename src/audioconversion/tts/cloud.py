from __future__ import annotations
from pathlib import Path
from .base import TTSEngine


class OpenAIEngine(TTSEngine):
    """Thin cloud adapter; credentials are read by the OpenAI SDK from the environment."""
    is_cloud = True

    def __init__(self, model: str, voice: str):
        self.model, self.voice = model, voice

    def synthesize(self, text: str, output_file: Path) -> None:
        from openai import OpenAI
        output_file.parent.mkdir(parents=True, exist_ok=True)
        response = OpenAI().audio.speech.create(model=self.model, voice=self.voice, input=text,
                                                response_format=output_file.suffix.lstrip("."))
        response.stream_to_file(output_file)
