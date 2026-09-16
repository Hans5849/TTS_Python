from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Chunk:
    text: str
    semantic_processing: bool
    reason: str


def needs_semantic_processing(text: str) -> tuple[bool, str]:
    if re.search(r"```|\$\$|\\(?:frac|sum|int|begin)\b", text):
        return True, "technical notation"
    lines = text.splitlines()
    if any(line.count("|") >= 2 for line in lines):
        return True, "table-like formatting"
    return False, "ordinary prose"


def chunk_text(text: str, max_chars: int = 3500) -> list[Chunk]:
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    paragraphs = re.split(r"\n\n+", text)
    chunks: list[str] = []
    current = ""
    current_semantic: bool | None = None
    for paragraph in paragraphs:
        pieces = re.split(r"(?<=[.!?])\s+", paragraph) if len(paragraph) > max_chars else [paragraph]
        for piece in pieces:
            if len(piece) > max_chars:
                raise ValueError("a single sentence exceeds the safe chunk size")
            piece_semantic, _ = needs_semantic_processing(piece)
            candidate = f"{current}\n\n{piece}" if current else piece
            # Do not let one formula/table cause adjacent ordinary prose to be sent
            # to an LLM in hybrid mode.
            if current and (len(candidate) > max_chars or piece_semantic != current_semantic):
                chunks.append(current)
                current = piece
                current_semantic = piece_semantic
            else:
                current = candidate
                current_semantic = piece_semantic
    if current:
        chunks.append(current)
    return [Chunk(value, *needs_semantic_processing(value)) for value in chunks]
