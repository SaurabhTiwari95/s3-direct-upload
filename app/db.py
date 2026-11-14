"""Upload records in SQLite (one file, no server). See README, "How it scales", for the path to PostgreSQL.

One connection, guarded by a lock, used from worker threads: statements take microseconds, and
opening a connection per request costs more than the query on Windows.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

PENDING, COMPLETE, REJECTED = "pending", "complete", "rejected"

# Each entry runs once, in order; PRAGMA user_version records how many have run.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE uploads (
        seq          INTEGER PRIMARY KEY AUTOINCREMENT,
        id           TEXT    NOT NULL UNIQUE,
        owner        TEXT    NOT NULL,
        object_key   TEXT    NOT NULL UNIQUE,
        filename     TEXT    NOT NULL,
        content_type TEXT    NOT NULL,
        size         INTEGER NOT NULL CHECK (size > 0),
        status       TEXT    NOT NULL CHECK (status IN ('pending', 'complete', 'rejected')),
        reason       TEXT,
        created_at   INTEGER NOT NULL,
        expires_at   INTEGER NOT NULL,
        completed_at INTEGER
    );
    CREATE INDEX uploads_owner_seq ON uploads (owner, seq DESC);
    """,
]


@dataclass(frozen=True)
class Upload:
    seq: int
    id: str
    owner: str
    object_key: str
    filename: str
    content_type: str
    size: int
    status: str
    reason: str | None
    created_at: int
    expires_at: int
    completed_at: int | None


_COLUMNS = ", ".join(Upload.__dataclass_fields__)


class Repository:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            self._conn.executescript(f"BEGIN; {sql} PRAGMA user_version = {int(number)}; COMMIT;")

    def schema_version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def create(self, *, id: str, owner: str, object_key: str, filename: str, content_type: str, size: int,
               created_at: int, expires_at: int) -> Upload:  # fmt: skip
        with self._lock:
            self._conn.execute(
                "INSERT INTO uploads (id, owner, object_key, filename, content_type, size, status, created_at,"
                " expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (id, owner, object_key, filename, content_type, size, PENDING, created_at, expires_at),
            )
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM uploads WHERE id = ?", (id,)).fetchone()
        return Upload(**row)

    def get(self, id: str, owner: str) -> Upload | None:
        """Only the owner's own records are ever returned."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM uploads WHERE id = ? AND owner = ?", (id, owner)
            ).fetchone()
        return Upload(**row) if row else None

    def finish(self, id: str, status: str, reason: str | None, at: int) -> bool:
        """Move a pending record to complete or rejected. False if it was no longer pending (a race)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE uploads SET status = ?, reason = ?, completed_at = ? WHERE id = ? AND status = ?",
                (status, reason, at, id, PENDING),
            )
        return cur.rowcount == 1

    def list(self, owner: str, limit: int, before: int | None = None) -> list[Upload]:
        """Newest first, keyset-paged on seq (stable and index-only, unlike OFFSET)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM uploads WHERE owner = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                (owner, before if before is not None else 2**63 - 1, limit),
            ).fetchall()
        return [Upload(**r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
