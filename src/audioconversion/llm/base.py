from __future__ import annotations
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    is_cloud = False

    @abstractmethod
    def process(self, text: str) -> str: ...

    def health(self) -> str:
        return "configured"


PRESERVATION_PROMPT = """Convert the supplied technical text into speech-ready wording. Preserve every fact, qualification, and ordering. Do not summarize, omit, correct, or invent content. Return only the converted text.\n\nTEXT:\n"""
