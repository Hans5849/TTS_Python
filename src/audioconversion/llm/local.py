from __future__ import annotations
import json
from urllib.request import Request, urlopen
from .base import LLMProvider, PRESERVATION_PROMPT


class OllamaProvider(LLMProvider):
    def __init__(self, endpoint: str, model: str, timeout: float = 120):
        self.endpoint, self.model, self.timeout = endpoint.rstrip("/"), model, timeout

    def process(self, text: str) -> str:
        body = json.dumps({"model": self.model, "prompt": PRESERVATION_PROMPT + text, "stream": False}).encode()
        request = Request(f"{self.endpoint}/api/generate", data=body, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            value = json.load(response).get("response", "").strip()
        if not value:
            raise RuntimeError("Ollama returned empty text")
        return value
