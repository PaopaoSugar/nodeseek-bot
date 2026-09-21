"""SQLite connection management."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class DatabaseError(Exception):
    """Raised when the database file exists but cannot be written."""


def _explain(exc: Exception, path: Path) -> str:
    """Turn a bare sqlite/OS error into something actionable."""
    return (
        f"数据库不可用：{path}（{exc}）\n"
        "  常见原因：之前以 root 身份跑过 bot（例如 --grant），"
        f"生成的 {path.name} 属于 root，而服务以另一个用户运行。\n"
        "  修复：systemctl stop nodeseek-bot && "
        f"chown -R nodeseek:nodeseek {path.parent} && "
        f"systemctl start nodeseek-bot"
    )


class Database:
    """Owns the process-wide SQLite connection."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("database is not initialized")
        return self._conn

    def initialize(self) -> None:
        path = Path(self._path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._probe_writable()
            self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        except (OSError, sqlite3.Error) as exc:
            self.close()
            raise DatabaseError(_explain(exc, path)) from exc

    def _probe_writable(self) -> None:
        """BEGIN IMMEDIATE is the cheapest write there is.

        It forces SQLite to create the journal (or WAL) file, which is
        exactly the step that fails when the database file belongs to
        another user or the directory is not writable. Doing it here turns
        a silent write failure hours later into a startup error.
        """
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection
        conn.execute("BEGIN")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
