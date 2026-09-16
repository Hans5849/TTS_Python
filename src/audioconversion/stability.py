from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Observation:
    size: int
    mtime_ns: int
    unchanged: int = 1
    observed_at: float = 0.0


class StabilityTracker:
    """Require repeated unchanged `(size, mtime_ns)` observations.

    Observations are additionally separated by the configured interval. This is
    safer for SMB/network copies than treating an old mtime as proof of arrival.
    """

    def __init__(self, checks: int, interval_seconds: float):
        self.checks = checks
        self.interval_seconds = interval_seconds
        self._files: dict[Path, Observation] = {}

    def observe(self, path: Path, now: float) -> bool:
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        previous = self._files.get(path)
        if previous is None or (previous.size, previous.mtime_ns) != signature:
            self._files[path] = Observation(*signature, observed_at=now)
            return self.checks <= 1
        if now - previous.observed_at < self.interval_seconds:
            return previous.unchanged >= self.checks
        previous.unchanged += 1
        previous.observed_at = now
        return previous.unchanged >= self.checks

    def forget(self, path: Path) -> None:
        self._files.pop(path, None)
