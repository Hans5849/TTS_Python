from __future__ import annotations
from .config import AppConfig
from .llm.local import OllamaProvider
from .processor import Processor
from .tts.cloud import OpenAIEngine
from .tts.local import EspeakEngine


def build_processor(config: AppConfig) -> Processor:
    llms = {}
    if config.llm.local.provider == "ollama":
        llms["local"] = OllamaProvider(config.llm.local.endpoint or "http://127.0.0.1:11434",
                                        config.llm.local.model)
    engines = {}
    if config.tts.local.provider in {"espeak", "espeak-ng"}:
        engines["local"] = EspeakEngine(voice=config.tts.local.voice)
    if config.tts.cloud.provider == "openai":
        engines["cloud"] = OpenAIEngine(config.tts.cloud.model, config.tts.cloud.voice)
    return Processor(config, llms, engines)
