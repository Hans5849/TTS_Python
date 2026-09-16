from __future__ import annotations
from collections.abc import Callable
from .base import LLMProvider, PRESERVATION_PROMPT


class CloudLLMProvider(LLMProvider):
    is_cloud = True

    def __init__(self, callback: Callable[[str], str]):
        self.callback = callback

    def process(self, text: str) -> str:
        return self.callback(PRESERVATION_PROMPT + text)
