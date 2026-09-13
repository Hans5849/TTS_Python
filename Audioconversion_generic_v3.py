#!/usr/bin/env python3
"""Batch text-to-speech for lecture notes. Python 3.10+.

Default: preserve text, detect section headings, generate lossless WAV chunks,
normalize the assembled recording, then encode each document to its own MP3.
There is deliberately no playback-speed or TTS-speed option.

Install in a virtual environment: python -m pip install openai tiktoken
System requirements: ffmpeg (with libmp3lame) and ffprobe on PATH.
Run --help or consult README.md. --dry-run never calls the speech API.
"""
from __future__ import annotations

import argparse
import codecs
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import difflib
import glob
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import subprocess
import struct
import sys
import tempfile
import time
from typing import Any, Callable, Iterator
import uuid
import wave

VERSION = "3.0.0"
SCHEMA = 3
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2
DEFAULT_MODEL = "gpt-4o-mini-tts-2025-12-15"
DEFAULT_INSTRUCTIONS = (
    "Narrate only the supplied text as a clear, natural educational lecture. "
    "Preserve every statement, qualification, variable, equation wording, and source note; "
    "do not summarize, correct, expand, or add introductions or conclusions. "
    "Use a consistent conversational voice. Read uppercase headings normally, not as shouting. "
    "Pause naturally after headings and between topics. Distinguish variables and initialisms "
    "clearly. Keep mathematical expressions together and follow their written wording."
)
ALL_VOICES = {
    "alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage",
    "shimmer", "verse", "marin", "cedar",
}
LEGACY_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
SETTINGS = {
    "model", "voice", "instructions", "max_chars", "max_tokens", "token_counter",
    "headings", "markdown_headings", "pronunciations", "chapters", "normalize",
    "loudness", "true_peak", "lra", "bitrate", "section_pause", "attempts",
    "read_timeout", "request_deadline", "process_timeout", "retry_budget", "rpm",
    "encoding",
}
DOTENV_KEYS = {"OPENAI_API_KEY", "OPENAI_BASE_URL"}


class TTSError(Exception):
    """Actionable user-facing failure."""


class FatalAPIError(TTSError):
    """Account-wide failure: do not continue making paid requests."""


class InputTooLong(TTSError):
    """The service rejected a request size; subdivide rather than retry it."""


class AudioError(TTSError):
    pass


