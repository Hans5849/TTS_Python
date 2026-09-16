from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


STATES = {"waiting", "queued", "processing", "retry_wait", "completed", "failed", "cancelled"}
ACTIVE_STATES = {"waiting", "queued", "processing", "retry_wait"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SourceIdentity:
    canonical_path: str
    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, path: Path) -> "SourceIdentity":
        resolved = path.resolve()
        stat = resolved.stat()
        return cls(str(resolved), stat.st_size, stat.st_mtime_ns)


class JobStore:
    """SQLite-owned job state and source-version identity.

    The `(canonical_path, source_size, source_mtime_ns)` tuple identifies one
    observed version. The schema migration is additive so existing installations
    retain their history.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, policy TEXT NOT NULL,
                status TEXT NOT NULL, output TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            existing = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            additions = {
                "canonical_path": "TEXT", "source_size": "INTEGER", "source_mtime_ns": "INTEGER",
                "speech_sha256": "TEXT", "attempts": "INTEGER NOT NULL DEFAULT 0",
                "selected_provider": "TEXT", "selected_model": "TEXT", "completion_time": "TEXT",
                "next_attempt_at": "REAL",
            }
            for name, definition in additions.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_identity ON jobs(canonical_path, source_size, source_mtime_ns)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, updated_at)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def find_identity(self, identity: SourceIdentity):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM jobs WHERE canonical_path=? AND source_size=? AND source_mtime_ns=? "
                "ORDER BY created_at DESC LIMIT 1",
                (identity.canonical_path, identity.size, identity.mtime_ns),
            ).fetchone()

    def enqueue(self, source: Path, policy: str = "normal"):
        identity = SourceIdentity.from_path(source)
        existing = self.find_identity(identity)
        if existing and existing["status"] in ACTIVE_STATES | {"completed"}:
            return existing, False
        job_id, now = uuid.uuid4().hex, utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO jobs
                (id, source, canonical_path, source_size, source_mtime_ns, policy, status,
                 attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)""",
                (job_id, str(source), identity.canonical_path, identity.size, identity.mtime_ns,
                 policy, now, now),
            )
            return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), True

    # Compatibility helper retained for callers/tests that create synthetic jobs.
    def add(self, source: Path, policy: str = "normal") -> str:
        if source.exists():
            return self.enqueue(source, policy)[0]["id"]
        job_id, now = uuid.uuid4().hex, utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs (id, source, canonical_path, policy, status, attempts, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'waiting', 0, ?, ?)",
                (job_id, str(source), str(source.absolute()), policy, now, now),
            )
        return job_id

    def update(self, job_id: str, status: str, *, output: Path | None = None,
               error: str | None = None, speech_sha256: str | None = None,
               provider: str | None = None, model: str | None = None,
               next_attempt_at: float | None = None, source_path: Path | None = None) -> None:
        if status not in STATES and status != "retried":
            raise ValueError(f"invalid job status: {status}")
        now = utc_now()
        completion = now if status == "completed" else None
        with self.connect() as db:
            db.execute(
                """UPDATE jobs SET status=?, output=COALESCE(?, output), error=?,
                speech_sha256=COALESCE(?, speech_sha256), selected_provider=COALESCE(?, selected_provider),
                selected_model=COALESCE(?, selected_model), next_attempt_at=?,
                completion_time=COALESCE(?, completion_time), source=COALESCE(?, source), updated_at=? WHERE id=?""",
                (status, str(output) if output else None, error, speech_sha256, provider, model,
                 next_attempt_at, completion, str(source_path) if source_path else None, now, job_id),
            )

    def start_attempt(self, job_id: str):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='processing', attempts=attempts+1, error=NULL, "
                       "next_attempt_at=NULL, updated_at=? WHERE id=?", (utc_now(), job_id))
            return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def recover_interrupted(self) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET status='queued', error='Recovered after interrupted processing', "
                "next_attempt_at=NULL, updated_at=? WHERE status='processing'", (utc_now(),)
            )
            return cursor.rowcount

    def due(self, now: float):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM jobs WHERE status='queued' OR (status='retry_wait' AND next_attempt_at<=?) "
                "ORDER BY created_at", (now,),
            ).fetchall()

    def get(self, job_id: str):
        with self.connect() as db:
            return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def find(self, job_or_source: str):
        row = self.get(job_or_source)
        if row:
            return row
        with self.connect() as db:
            return db.execute("SELECT * FROM jobs WHERE source=? OR canonical_path=? ORDER BY created_at DESC LIMIT 1",
                              (job_or_source, str(Path(job_or_source).expanduser().absolute()))).fetchone()

    def list(self, limit: int = 50, status: str | None = None):
        with self.connect() as db:
            if status:
                return db.execute("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                                  (status, limit)).fetchall()
            return db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def failed(self):
        return self.list(status="failed")

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return {row[0]: row[1] for row in db.execute("SELECT status, count(*) FROM jobs GROUP BY status")}

    def reset(self, statuses: set[str]) -> int:
        if not statuses or not statuses <= STATES:
            raise ValueError("invalid reset states")
        placeholders = ",".join("?" for _ in statuses)
        with self.connect() as db:
            cursor = db.execute(f"DELETE FROM jobs WHERE status IN ({placeholders})", tuple(sorted(statuses)))
            return cursor.rowcount

    def reset_terminal(self) -> int:
        return self.reset({"completed", "failed", "cancelled"})
