from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib
from typing import Any


class ConfigError(ValueError):
    pass


def default_config_path() -> Path:
    override = os.getenv("AUDIOCONVERSION_CONFIG")
    return Path(override).expanduser() if override else Path.home() / ".config/audioconversion/config.toml"


@dataclass(frozen=True)
class PathsConfig:
    inbox: Path = Path.home() / ".local/share/audioconversion/inbox"
    outbox: Path = Path.home() / ".local/share/audioconversion/outbox"
    processed: Path = Path.home() / ".local/share/audioconversion/processed"
    archive: Path = Path.home() / ".local/share/audioconversion/archive"
    failed: Path = Path.home() / ".local/share/audioconversion/failed"
    state: Path = Path.home() / ".local/state/audioconversion"
    logs: Path = Path.home() / ".local/state/audioconversion/logs"


@dataclass(frozen=True)
class ProviderConfig:
    provider: str = "none"
    model: str = ""
    voice: str = "default"
    endpoint: str = ""


@dataclass(frozen=True)
class RouteConfig:
    preferred: str = "local"
    fallback: str = "none"
    local: ProviderConfig = field(default_factory=ProviderConfig)
    cloud: ProviderConfig = field(default_factory=ProviderConfig)


@dataclass(frozen=True)
class AppConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    processing_mode: str = "hybrid"
    llm: RouteConfig = field(default_factory=RouteConfig)
    tts: RouteConfig = field(default_factory=RouteConfig)
    output_format: str = "mp3"
    watcher_interval: float = 2.0
    stability_seconds: float = 5.0
    cache_enabled: bool = True

    def validate(self) -> None:
        if self.processing_mode not in {"local", "llm", "hybrid"}:
            raise ConfigError("processing.mode must be local, llm, or hybrid")
        if self.output_format not in {"mp3", "wav"}:
            raise ConfigError("output.format must be mp3 or wav")
        for name, route in (("llm", self.llm), ("tts", self.tts)):
            if route.preferred not in {"local", "cloud"} or route.fallback not in {"local", "cloud", "none"}:
                raise ConfigError(f"invalid {name} routing policy")
        if self.watcher_interval <= 0 or self.stability_seconds < 0:
            raise ConfigError("watcher timings must be non-negative")


def _provider(data: dict[str, Any]) -> ProviderConfig:
    return ProviderConfig(**{k: str(v) for k, v in data.items() if k in ProviderConfig.__dataclass_fields__})


def _route(data: dict[str, Any]) -> RouteConfig:
    return RouteConfig(preferred=str(data.get("preferred", "local")), fallback=str(data.get("fallback", "none")),
                       local=_provider(data.get("local", {})), cloud=_provider(data.get("cloud", {})))


def load_config(path: Path | None = None, *, required: bool = False) -> AppConfig:
    path = path or default_config_path()
    if not path.exists():
        if required:
            raise ConfigError(f"configuration does not exist: {path}")
        config = AppConfig()
        config.validate()
        return config
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    known = {"paths", "processing", "llm", "tts", "output", "watcher", "cache", "privacy"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown configuration sections: {', '.join(sorted(unknown))}")
    defaults = PathsConfig()
    paths_raw = raw.get("paths", {})
    paths = PathsConfig(**{name: Path(paths_raw.get(name, getattr(defaults, name))).expanduser()
                           for name in PathsConfig.__dataclass_fields__})
    watcher = raw.get("watcher", {})
    config = AppConfig(paths=paths, processing_mode=str(raw.get("processing", {}).get("mode", "hybrid")),
                       llm=_route(raw.get("llm", {})), tts=_route(raw.get("tts", {})),
                       output_format=str(raw.get("output", {}).get("format", "mp3")),
                       watcher_interval=float(watcher.get("poll_interval_seconds", 2)),
                       stability_seconds=float(watcher.get("stability_seconds", 5)),
                       cache_enabled=bool(raw.get("cache", {}).get("enabled", True)))
    config.validate()
    return config
