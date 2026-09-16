from __future__ import annotations
from pathlib import Path
from speech_common.credentials import openai_client
from .base import TTSEngine


class OpenAIEngine(TTSEngine):
    """Lazy per-process client; no credentials are required for private/local work."""
    is_cloud = True

    def __init__(self, model: str, voice: str):
        self.model, self.voice, self._client = model, voice, None

    def synthesize(self, text: str, output_file: Path) -> None:
        if self._client is None:
            self._client = openai_client("tts")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with self._client.audio.speech.with_streaming_response.create(
            model=self.model, voice=self.voice, input=text,
            response_format=output_file.suffix.lstrip("."),
        ) as response:
            response.stream_to_file(output_file)