def load_dotenv(script_dir: Path) -> None:
    """Load supported credentials from .env without evaluating shell syntax.

    The working-directory file has precedence over the script-directory file,
    while variables already present in the process environment have precedence
    over both.
    """
    candidates = [Path.cwd() / ".env", script_dir / ".env"]
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            lines = candidate.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            raise TTSError(f"Cannot read environment file {candidate}: {exc}") from exc
        for line_number, raw in enumerate(lines, 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.fullmatch(
                r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", stripped
            )
            if not match:
                if any(key in stripped for key in DOTENV_KEYS):
                    raise TTSError(f"Malformed supported entry in {candidate}:{line_number}; use NAME=value.")
                continue
            key, value = match.groups()
            if key not in DOTENV_KEYS:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            elif value.startswith(("\"", "'")) or value.endswith(("\"", "'")):
                raise TTSError(f"Malformed quoted {key} in {candidate}:{line_number}.")
            if not value:
                raise TTSError(f"{key} is empty in {candidate}:{line_number}; provide a value or remove the entry.")
            os.environ.setdefault(key, value)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def content_key(text: str) -> str:
    """Whitespace-insensitive check used ONLY for loss/duplication validation."""
    return re.sub(r"\s+", "", text)


def safe_error(exc: BaseException) -> str:
    message = str(exc).strip() or type(exc).__name__
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        message = message.replace(key, "[REDACTED]")
    message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", message)
    return message[:1600]


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tts-", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path: Path, data: Any) -> None:
    atomic_text(path, json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read_object(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """OS-backed nonblocking lock; released even if this process dies.

    Keep the tiny lock file. Unlinking a locked inode would make locking unsafe.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+b")
    locked = False
    try:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise TTSError(f"Another process is using this job/output: {path}") from exc
        yield
    finally:
        if locked:
            f.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def run_process(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"{Path(command[0]).name} exceeded {timeout:g} seconds.") from exc
    except FileNotFoundError as exc:
        raise TTSError(f"Required program not found: {command[0]}") from exc
    if result.returncode:
        raise AudioError(f"{Path(command[0]).name} failed: {result.stderr.strip()[-2500:]}")
    return result


def ffmpeg_base(verbose: bool = False) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel",
            "info" if verbose else "error", "-nostats", "-y"]


def probe_audio(path: Path, timeout: float, full_decode: bool = False) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise AudioError(f"Missing or empty audio: {path.name}")
    result = run_process([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)
    ], timeout)
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid duration")
        info = {"duration": duration, "codec": stream["codec_name"],
                "sample_rate": int(stream["sample_rate"]), "channels": int(stream["channels"]),
                "bytes": path.stat().st_size}
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AudioError(f"Audio is not probeable: {path.name}") from exc
    if full_decode:
        run_process(ffmpeg_base() + ["-xerror", "-i", str(path), "-map", "0:a:0",
                                     "-f", "null", "-"], timeout)
    return info


def repair_streaming_wav(path: Path) -> bool:
    """Repair known unknown-length RIFF/data sentinels after a complete download.

    Do not globally use FFmpeg ignore_length: that can misread trailing metadata
    as audio. Conventional finite lengths that exceed the file are truncation
    errors, NOT silently repaired. A successful HTTP transfer still cannot prove
    that a generative model spoke every requested word.
    """
    size = path.stat().st_size
    placeholders = {0xffffffff, 0x7fffffff}
    with path.open("r+b") as f:
        head = f.read(12)
        if len(head) != 12 or head[:4] != b"RIFF" or head[8:] != b"WAVE":
            raise AudioError("Speech response is not a RIFF WAVE file.")
        riff_size = struct.unpack("<I", head[4:8])[0]
        if riff_size not in placeholders | {0} and riff_size + 8 > size:
            raise AudioError("WAV is truncated according to its finite RIFF length.")
        offset = 12
        alignment = None
        while offset + 8 <= size:
            f.seek(offset)
            header = f.read(8)
            tag, declared = header[:4], struct.unpack("<I", header[4:])[0]
            if tag == b"fmt ":
                fmt = f.read(min(declared, 40))
                if len(fmt) < 16:
                    raise AudioError("WAV format header is incomplete.")
                alignment = struct.unpack("<H", fmt[12:14])[0]
            if tag == b"data":
                available = size - (offset + 8)
                unknown = declared in placeholders or (declared == 0 and riff_size in placeholders | {0})
                if unknown:
                    if not alignment or available <= 0 or available % alignment:
                        raise AudioError("Streaming WAV does not contain a whole number of sample frames.")
                    if size - 8 > 0xffffffff:
                        raise AudioError("Streaming WAV exceeds RIFF's 4 GiB limit.")
                    f.seek(offset + 4)
                    f.write(struct.pack("<I", available))
                    f.seek(4)
                    f.write(struct.pack("<I", size - 8))
                    f.flush()
                    os.fsync(f.fileno())
                    return True
                if declared > available or declared == 0:
                    raise AudioError("WAV data is empty or truncated.")
                if riff_size in placeholders | {0}:
                    f.seek(4)
                    f.write(struct.pack("<I", size - 8))
                    return True
                return False
            if declared > size - offset - 8:
                raise AudioError("WAV chunk length exceeds the downloaded file.")
            offset += 8 + declared + (declared % 2)
    raise AudioError("WAV response contains no data chunk.")


def wav_frames(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                    audio.getcomptype()) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE, "NONE"):
                raise AudioError(f"Noncanonical cached WAV: {path.name}")
            frames = audio.getnframes()
            if frames < 1:
                raise AudioError(f"Empty WAV: {path.name}")
            return frames
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"Invalid WAV: {path.name}") from exc


class TokenBudget:
    """Conservative local budget, not an API billing-token measurement.

    o200k_base is the GPT-4o-family tokenizer. Include instructions and a 128-token
    reserve. Without tiktoken, UTF-8 byte counts are used conservatively instead;
    this intentionally produces smaller chunks. No chars/4 estimate is used.
    """
    def __init__(self, args: argparse.Namespace):
        self.max_chars = args.max_chars
        self.max_tokens = args.max_tokens
        self.instructions = args.instructions if args.model not in {"tts-1", "tts-1-hd"} else ""
        self.encoding = None
        self.warning = ""
        if args.token_counter != "bytes":
            try:
                import tiktoken
                self.encoding = tiktoken.get_encoding("o200k_base")
            except Exception as exc:
                if args.token_counter == "tiktoken":
                    raise TTSError("Cannot load tiktoken/o200k_base. Install tiktoken; its first "
                                   "use may need internet to download tokenizer data.") from exc
                self.warning = ("tiktoken unavailable; using conservative UTF-8 byte budgets. "
                                "Install tiktoken for fewer requests. The chunk plan may change.")
        self.name = "o200k_base estimate" if self.encoding else "UTF-8 byte upper estimate"
        self.overhead = self.count(self.instructions) + 128
        if self.max_tokens - self.overhead < 128:
            raise TTSError("Instructions leave too little input budget. Shorten --instructions "
                           "or install tiktoken. --max-tokens includes instructions + reserve.")

    def count(self, text: str) -> int:
        if self.encoding:
            return len(self.encoding.encode(text, disallowed_special=()))
        return len(text.encode("utf-8"))

    def total(self, text: str) -> int:
        return self.count(text) + self.overhead

    def fits(self, text: str, char_limit: int | None = None) -> bool:
        return (len(text) <= min(self.max_chars, char_limit or self.max_chars)
                and self.total(text) <= self.max_tokens)


@dataclass
class Section:
    index: int
    title: str
    text: str


@dataclass
class Chunk:
    text: str
    sections: list[int]
    titles: list[str]
    starts_section: bool = True


@dataclass
class Plan:
    source: Path
    source_hash: str
    original: str
    prepared: str
    title: str
    sections: list[Section]
    chunks: list[Chunk]
    changes: dict[str, int]
    warnings: list[str]
    plan_id: str


@dataclass
class Paths:
    work: Path
    final: Path
    chapters: Path


@dataclass
class AudioPart:
    path: Path
    frames: int
    audio_hash: str
    request_hash: str


@dataclass
class Stats:
    generated: int = 0
    cached: int = 0
    requests: int = 0
    adaptive_splits: int = 0


def normalize_source(text: str) -> str:
    # Never strip mathematical/list operators or rewrite technical expressions.
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()


def strip_heading_markers(text: str) -> str:
    """Explicit opt-in: remove only ATX heading markers, outside protected blocks."""
    out = []
    fence: str | None = None
    math_block = False
    for line in text.split("\n"):
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            c = marker.group(1)[0]
            fence = None if fence == c else c if fence is None else fence
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        if stripped in {"$$", r"\[", r"\]"}:
            math_block = not math_block
        if not math_block:
            line = re.sub(r"^ {0,3}#{1,6}\s+(?=\S)", "", line)
        out.append(line)
    return "\n".join(out)


def load_pronunciations(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    try:
        obj = json.loads(Path(path).expanduser().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise TTSError(f"Cannot read pronunciation dictionary: {path}") from exc
    if not isinstance(obj, dict):
        raise TTSError("Pronunciations must be a JSON object of string: string pairs.")
    for key, value in obj.items():
        if (not isinstance(key, str) or not isinstance(value, str) or not key.strip()
                or not value.strip() or "\n" in key or "\n" in value):
            raise TTSError("Pronunciation keys/values must be nonempty single-line strings.")
    return obj


def apply_pronunciations(text: str, rules: dict[str, str]) -> tuple[str, dict[str, int]]:
    """Whole-token, case-sensitive, longest-first, non-cascading substitution."""
    if not rules:
        return text, {}
    patterns = []
    for key in sorted(rules, key=len, reverse=True):
        left = r"(?<!\w)" if re.match(r"\w", key[0]) else ""
        right = r"(?!\w)" if re.match(r"\w", key[-1]) else ""
        patterns.append(left + re.escape(key) + right)
    pattern = re.compile("|".join(patterns))
    counts: dict[str, int] = {}

    def replacement(match: re.Match[str]) -> str:
        key = match.group()
        counts[key] = counts.get(key, 0) + 1
        return rules[key]

    return pattern.sub(replacement, text), counts


def heading_title(line: str) -> str | None:
    stripped = line.strip()
    explicit = re.match(r"^#{1,6}\s+(.+)$", stripped)
    if explicit:
        return explicit.group(1).strip()
    if not 3 <= len(stripped) <= 180 or len(stripped.split()) > 22:
        return None
    if re.match(r"(?i)^(summary|overview|conclusion|conclusions|references|source notes?|"
                r"epilogue|introduction)(?:\s*[:\-].*)?$", stripped):
        return stripped
    # Operators and sentence-ending punctuation suggest an expression, not a heading.
    if re.search(r"[=<>+*/\\^{}\[\]]", stripped) or stripped.endswith((".", "?", ";", ",")):
        return None
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters):
        return stripped
    return None


def detect_sections(text: str, mode: str) -> list[Section]:
    if mode == "none":
        return [Section(0, "Complete text", text)]
    lines = text.split("\n")
    sections: list[Section] = []
    buffer: list[str] = []
    title = "Introduction"
    fence: str | None = None
    math_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            c = marker.group(1)[0]
            fence = None if fence == c else c if fence is None else fence
        if not fence and stripped in {"$$", r"\[", r"\]"}:
            math_block = not math_block
        isolated = (i == 0 or not lines[i - 1].strip()) and (i + 1 == len(lines) or not lines[i + 1].strip())
        explicit = re.match(r"^#{1,6}\s+", stripped) is not None
        h = heading_title(line) if not fence and not math_block and (isolated or explicit) else None
        if h:
            previous = "\n".join(buffer).strip()
            if previous:
                sections.append(Section(len(sections), title, previous))
            buffer = [line]
            title = h
        else:
            buffer.append(line)
    last = "\n".join(buffer).strip()
    if last:
        sections.append(Section(len(sections), title, last))
    return sections


def split_naturally(text: str, budget: TokenBudget, char_limit: int | None = None) -> list[str]:
    """Split at natural boundaries; never split a non-whitespace token.

    An extra request is preferable to cutting a variable, word, URL, or formula.
    Oversized unbroken tokens are rejected instead of silently being changed.
    """
    remaining = text.strip()
    pieces: list[str] = []
    while remaining:
        if budget.fits(remaining, char_limit):
            pieces.append(remaining)
            break
        cap = min(len(remaining), budget.max_chars, char_limit or budget.max_chars)
        window = remaining[:cap + 1]
        cuts: dict[int, int] = {}
        for pattern, strength in [
            (r"\n[ \t]*\n", 4), (r"[.!?][\"')\]]*\s+", 3),
            (r"\n", 2), (r"[,;:]\s+", 1), (r"\s+", 0),
        ]:
            for match in re.finditer(pattern, window):
                end = match.start() if strength in {0, 2, 4} else match.start() + len(match.group().rstrip())
                if 0 < end <= cap:
                    cuts[end] = max(cuts.get(end, -1), strength)
        valid = [(end, quality) for end, quality in cuts.items()
                 if remaining[:end].strip() and budget.fits(remaining[:end].strip(), char_limit)]
        if not valid:
            token = remaining.split(maxsplit=1)[0]
            raise TTSError(f"An unbroken token cannot fit the request budget ({len(token)} characters). "
                           "Review long formulas/URLs or shorten instructions; it was NOT cut in half.")
        furthest = max(end for end, _ in valid)
        useful = [(end, quality) for end, quality in valid if end >= furthest * 0.55]
        # Do not strand an isolated heading at the end of a request.
        def stranded(end: int) -> bool:
            tail = re.split(r"\n\s*\n", remaining[:end].strip())[-1]
            return "\n" not in tail and heading_title(tail) is not None
        safe = [(end, quality) for end, quality in useful if not stranded(end)]
        if safe:
            useful = safe
        end, _ = max(useful, key=lambda item: (item[1], item[0]))
        pieces.append(remaining[:end].strip())
        remaining = remaining[end:].lstrip()
    if content_key("".join(pieces)) != content_key(text):
        raise TTSError("Internal splitter integrity check failed; nothing was sent.")
    return pieces


def make_chunks(sections: list[Section], budget: TokenBudget, chapters: bool) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: Chunk | None = None
    for section in sections:
        pieces = split_naturally(section.text, budget)
        for index, piece in enumerate(pieces):
            can_join = (not chapters and current is not None and len(pieces) == 1
                        and budget.fits(current.text + "\n\n" + piece))
            if can_join:
                current.text += "\n\n" + piece
                current.sections.append(section.index)
                current.titles.append(section.title)
            else:
                if current:
                    chunks.append(current)
                current = Chunk(piece, [section.index], [section.title], index == 0)
            if len(pieces) > 1:
                chunks.append(current)
                current = None
    if current:
        chunks.append(current)
    return chunks


def prepare_plan(source: Path, args: argparse.Namespace, budget: TokenBudget,
                 rules: dict[str, str]) -> Plan:
    try:
        raw = source.read_bytes()
        original = raw.decode(args.encoding)
    except UnicodeError as exc:
        raise TTSError(f"{source.name} is not {args.encoding}; re-save as UTF-8 or use --encoding.") from exc
    normalized = normalize_source(original)
    if not normalized:
        raise TTSError(f"Empty source: {source.name}")
    if "\x00" in normalized:
        raise TTSError(f"NUL bytes found in {source.name}. Check the encoding (for example UTF-16).")
    prepared = strip_heading_markers(normalized) if args.markdown_headings else normalized
    prepared, changes = apply_pronunciations(prepared, rules)
    sections = detect_sections(prepared, args.headings)
    chunks = make_chunks(sections, budget, args.chapters)
    if not chunks or content_key("".join(c.text for c in chunks)) != content_key(prepared):
        raise TTSError("Prepared-text integrity check failed; nothing was sent.")
    warnings = []
    if "\u200b" in prepared or "\ufeff" in prepared:
        warnings.append("Invisible Unicode characters remain in the body; preserved rather than silently deleted.")
    if re.search(r"(?m)^\s*[>+*-]\s|\$\$|\\(?:frac|begin)|[=<>]", prepared):
        warnings.append("Symbolic notation/formatting was preserved. Review pronunciation in the first recording.")
    data = {"schema": SCHEMA, "text": prepared, "chunks": [asdict(c) for c in chunks]}
    return Plan(source, hashlib.sha256(raw).hexdigest(), original, prepared,
                prepared.splitlines()[0].strip()[:240], sections, chunks, changes,
                warnings, digest_json(data))


def path_key(path: Path) -> str:
    return str(path.resolve()).casefold()


def paths_for(source: Path, args: argparse.Namespace) -> Paths:
    base = Path(args.output_dir).expanduser().resolve() if args.output_dir else source.parent
    final = Path(args.output).expanduser().absolute() if args.output else base / f"{source.stem}_FINAL.mp3"
    if final.suffix.lower() != ".mp3":
        raise TTSError("--output must end in .mp3.")
    if final.is_symlink():
        raise TTSError(f"Refusing to replace a symlink output: {final}")
    final = final.resolve()
    return Paths(base / f"{source.stem}_audio_v3", final, final.parent / f"{final.stem}_chapters")


def collision_check(sources: list[Path], layouts: list[Paths], args: argparse.Namespace) -> None:
    used: dict[str, Path] = {}
    source_keys = {path_key(s): s for s in sources}
    for source, paths in zip(sources, layouts):
        for destination in (paths.work, paths.final, paths.chapters):
            if "\n" in str(destination) or "\r" in str(destination):
                raise TTSError("Newlines are not allowed in output paths.")
            key = path_key(destination)
            if key in source_keys:
                raise TTSError(f"Output would overwrite an input: {destination}")
            if key in used:
                raise TTSError(f"Output collision: {source} and {used[key]} both use {destination}. "
                               "Rename the inputs or use separate output directories.")
            used[key] = source
        owner = read_object(paths.work / "owner.json")
        if owner and owner.get("source") != str(source) and not args.adopt_moved_cache:
            raise TTSError(f"{paths.work} belongs to {owner.get('source')}. Use a different output "
                           "directory. For a deliberately moved project only, use --adopt-moved-cache.")
        if paths.work.exists() and not paths.work.is_dir():
            raise TTSError(f"Work directory is a file: {paths.work}")


def discover_files(script_dir: Path, args: argparse.Namespace) -> list[Path]:
    dirs = [Path(args.input_dir).expanduser().resolve()] if args.input_dir else [Path.cwd(), script_dir]
    found: dict[str, Path] = {}
    for directory in dirs:
        if not directory.is_dir():
            raise TTSError(f"Input directory does not exist: {directory}")
        iterator = directory.rglob("*") if args.recursive else directory.iterdir()
        for path in iterator:
            if not path.is_file() or path.suffix.lower() not in {".txt", ".md"}:
                continue
            if path.name.lower().startswith(("readme", "requirements", "test_results")):
                continue
            relative_parents = path.relative_to(directory).parts[:-1]
            if any(p.startswith(".") or p.endswith(("_audio_parts", "_audio_v3", "_chapters"))
                   for p in relative_parents):
                continue
            found[str(path.resolve())] = path.resolve()
    return sorted(found.values(), key=lambda p: (p.name.casefold(), str(p)))


def parse_selection(selection: str, count: int) -> list[int]:
    if selection.strip().lower() in {"all", "a", "*"}:
        return list(range(count))
    output: list[int] = []
    for token in selection.replace(",", " ").split():
        if re.fullmatch(r"\d+", token):
            values = [int(token)]
        elif re.fullmatch(r"\d+-\d+", token):
            a, b = map(int, token.split("-"))
            values = range(a, b + (1 if b >= a else -1), 1 if b >= a else -1)
        else:
            raise TTSError(f"Invalid selection: {token}")
        for value in values:
            if not 1 <= value <= count:
                raise TTSError(f"File number out of range: {value}")
            if value - 1 not in output:
                output.append(value - 1)
    if not output:
        raise TTSError("No files selected.")
    return output


def resolve_inputs(args: argparse.Namespace, script_dir: Path) -> list[Path]:
    names = list(args.input_files)
    if not names:
        files = discover_files(script_dir, args)
        if args.all:
            return files
        if not sys.stdin.isatty():
            raise TTSError("Provide input paths or --all when not running interactively.")
        print("\nAvailable text files:\n")
        for i, path in enumerate(files, 1):
            print(f"  {i:>2}. {path.name}  [{path.parent}]")
        print("\n  A. All files    0. Enter paths    Q. Quit")
        print("  Examples: 1 2 3  |  1-3  |  1,3  |  all")
        while True:
            answer = input("\nChoose file(s): ").strip()
            if answer.lower() in {"q", "quit"}:
                return []
            if answer == "0" or not files:
                entered = input("File paths (quote names containing spaces): ").strip()
                try:
                    names = shlex.split(entered, posix=os.name != "nt")
                    names = [n.strip('"') for n in names]
                except ValueError as exc:
                    print(exc)
                    continue
                if names:
                    break
                continue
            try:
                return [files[i] for i in parse_selection(answer, len(files))]
            except TTSError as exc:
                print(exc)
    resolved: list[Path] = []
    for name in names:
        expanded = str(Path(name).expanduser())
        matches = glob.glob(expanded) if glob.has_magic(expanded) else [expanded]
        if not matches:
            raise TTSError(f"No files match: {name}")
        for match in sorted(matches):
            path = Path(match)
            if not path.exists() and not path.is_absolute() and (script_dir / path).exists():
                path = script_dir / path
            path = path.resolve()
            if not path.is_file():
                raise TTSError(f"Input is not a file: {path}")
            if path.suffix.lower() not in {".txt", ".md"}:
                raise TTSError(f"Only .txt and .md inputs are supported: {path.name}")
            if path not in resolved:
                resolved.append(path)
    return resolved


def print_plan(plan: Plan, budget: TokenBudget, args: argparse.Namespace) -> None:
    print(f"\n{plan.source.name}")
    print(f"  {len(plan.prepared):,} characters | {len(plan.sections)} sections | {len(plan.chunks)} requests planned")
    print(f"  Token check: {budget.name}; includes instructions + 128 reserve")
    for i, c in enumerate(plan.chunks, 1):
        title = " / ".join(c.titles)
        if len(title) > 100:
            title = title[:97] + "..."
        print(f"  [{i:>2}/{len(plan.chunks)}] {len(c.text):>4} chars | budget {budget.total(c.text):>4}/{budget.max_tokens} | {title}")
        if args.show_chunks:
            print(f"\n--- Exact request text {i} ---\n{c.text}\n--- End request text ---")
    if plan.changes:
        print("  Explicit pronunciation replacements: " + json.dumps(plan.changes, ensure_ascii=False))
    for warning in plan.warnings:
        print(f"  NOTE: {warning}")


def export_plan(plan: Plan, paths: Paths, budget: TokenBudget) -> None:
    directory = paths.work / "plans" / plan.plan_id
    atomic_text(directory / "prepared.txt", plan.prepared + "\n")
    changes = "".join(difflib.unified_diff(
        plan.original.splitlines(keepends=True), (plan.prepared + "\n").splitlines(keepends=True),
        fromfile="original", tofile="prepared"))
    atomic_text(directory / "changes.diff", changes or "No textual changes.\n")
    atomic_json(directory / "plan.json", {
        "schema": SCHEMA, "plan_id": plan.plan_id, "source": str(plan.source),
        "source_sha256": plan.source_hash, "title": plan.title,
        "token_counter": budget.name, "pronunciations_applied": plan.changes,
        "sections": [{"index": s.index, "title": s.title} for s in plan.sections],
        "chunks": [dict(asdict(c), estimated_budget=budget.total(c.text)) for c in plan.chunks],
    })
    atomic_json(paths.work / "current_plan.json", {"plan_id": plan.plan_id,
                "directory": f"plans/{plan.plan_id}", "updated": utc_now()})


class Reporter:
    def __init__(self, log: Path | None = None):
        self.log = log

    def say(self, message: str) -> None:
        print(message, flush=True)
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a", encoding="utf-8") as handle:
                handle.write(f"{utc_now()} {message}\n")


class LazyClient:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.client: Any = None

    def get(self) -> Any:
        if self.args.assemble_only:
            raise TTSError("--assemble-only cannot generate missing chunks.")
        if self.client is None:
            if not os.environ.get("OPENAI_API_KEY", "").strip():
                raise FatalAPIError("OPENAI_API_KEY is missing. Set it in this terminal; never put it in the script.")
            try:
                from openai import OpenAI
                import httpx2
            except ImportError as exc:
                raise FatalAPIError("Install the SDK in your virtual environment: python -m pip install openai") from exc
            self.client = OpenAI(max_retries=0, timeout=httpx2.Timeout(
                connect=15.0,
                read=self.args.read_timeout,
                write=30.0,
                pool=30.0,
            ))
        return self.client

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


class RateLimiter:
    def __init__(self, rpm: float):
        self.interval = 60.0 / rpm
        self.last: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self.last is not None:
            delay = self.interval - (now - self.last)
            if delay > 0:
                time.sleep(delay)
        self.last = time.monotonic()


def error_kind(exc: Exception) -> str:
    if isinstance(exc, InputTooLong):
        return "length"
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    message = str(body) if body is not None else str(exc)
    lower = message.lower()
    if status in {401, 403} or (status == 429 and any(x in lower for x in (
            "insufficient_quota", "billing_hard_limit", "exceeded your current quota"))):
        return "fatal"
    if status == 413:
        return "length"
    if status == 400 and any(x in lower for x in (
            "context_length_exceeded", "maximum context", "input too long", "input is too long",
            "maximum length", "max length", "too many tokens", "maximum number of input tokens",
            "string too long", "string_too_long")):
        return "length"
    if status in {408, 409, 429} or isinstance(status, int) and status >= 500:
        return "retry"
    if isinstance(exc, AudioError):
        return "retry"
    # OpenAI APIConnectionError/APITimeoutError; httpx failures during streaming
    # can escape the SDK's request-level exception wrappers.
    if type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
        return "retry"
    try:
        import httpx
        if isinstance(exc, httpx.TransportError):
            return "retry"
    except ImportError:
        pass
    return "fail"


def retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    try:
        if "retry-after-ms" in headers:
            value = float(headers["retry-after-ms"]) / 1000
        elif "retry-after" in headers:
            try:
                value = float(headers["retry-after"])
            except ValueError:
                date = parsedate_to_datetime(headers["retry-after"])
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                value = (date - datetime.now(timezone.utc)).total_seconds()
        else:
            return None
        return max(0.0, value) if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def request_spec(text: str, args: argparse.Namespace) -> dict[str, Any]:
    # Include endpoint identity, but never credentials, in cache identity.
    return {"schema": SCHEMA, "model": args.model, "voice": args.voice, "input": text,
            "instructions": args.instructions if args.model not in {"tts-1", "tts-1-hd"} else "",
            "response_format": "wav", "canonical_audio": [SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH],
            "endpoint": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")}


def valid_cache(cache: Path, key: str, args: argparse.Namespace) -> AudioPart | None:
    meta = read_object(cache / f"{key}.json")
    path = cache / f"{key}.wav"
    if not meta or meta.get("schema") != SCHEMA or meta.get("request_hash") != key:
        return None
    try:
        if not path.is_file() or meta.get("sha256") != sha_file(path):
            return None
        frames = wav_frames(path)
        if frames != meta.get("frames"):
            return None
        info = probe_audio(path, args.process_timeout)
        if info["codec"] != "pcm_s16le":
            return None
        return AudioPart(path, frames, meta["sha256"], key)
    except (OSError, AudioError, ValueError, TypeError):
        return None


def generate_wav(text: str, cache: Path, key: str, args: argparse.Namespace,
                 provider: LazyClient, limiter: RateLimiter, stats: Stats,
                 reporter: Reporter) -> AudioPart:
    cache.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    client = provider.get()
    for attempt in range(1, args.attempts + 1):
        try:
            with tempfile.TemporaryDirectory(prefix=".download-", dir=cache) as temp_dir:
                temp = Path(temp_dir)
                raw = temp / "response.wav"
                fixed = temp / "canonical.wav"
                limiter.wait()
                stats.requests += 1
                api_args: dict[str, Any] = {"model": args.model, "voice": args.voice,
                                            "input": text, "response_format": "wav"}
                if args.model not in {"tts-1", "tts-1-hd"}:
                    api_args["instructions"] = args.instructions
                request_started = time.monotonic()
                request_id = None
                with client.audio.speech.with_streaming_response.create(**api_args) as response:
                    request_id = response.headers.get("x-request-id")
                    with raw.open("wb") as f:
                        for block in response.iter_bytes(chunk_size=65536):
                            if time.monotonic() - request_started > args.request_deadline:
                                import httpx
                                raise httpx.ReadTimeout("Total speech download deadline exceeded.")
                            f.write(block)
                        f.flush()
                        os.fsync(f.fileno())
                # Unknown streaming WAV lengths can otherwise trigger FFmpeg's
                # corruption check even when the HTTP response completed normally.
                repair_streaming_wav(raw)
                # Canonicalize without lossy encoding.
                run_process(ffmpeg_base() + ["-xerror", "-i", str(raw), "-map", "0:a:0",
                            "-vn", "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
                            str(fixed)], args.process_timeout)
                info = probe_audio(fixed, args.process_timeout)
                frames = wav_frames(fixed)
                audio_hash = sha_file(fixed)
                target = cache / f"{key}.wav"
                os.replace(fixed, target)
                atomic_json(cache / f"{key}.json", {
                    "schema": SCHEMA, "request_hash": key, "sha256": audio_hash,
                    "frames": frames, "duration": frames / SAMPLE_RATE,
                    "characters": len(text), "request_id": request_id, "created": utc_now(),
                })
                stats.generated += 1
                word_count = len(text.split())
                wpm = word_count * 60 / info["duration"]
                if word_count >= 50 and (wpm < 55 or wpm > 330):
                    reporter.say(f"    WARNING: unusual duration ({wpm:.0f} words/min). Listen to this part; "
                                 "a valid audio file does not prove complete narration.")
                return AudioPart(target, frames, audio_hash, key)
        except Exception as exc:
            kind = error_kind(exc)
            if kind == "fatal":
                raise FatalAPIError(f"Account/API access problem: {safe_error(exc)}") from exc
            if kind == "length":
                raise InputTooLong(safe_error(exc)) from exc
            if kind != "retry" or attempt == args.attempts:
                raise
            delay = retry_after(exc)
            if delay is None:
                delay = min(2 ** attempt, 30) + random.uniform(0, 1)
            if time.monotonic() - started + delay > args.retry_budget:
                raise TTSError("Retry budget exhausted; completed parts remain cached. "
                               + safe_error(exc)) from exc
            reporter.say(f"    Attempt {attempt}/{args.attempts} failed: {safe_error(exc)[:220]}")
            reporter.say(f"    Retrying in {delay:.1f}s (SDK retries disabled).")
            time.sleep(delay)
    raise TTSError("Unreachable retry state")


def obtain_audio(text: str, cache: Path, args: argparse.Namespace, budget: TokenBudget,
                 provider: LazyClient, limiter: RateLimiter, stats: Stats, reporter: Reporter,
                 seen: dict[str, list[AudioPart]], depth: int = 0) -> list[AudioPart]:
    key = digest_json(request_spec(text, args))
    if key in seen:
        return seen[key]
    # Split recipes preserve resumability after a server-side size rejection.
    recipe_path = cache / f"{key}.split.json"
    recipe = read_object(recipe_path)
    children = recipe.get("children") if recipe and recipe.get("request_hash") == key else None
    valid_recipe = (isinstance(children, list) and len(children) >= 2
                    and all(isinstance(s, str) and 0 < len(s) < len(text) for s in children))
    if valid_recipe and content_key("".join(children)) == content_key(text):
        if depth >= 12:
            raise TTSError("Adaptive split depth exceeded. Reduce the chunk size.")
        parts = []
        for child in children:
            parts.extend(obtain_audio(child, cache, args, budget, provider, limiter, stats,
                                      reporter, seen, depth + 1))
        seen[key] = parts
        return parts
    if not args.restart:
        existing = valid_cache(cache, key, args)
        if existing:
            stats.cached += 1
            seen[key] = [existing]
            return [existing]
    if args.assemble_only:
        raise TTSError(f"Missing or invalid cached chunk {key[:12]}; --assemble-only made no API calls.")
    try:
        part = generate_wav(text, cache, key, args, provider, limiter, stats, reporter)
        seen[key] = [part]
        return [part]
    except InputTooLong:
        if depth >= 12 or len(text.split()) < 2:
            raise TTSError("API still rejects the smallest safe request; check instructions/model limits.")
        children = split_naturally(text, budget, max(1, len(text) // 2))
        if len(children) < 2:
            raise TTSError("Unable to subdivide the rejected request safely.")
        cache.mkdir(parents=True, exist_ok=True)
        atomic_json(recipe_path, {"schema": SCHEMA, "request_hash": key, "children": children})
        stats.adaptive_splits += 1
        reporter.say("    API length limit: subdividing at natural boundaries and saving the new plan.")
        parts = []
        for child in children:
            parts.extend(obtain_audio(child, cache, args, budget, provider, limiter, stats,
                                      reporter, seen, depth + 1))
        seen[key] = parts
        return parts


def copy_wave_frames(source: wave.Wave_read, target: wave.Wave_write, count: int) -> None:
    remaining = count
    while remaining:
        data = source.readframes(min(65536, remaining))
        frames = len(data) // (CHANNELS * SAMPLE_WIDTH)
        if not frames or len(data) % (CHANNELS * SAMPLE_WIDTH):
            raise AudioError("WAV ended before its declared sample count.")
        target.writeframesraw(data)
        remaining -= frames


def configure_wave(target: wave.Wave_write) -> None:
    target.setnchannels(CHANNELS)
    target.setsampwidth(SAMPLE_WIDTH)
    target.setframerate(SAMPLE_RATE)


def assemble_master(plan: Plan, parts: list[list[AudioPart]], output: Path,
                    args: argparse.Namespace) -> list[dict[str, Any]]:
    chapter_ranges: list[dict[str, Any]] = []
    position = 0
    with wave.open(str(output), "wb") as combined:
        configure_wave(combined)
        for index, (chunk, subparts) in enumerate(zip(plan.chunks, parts)):
            new_chapter = args.chapters and (not chapter_ranges or chapter_ranges[-1]["section"] != chunk.sections[0])
            if index and chunk.starts_section and args.section_pause:
                silence_frames = round(args.section_pause * SAMPLE_RATE)
                combined.writeframesraw(b"\x00" * silence_frames * CHANNELS * SAMPLE_WIDTH)
                position += silence_frames
            if new_chapter:
                if chapter_ranges:
                    chapter_ranges[-1]["end_frame"] = position
                chapter_ranges.append({"section": chunk.sections[0], "title": chunk.titles[0],
                                       "start_frame": position})
            for part in subparts:
                with wave.open(str(part.path), "rb") as source:
                    copy_wave_frames(source, combined, part.frames)
                position += part.frames
        if chapter_ranges:
            chapter_ranges[-1]["end_frame"] = position
    return chapter_ranges


def normalize_master(source: Path, target: Path, args: argparse.Namespace,
                     reporter: Reporter) -> dict[str, Any]:
    base_filter = f"loudnorm=I={args.loudness}:TP={args.true_peak}:LRA={args.lra}"
    analysis = run_process(ffmpeg_base(True) + ["-i", str(source), "-af",
                           base_filter + ":print_format=json", "-f", "null", "-"], args.process_timeout)
    measurements = None
    for block in re.findall(r"\{[^{}]+\}", analysis.stderr):
        try:
            obj = json.loads(block)
            if "input_i" in obj:
                measurements = obj
        except ValueError:
            pass
    keys = ["input_i", "input_tp", "input_lra", "input_thresh", "target_offset"]
    try:
        values = {key: float(measurements[key]) for key in keys}
        valid = all(math.isfinite(v) for v in values.values())
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        reporter.say("    WARNING: loudness could not be measured (possibly silence/very short audio); normalization skipped.")
        shutil.copyfile(source, target)
        return {"applied": False, "reason": "nonfinite_or_missing_measurements"}
    filter_spec = (base_filter + f":measured_I={values['input_i']}:measured_TP={values['input_tp']}"
                   + f":measured_LRA={values['input_lra']}:measured_thresh={values['input_thresh']}"
                   + f":offset={values['target_offset']}:linear=true:print_format=json")
    result = run_process(ffmpeg_base(True) + ["-i", str(source), "-af", filter_spec,
                         "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(target)],
                         args.process_timeout)
    return {"applied": True, "target_lufs": args.loudness, "target_true_peak": args.true_peak,
            "measurements": measurements, "second_pass": result.stderr[-1600:]}


def encode_mp3(source: Path, target: Path, args: argparse.Namespace, title: str,
               track: int | None = None) -> dict[str, Any]:
    command = ffmpeg_base() + ["-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame",
                "-b:a", f"{args.bitrate}k", "-ar", str(SAMPLE_RATE), "-ac", "1",
                "-id3v2_version", "3", "-write_xing", "1", "-metadata", f"title={title}",
                "-metadata", "artist=AI-generated narration", "-metadata",
                "comment=AI-generated speech from supplied text. Playback speed is controlled by the player."]
    if track is not None:
        command += ["-metadata", f"track={track}"]
    run_process(command + [str(target)], args.process_timeout)
    return probe_audio(target, args.process_timeout, full_decode=True)


def slice_wave(source: Path, target: Path, start: int, end: int) -> None:
    with wave.open(str(source), "rb") as original, wave.open(str(target), "wb") as output:
        configure_wave(output)
        if not 0 <= start < end <= original.getnframes():
            raise AudioError("Invalid chapter sample boundaries.")
        original.setpos(start)
        copy_wave_frames(original, output, end - start)


def safe_filename(title: str) -> str:
    name = re.sub(r"[^\w\- ]", "", title, flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name).strip("._ ")[:85]
    return name or "Section"


def assembly_settings(args: argparse.Namespace) -> dict[str, Any]:
    return {name: getattr(args, name) for name in (
        "normalize", "loudness", "true_peak", "lra", "bitrate", "section_pause", "chapters")}


def output_fingerprint(plan: Plan, paths: Paths, args: argparse.Namespace) -> str:
    return digest_json({"version": VERSION, "plan": plan.plan_id,
                        "generation": request_spec("", args),
                        "assembly": assembly_settings(args), "output": str(paths.final)})


def output_record(path: Path, info: dict[str, Any]) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha_file(path), **info}


def completed_valid(record: dict[str, Any] | None, fingerprint: str, args: argparse.Namespace) -> bool:
    if not record or record.get("fingerprint") != fingerprint or record.get("schema") != SCHEMA:
        return False
    outputs = record.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return False
    try:
        for item in outputs:
            path = Path(item["path"])
            if not path.is_file() or path.stat().st_size != item["bytes"] or sha_file(path) != item["sha256"]:
                return False
            probe_audio(path, args.process_timeout)
    except (KeyError, ValueError, TypeError, OSError, AudioError):
        return False
    return True


def ensure_output_allowed(paths: Paths, args: argparse.Namespace, plan: Plan | None = None) -> None:
    record = read_object(paths.work / "completed.json")
    owned = set()
    if record and record.get("schema") == SCHEMA and isinstance(record.get("outputs"), list):
        owned = {item.get("path") for item in record["outputs"] if isinstance(item, dict)}
    if args.adopt_moved_cache and record and isinstance(record.get("outputs"), list):
        for i, item in enumerate(record["outputs"]):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            candidate = paths.final if i == 0 else paths.chapters / Path(item["path"]).name
            if candidate.is_file() and sha_file(candidate) == item.get("sha256"):
                owned.add(str(candidate))
    if paths.final.exists() and str(paths.final) not in owned and not args.overwrite:
        raise TTSError(f"Existing output is not owned by this V3 job: {paths.final}. "
                       "Use --output-dir for a new folder, or --overwrite to explicitly replace it.")
    if paths.final.exists() and not paths.final.is_file():
        raise TTSError(f"Output is not a regular file: {paths.final}")
    if args.chapters and paths.chapters.exists() and not paths.chapters.is_dir():
        raise TTSError(f"Chapter destination is not a directory: {paths.chapters}")
    if args.chapters and plan is not None and not args.overwrite:
        for index, section in enumerate(plan.sections, 1):
            target = paths.chapters / f"{index:03d}_{safe_filename(section.title)}.mp3"
            if target.exists() and str(target) not in owned:
                raise TTSError(f"Chapter output would overwrite an unrelated file: {target}")
    if args.chapters and paths.chapters.exists() and not args.overwrite:
        expected_owned = {str(Path(p).parent) for p in owned if isinstance(p, str)}
        if str(paths.chapters) not in expected_owned and any(paths.chapters.iterdir()):
            raise TTSError(f"Existing chapter folder is not owned by this job: {paths.chapters}")


def finalize(plan: Plan, paths: Paths, parts: list[list[AudioPart]], args: argparse.Namespace,
             reporter: Reporter, fingerprint: str) -> dict[str, Any]:
    paths.final.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem as final destination: os.replace remains atomic.
    with tempfile.TemporaryDirectory(prefix=".tts-assembly-", dir=paths.final.parent) as directory:
        temp = Path(directory)
        master = temp / "master.wav"
        chapters = assemble_master(plan, parts, master, args)
        original_frames = wav_frames(master)
        normalized_info: dict[str, Any] = {"applied": False, "reason": "disabled"}
        if args.normalize:
            reporter.say("  Measuring and normalizing the complete recording...")
            normalized = temp / "normalized.wav"
            normalized_info = normalize_master(master, normalized, args, reporter)
            # Normalization should preserve duration; reject timing drift for chapters.
            difference = abs(wav_frames(normalized) - original_frames)
            if difference > SAMPLE_RATE * 0.1:
                raise AudioError("Normalization unexpectedly changed duration by more than 0.1 second.")
            master = normalized
            if chapters:
                chapters[-1]["end_frame"] = wav_frames(master)
        reporter.say("  Encoding and validating the final MP3...")
        final_temp = temp / "final.mp3"
        final_info = encode_mp3(master, final_temp, args, plan.title)
        expected = wav_frames(master) / SAMPLE_RATE
        if abs(final_info["duration"] - expected) > max(0.35, expected * 0.002):
            raise AudioError("Final MP3 duration differs unexpectedly from assembled PCM.")
        pending: list[tuple[Path, Path, dict[str, Any]]] = [(final_temp, paths.final, final_info)]
        chapter_manifest = []
        for index, chapter in enumerate(chapters, 1):
            wave_part = temp / f"chapter-{index}.wav"
            mp3_part = temp / f"chapter-{index}.mp3"
            slice_wave(master, wave_part, chapter["start_frame"], chapter["end_frame"])
            info = encode_mp3(wave_part, mp3_part, args, chapter["title"], index)
            destination = paths.chapters / f"{index:03d}_{safe_filename(chapter['title'])}.mp3"
            pending.append((mp3_part, destination, info))
            chapter_manifest.append({**chapter, "file": destination.name,
                                     "start_seconds": chapter["start_frame"] / SAMPLE_RATE,
                                     "end_seconds": chapter["end_frame"] / SAMPLE_RATE})
        previous = read_object(paths.work / "completed.json")
        # Every new output has passed validation before replacing any prior output.
        records = []
        for temporary, destination, info in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
            records.append(output_record(destination, info))
        if chapters:
            atomic_json(paths.chapters / "chapters.json", chapter_manifest)
            atomic_text(paths.chapters / "playlist.m3u8", "#EXTM3U\n" + "\n".join(
                row["file"] for row in chapter_manifest) + "\n")
        record = {"schema": SCHEMA, "fingerprint": fingerprint, "plan_id": plan.plan_id,
                  "source_sha256": plan.source_hash, "finished": utc_now(), "outputs": records,
                  "normalization": normalized_info, "chapters": chapter_manifest,
                  "assembly": assembly_settings(args)}
        atomic_json(paths.work / "completed.json", record)
        if chapters and previous and isinstance(previous.get("outputs"), list):
            active = {row["path"] for row in records}
            for item in previous["outputs"]:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    continue
                old_path = Path(item["path"])
                if old_path.parent == paths.chapters and str(old_path) not in active and old_path.is_file():
                    if sha_file(old_path) == item.get("sha256"):
                        old_path.unlink()
                    else:
                        reporter.say(f"    Preserved modified old chapter: {old_path.name}")
        return record


def pretty_duration(seconds: float) -> str:
    seconds = round(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def process_plan(plan: Plan, paths: Paths, args: argparse.Namespace, budget: TokenBudget,
                 provider: LazyClient, limiter: RateLimiter, report: dict[str, Any]) -> None:
    started = time.monotonic()
    stats = Stats()
    report.update({"source": str(plan.source), "planned_chunks": len(plan.chunks),
                   "sections": len(plan.sections), "output": str(paths.final), "status": "running"})
    lock_paths = [paths.work / ".job.lock", paths.final.parent / f".{paths.final.name}.tts.lock"]
    with ExitStack() as stack:
        for lock in sorted(lock_paths, key=str):
            stack.enter_context(file_lock(lock))
        owner = read_object(paths.work / "owner.json")
        if owner and owner.get("source") != str(plan.source) and not args.adopt_moved_cache:
            raise TTSError("The job ownership changed after preflight; use a different output directory.")
        atomic_json(paths.work / "owner.json", {"schema": SCHEMA, "source": str(plan.source)})
        reporter = Reporter(paths.work / "run.log")
        reporter.say(f"\nProcessing {plan.source.name} | {VERSION}")
        try:
            ensure_output_allowed(paths, args, plan)
            export_plan(plan, paths, budget)
            fingerprint = output_fingerprint(plan, paths, args)
            old = read_object(paths.work / "completed.json")
            if not args.restart and not args.rebuild and completed_valid(old, fingerprint, args):
                report.update({"status": "skipped", "duration": old["outputs"][0]["duration"],
                               "bytes": old["outputs"][0]["bytes"]})
                reporter.say("  Final output matches the text/settings and passes validation; skipped.")
                return
            parts: list[list[AudioPart]] = []
            seen: dict[str, list[AudioPart]] = {}
            for i, chunk in enumerate(plan.chunks, 1):
                before = stats.generated
                reporter.say(f"  [{i}/{len(plan.chunks)}] {len(chunk.text):,} characters...")
                subparts = obtain_audio(chunk.text, paths.work / "cache", args, budget, provider,
                                       limiter, stats, reporter, seen)
                parts.append(subparts)
                status = "generated" if stats.generated > before else "cached"
                duration = sum(part.frames for part in subparts) / SAMPLE_RATE
                reporter.say(f"    {status} | {pretty_duration(duration)} audio")
                # A separate progress file NEVER deletes cache records for later chunks.
                atomic_json(paths.work / "progress.json", {"plan_id": plan.plan_id,
                    "completed_planned_chunks": i, "planned_chunks": len(plan.chunks),
                    "stats": asdict(stats), "updated": utc_now()})
            completed = finalize(plan, paths, parts, args, reporter, fingerprint)
            report.update({"status": "ok", "duration": completed["outputs"][0]["duration"],
                           "bytes": completed["outputs"][0]["bytes"], "chapter_count": len(completed["chapters"])})
            reporter.say(f"  Output: {paths.final}")
            reporter.say(f"  Duration: {pretty_duration(report['duration'])} | {report['bytes'] / 1048576:.1f} MiB")
        except KeyboardInterrupt:
            report.update({"status": "interrupted", "error": "Interrupted by user"})
            reporter.say("  Interrupted. Validated chunks are saved; rerun to resume.")
            raise
        except Exception as exc:
            report.update({"status": "failed", "error": safe_error(exc)})
            reporter.say("  ERROR: " + safe_error(exc))
            raise
        finally:
            report.update({**asdict(stats), "elapsed_seconds": round(time.monotonic() - started, 2)})
            atomic_json(paths.work / "last_run.json", report)


def dependency_check(args: argparse.Namespace) -> None:
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise TTSError(f"{name} is missing. Install FFmpeg and make both programs available on PATH.")
    encoders = run_process(["ffmpeg", "-hide_banner", "-encoders"], 30)
    if "libmp3lame" not in encoders.stdout:
        raise TTSError("This FFmpeg build lacks the libmp3lame encoder.")
    if args.normalize:
        filters = run_process(["ffmpeg", "-hide_banner", "-filters"], 30)
        if "loudnorm" not in filters.stdout:
            raise TTSError("This FFmpeg build lacks loudnorm. Install a full build or use --no-normalize.")


def doctor(args: argparse.Namespace) -> int:
    print(f"TTS V{VERSION} setup check (no speech API calls)")
    print(f"Python: {sys.version.split()[0]} | {sys.executable}")
    print(f"Virtual environment: {'yes' if sys.prefix != sys.base_prefix else 'no'}")
    status = 0
    for name in ("openai", "tiktoken"):
        try:
            print(f"{name}: {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            print(f"{name}: not installed")
            if name == "openai":
                status = 1
    print("API key: " + ("present (not displayed)" if os.getenv("OPENAI_API_KEY") else "missing"))
    print(f"Working directory: {Path.cwd()}")
    print(f"Writable: {os.access(Path.cwd(), os.W_OK)}")
    try:
        dependency_check(args)
        for name in ("ffmpeg", "ffprobe"):
            print(f"{name}: {shutil.which(name)}")
        print("MP3 encoder and requested filters: available")
    except TTSError as exc:
        print("ERROR: " + safe_error(exc))
        status = 1
    print(f"Model: {args.model} | voice: {args.voice}")
    print("A present key is not proof of valid credentials, credit, or model access.")
    print("No synthesis was attempted. Playback speed remains external.")
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--config")
    known, _ = early.parse_known_args(argv)
    config: dict[str, Any] = {}
    if known.config:
        config_path = Path(known.config).expanduser().resolve()
        config = read_object(config_path)
        if config is None:
            raise TTSError("--config must be a readable JSON object.")
        unknown = set(config) - SETTINGS
        if unknown:
            raise TTSError("Unknown config keys: " + ", ".join(sorted(unknown)))
        if config.get("pronunciations"):
            if not isinstance(config["pronunciations"], str):
                raise TTSError("Config pronunciations must be a filename or null.")
            p = Path(config["pronunciations"]).expanduser()
            config["pronunciations"] = str(p if p.is_absolute() else config_path.parent / p)
    p = argparse.ArgumentParser(description="Convert text documents to separate narrated MP3s. No speed control.",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input_files", nargs="*", help="Text/Markdown files; omit for interactive multi-select.")
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--config", help="Explicit JSON settings file; command-line options take precedence.")
    p.add_argument("--all", action="store_true", help="Process all discovered input files without a menu.")
    p.add_argument("--input-dir", help="Discover notes only in this directory.")
    p.add_argument("--recursive", action="store_true", help="Discover files in subfolders; caches/venvs excluded.")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--voice", default="cedar")
    p.add_argument("--instructions", default=DEFAULT_INSTRUCTIONS)
    p.add_argument("--instructions-file", help="UTF-8 file containing narration instructions, not narration text.")
    p.add_argument("--max-chars", type=int, default=3800)
    p.add_argument("--max-tokens", type=int, default=1850, help="Local budget INCLUDING instructions and reserve.")
    p.add_argument("--token-counter", choices=("auto", "tiktoken", "bytes"), default="auto")
    p.add_argument("--headings", choices=("auto", "none"), default="auto", help="Heading detection affects structure, not wording.")
    p.add_argument("--markdown-headings", action="store_true", help="Opt-in removal of leading Markdown # heading markers only.")
    p.add_argument("--no-cleanup", action="store_true", help="Compatibility flag: preserve text (already the default).")
    p.add_argument("--pronunciations", help="Optional case-sensitive whole-token replacement JSON; never auto-loaded.")
    p.add_argument("--encoding", default="utf-8-sig", help="Explicit source encoding; no silent legacy-encoding fallback.")
    p.add_argument("--chapters", action=argparse.BooleanOptionalAction, default=False,
                   help="Align requests to sections and also export section MP3s and playlist; more API requests.")
    p.add_argument("--normalize", action=argparse.BooleanOptionalAction, default=True,
                   help="Two-pass whole-recording loudness normalization, not per-chunk leveling.")
    p.add_argument("--loudness", type=float, default=-19.0, help="Requested mono integrated loudness (LUFS).")
    p.add_argument("--true-peak", type=float, default=-1.5, help="Requested normalization true-peak ceiling (dBTP), before MP3 encoding.")
    p.add_argument("--lra", type=float, default=11.0, help="Requested loudness range for loudnorm.")
    p.add_argument("--bitrate", type=int, default=128, choices=(64, 96, 128, 160))
    p.add_argument("--section-pause", type=float, default=0.4,
                   help="Extra silence at request boundaries that begin a new section; internal pauses are model-controlled.")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--output", help="Final MP3 path for one source only.")
    out.add_argument("--output-dir", help="Directory for all outputs and per-source V3 caches.")
    p.add_argument("--overwrite", action="store_true", help="Allow replacing preexisting outputs not owned by V3 (such as V2 MP3s).")
    p.add_argument("--adopt-moved-cache", action="store_true", help="Explicitly transfer job ownership after moving the whole project.")
    p.add_argument("--restart", action="store_true", help="Regenerate requested chunks (paid) but do not delete caches first.")
    p.add_argument("--rebuild", action="store_true", help="Reassemble even when completed output matches; reuse valid cached chunks.")
    p.add_argument("--assemble-only", action="store_true", help="Use cached audio only; never create an API client or make requests.")
    p.add_argument("--attempts", "--retries", dest="attempts", type=int, default=4,
                   help="Total attempts per speech request, including the first.")
    p.add_argument("--read-timeout", type=float, default=120.0, help="Maximum wait for an individual network read.")
    p.add_argument("--request-deadline", type=float, default=600.0,
                   help="Streaming elapsed deadline checked between blocks; a blocked read is bounded by read-timeout.")
    p.add_argument("--retry-budget", type=float, default=900.0, help="Do not START a retry beyond this elapsed budget.")
    p.add_argument("--process-timeout", type=float, default=1800.0, help="Timeout for each FFmpeg/ffprobe operation.")
    p.add_argument("--rpm", type=float, default=30.0, help="Maximum request starts per minute within this sequential process.")
    p.add_argument("--stop-on-error", action="store_true", help="Stop batch on any file failure; auth/quota failures always stop it.")
    p.add_argument("--dry-run", action="store_true", help="Prepare and inspect only; no speech API calls or audio generation.")
    p.add_argument("--show-chunks", action="store_true", help="Show the exact text planned for each request.")
    p.add_argument("--export-plan", action="store_true", help="Write prepared text/diff/plan during dry-run (always saved for real runs).")
    p.add_argument("--doctor", action="store_true", help="Check the local setup without contacting the speech API.")
    p.set_defaults(**config)
    args = p.parse_args(argv)
    if args.instructions_file:
        args.instructions = Path(args.instructions_file).expanduser().read_text(encoding="utf-8-sig").strip()
    if args.no_cleanup:
        args.markdown_headings = False
    # argparse type/choices checks do not cover every JSON default, so validate again.
    for name in ("chapters", "normalize", "markdown_headings"):
        if not isinstance(getattr(args, name), bool):
            raise TTSError(f"Config {name} must be true or false.")
    for name in ("model", "voice", "instructions", "encoding", "token_counter", "headings"):
        if not isinstance(getattr(args, name), str):
            raise TTSError(f"{name} must be a string.")
    for name in ("max_chars", "max_tokens", "attempts", "bitrate"):
        if type(getattr(args, name)) is not int:
            raise TTSError(f"{name} must be an integer.")
    for name in ("loudness", "true_peak", "lra", "section_pause", "read_timeout", "request_deadline",
                 "retry_budget", "process_timeout", "rpm"):
        value = getattr(args, name)
        if type(value) not in (float, int) or not math.isfinite(value):
            raise TTSError(f"{name} must be a finite number.")
    if not 200 <= args.max_chars <= 4096:
        raise TTSError("--max-chars must be between 200 and 4096.")
    if not 512 <= args.max_tokens <= 1900:
        raise TTSError("--max-tokens must be between 512 and 1900, including instructions/reserve.")
    if len(args.instructions) > 4096:
        raise TTSError("Instructions must not exceed 4096 characters.")
    if args.model not in {"tts-1", "tts-1-hd", "gpt-4o-mini-tts"} and not re.fullmatch(r"gpt-4o-mini-tts-\d{4}-\d{2}-\d{2}", args.model):
        raise TTSError("Use a supported speech model, not a chat model.")
    voices = LEGACY_VOICES if args.model in {"tts-1", "tts-1-hd"} else ALL_VOICES
    if args.voice not in voices:
        raise TTSError(f"Voice {args.voice!r} is incompatible with {args.model}. Choices: {', '.join(sorted(voices))}")
    if args.model in {"tts-1", "tts-1-hd"} and args.instructions != DEFAULT_INSTRUCTIONS:
        raise TTSError("tts-1/tts-1-hd do not support custom instructions.")
    if args.headings not in {"auto", "none"} or args.token_counter not in {"auto", "tiktoken", "bytes"}:
        raise TTSError("Invalid heading/token-counter setting.")
    if args.bitrate not in {64, 96, 128, 160}:
        raise TTSError("Unsupported bitrate for the chosen output pipeline.")
    if not -70 <= args.loudness <= -5 or not -9 <= args.true_peak <= 0 or not 1 <= args.lra <= 50:
        raise TTSError("Normalization targets are outside loudnorm's supported ranges.")
    if not 0 <= args.section_pause <= 5 or not 1 <= args.attempts <= 10 or not 0 < args.rpm <= 600:
        raise TTSError("Invalid section pause, attempts, or request rate.")
    if any(getattr(args, name) <= 0 for name in ("read_timeout", "request_deadline", "retry_budget", "process_timeout")):
        raise TTSError("Timeouts must be positive.")
    try:
        codecs.lookup(args.encoding)
    except LookupError as exc:
        raise TTSError(f"Unknown text encoding: {args.encoding}") from exc
    if args.restart and args.assemble_only:
        raise TTSError("--restart and --assemble-only cannot be combined.")
    if args.pronunciations is not None and not isinstance(args.pronunciations, str):
        raise TTSError("pronunciations must be a filename or null.")
    if args.all and args.input_files:
        raise TTSError("Use explicit input files OR --all, not both.")
    return args


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir)
    args = parse_args(argv)
    if args.doctor:
        return doctor(args)
    started = time.monotonic()
    if not args.output and not args.output_dir:
        args.output_dir = str(script_dir / "audio")
    sources = resolve_inputs(args, script_dir)
    if not sources:
        print("No files selected.")
        return 0
    if args.output and len(sources) != 1:
        raise TTSError("--output requires exactly one source; use --output-dir for a batch.")
    layouts = [paths_for(source, args) for source in sources]
    collision_check(sources, layouts, args)
    if not args.dry_run:
        dependency_check(args)
        # Check every output BEFORE any API request, including collision with V2 finals.
        for paths in layouts:
            ensure_output_allowed(paths, args)
    budget = TokenBudget(args)
    rules = load_pronunciations(args.pronunciations)
    print(f"\nOpenAI Text-to-Speech V{VERSION} | {len(sources)} separate files")
    print(f"Model: {args.model} | Voice: {args.voice} | AI-generated narration")
    print("Source wording is preserved. Playback speed is controlled by your audio player.")
    if budget.warning:
        print("NOTE: " + budget.warning)
    plans: list[Plan | None] = []
    results: list[dict[str, Any]] = []
    # Validate all text before starting generation; a bad source need not block others.
    for source in sources:
        result: dict[str, Any] = {"source": str(source), "status": "pending"}
        results.append(result)
        try:
            plan = prepare_plan(source, args, budget, rules)
            print_plan(plan, budget, args)
            plans.append(plan)
        except (OSError, ValueError, TTSError) as exc:
            plans.append(None)
            result.update({"status": "failed", "error": safe_error(exc)})
            print(f"ERROR preparing {source.name}: {safe_error(exc)}")
    if not args.dry_run:
        for plan, paths in zip(plans, layouts):
            if plan is not None:
                ensure_output_allowed(paths, args, plan)
    if args.stop_on_error and any(p is None for p in plans):
        raise TTSError("Preparation failed; --stop-on-error prevented all generation.")
    if args.dry_run:
        for plan, paths, result in zip(plans, layouts, results):
            if plan:
                if args.export_plan:
                    with file_lock(paths.work / ".job.lock"):
                        export_plan(plan, paths, budget)
                    print(f"  Plan saved: {paths.work / 'plans' / plan.plan_id}")
                result.update({"status": "dry-run", "planned_chunks": len(plan.chunks)})
        print("\nDry run complete. No speech API calls were made.")
        return 1 if any(r["status"] == "failed" for r in results) else 0
    root = Path(args.output_dir).expanduser().resolve() if args.output_dir else Path.cwd()
    report_dir = root / ".tts_runs"
    report_file = report_dir / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json")
    report_dir.mkdir(parents=True, exist_ok=True)
    provider = LazyClient(args)
    limiter = RateLimiter(args.rpm)
    interrupted = False
    fatal = False
    try:
        for plan, paths, result in zip(plans, layouts, results):
            if plan is None:
                continue
            try:
                process_plan(plan, paths, args, budget, provider, limiter, result)
            except FatalAPIError:
                fatal = True
                break
            except KeyboardInterrupt:
                interrupted = True
                break
            except Exception as exc:
                result.update({"status": "failed", "error": safe_error(exc)})
                print(f"File failed; completed cache entries are preserved: {safe_error(exc)}")
                if args.stop_on_error:
                    break
            finally:
                atomic_json(report_file, {"version": VERSION, "updated": utc_now(), "files": results})
    finally:
        provider.close()
        for result in results:
            if result["status"] == "pending":
                result["status"] = "not-run"
        atomic_json(report_file, {"version": VERSION, "finished": utc_now(),
                                 "elapsed_seconds": round(time.monotonic() - started, 2), "files": results})
    print("\n" + "=" * 70 + "\nBatch summary")
    for result in results:
        detail = f" | {pretty_duration(result['duration'])}" if "duration" in result else ""
        print(f"  {result['status'].upper():<11} {Path(result['source']).name}{detail}")
    print(f"Generated: {sum(r.get('generated', 0) for r in results)} unique chunks | "
          f"Reused: {sum(r.get('cached', 0) for r in results)} | "
          f"Speech attempts: {sum(r.get('requests', 0) for r in results)}")
    print(f"Report: {report_file}")
    print(f"Elapsed: {pretty_duration(time.monotonic() - started)}")
    if interrupted:
        return 130
    return 1 if fatal or any(r["status"] in {"failed", "not-run"} for r in results) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Completed cache entries have not been deleted.", file=sys.stderr)
        raise SystemExit(130)
    except (TTSError, OSError, ValueError) as exc:
        print("ERROR: " + safe_error(exc), file=sys.stderr)
        raise SystemExit(1)
