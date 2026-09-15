from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, policy TEXT NOT NULL,
                status TEXT NOT NULL, output TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def add(self, source: Path, policy: str = "normal") -> str:
        job_id, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, 'waiting', NULL, NULL, ?, ?)",
                       (job_id, str(source), policy, now, now))
        return job_id

    def update(self, job_id: str, status: str, *, output: Path | None = None, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?, output=?, error=?, updated_at=? WHERE id=?",
                       (status, str(output) if output else None, error, now, job_id))

    def list(self, limit: int = 50):
        with self.connect() as db:
            return db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return {row[0]: row[1] for row in db.execute("SELECT status, count(*) FROM jobs GROUP BY status")}
