from __future__ import annotations

from pathlib import Path
import logging
import threading
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelLifecycle(Protocol):
    @property
    def is_loaded(self) -> bool: ...
    @property
    def last_used(self) -> float | None: ...
    def load(self) -> None: ...
    def unload(self) -> None: ...


class ManagedTTSEngine:
    """Thread-safe transparent load/use wrapper for a model-backed engine."""

    def __init__(self, engine, clock=time.monotonic, logger: logging.Logger | None = None):
        if not isinstance(engine, ModelLifecycle):
            raise TypeError("engine does not implement ModelLifecycle")
        self.engine = engine
        self.is_cloud = engine.is_cloud
        self._clock = clock
        self.log = logger or logging.getLogger("audioconversion.lifecycle")
        self._lock = threading.RLock()
        self._active = 0
        self._last_used: float | None = engine.last_used

    @property
    def is_loaded(self) -> bool:
        return self.engine.is_loaded

    @property
    def last_used(self) -> float | None:
        return self._last_used

    def synthesize(self, text: str, output_file: Path) -> None:
        with self._lock:
            if not self.engine.is_loaded:
                self.log.info("loading provider/model: %s", type(self.engine).__name__)
                self.engine.load()
            self._active += 1
        try:
            self.engine.synthesize(text, output_file)
        finally:
            with self._lock:
                self._active -= 1
                self._last_used = self._clock()

    def unload_if_idle(self, timeout: float, now: float | None = None) -> bool:
        with self._lock:
            current = self._clock() if now is None else now
            if timeout <= 0 or self._active or not self.engine.is_loaded or self._last_used is None:
                return False
            if current - self._last_used < timeout:
                return False
            self.engine.unload()
            return True

    def unload(self) -> None:
        with self._lock:
            if not self._active and self.engine.is_loaded:
                self.engine.unload()


class LifecycleManager:
    def __init__(self, logger: logging.Logger | None = None):
        self._providers: list[tuple[ManagedTTSEngine, float]] = []
        self.log = logger or logging.getLogger("audioconversion.lifecycle")

    def register(self, provider: ManagedTTSEngine, idle_timeout: float) -> None:
        self._providers.append((provider, idle_timeout))

    def unload_idle(self) -> None:
        for provider, timeout in self._providers:
            if provider.unload_if_idle(timeout):
                self.log.info("provider/model unloaded after %.0f idle seconds", timeout)

    def shutdown(self) -> None:
        for provider, _ in self._providers:
            provider.unload()
