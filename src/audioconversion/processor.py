from __future__ import annotations
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import tempfile

from .chunker import chunk_text
from .config import AppConfig
from .normalizer import normalize
from .router import candidates, execute_with_fallback


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    speech_ready: Path
    audio: Path
    digest: str
    provider: str
    model: str


class Processor:
    def __init__(self, config: AppConfig, llms: dict, engines: dict, logger: logging.Logger | None = None):
        self.config, self.llms, self.engines = config, llms, engines
        self.logger = logger or logging.getLogger("audioconversion")

    def convert(self, source: Path, *, private: bool = False) -> ConversionResult:
        source = source.resolve()
        if not source.is_file() or source.suffix.lower() not in {".txt", ".md"}:
            raise ValueError(f"unsupported or missing input: {source}")
        original = source.read_text(encoding="utf-8-sig")
        speech = normalize(original)
        if not speech:
            raise ValueError("input contains no speakable text")
        pieces = chunk_text(speech)
        requires_llm = self.config.processing_mode == "llm" or (
            self.config.processing_mode == "hybrid" and any(piece.semantic_processing for piece in pieces))
        llm_options = (candidates(self.config.llm.preferred, self.config.llm.fallback, self.llms, private)
                       if requires_llm else [])
        rendered = []
        for piece in pieces:
            use_llm = self.config.processing_mode == "llm" or (
                self.config.processing_mode == "hybrid" and piece.semantic_processing)
            rendered.append(execute_with_fallback(llm_options, lambda provider: provider.process(piece.text), self.logger)
                            if use_llm else piece.text)
        speech = "\n\n".join(rendered)
        digest = hashlib.sha256(speech.encode()).hexdigest()
        stem = source.stem
        speech_path = self.config.paths.processed / f"{stem}.tts.txt"
        audio_path = self.config.paths.outbox / f"{stem}.{self.config.output_format}"
        self._atomic_text(speech_path, speech + "\n")
        engines = candidates(self.config.tts.preferred, self.config.tts.fallback, self.engines, private)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{stem}-", suffix=audio_path.suffix, dir=audio_path.parent)
        os.close(fd)
        temp_path = Path(temporary)
        selected_provider = "unknown"
        selected_model = ""
        try:
            def synthesize(engine):
                nonlocal selected_provider, selected_model
                selected_provider = next((name for name, value in self.engines.items() if value is engine),
                                         type(engine).__name__)
                selected_model = str(getattr(engine, "model", getattr(getattr(engine, "engine", None), "model", "")))
                return engine.synthesize(speech, temp_path)

            execute_with_fallback(engines, synthesize, self.logger)
            if not temp_path.is_file() or temp_path.stat().st_size == 0:
                raise RuntimeError("TTS provider produced no audio")
            os.replace(temp_path, audio_path)
        finally:
            temp_path.unlink(missing_ok=True)
        return ConversionResult(source, speech_path, audio_path, digest, selected_provider, selected_model)

    @staticmethod
    def _atomic_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".tts-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
