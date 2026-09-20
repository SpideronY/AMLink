"""Shared models and helpers for harness adapters.

All SQLite reads use a strict read-only connection (`file:...?mode=ro`).
If the database is an orphaned WAL (harness crashed with no -shm file),
we fall back to a temporary copy instead of ever writing to the
harness's database.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


@dataclass
class SessionRecord:
    """Lightweight index entry (one session, one line)."""

    harness: str
    session_id: str
    title: str = ""
    project: str = ""
    created_ms: Optional[int] = None
    updated_ms: Optional[int] = None
    source_path: str = ""
    extra: dict = field(default_factory=dict)


class BaseAdapter:
    """Common interface: list the index, load one session's details."""

    name = "base"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}

    # -- API ------------------------------------------------------------
    def available(self) -> bool:
        raise NotImplementedError

    def fingerprint(self) -> Any:
        """Source fingerprint used to invalidate the cache (stat calls)."""
        raise NotImplementedError

    def list_sessions(self) -> list[SessionRecord]:
        raise NotImplementedError

    def load_detail(self, session_id: str) -> Optional[dict]:
        raise NotImplementedError


# ----------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------
def normalize_text(s: str | None) -> str:
    """Casefold + strip accents (for insensitive search)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().strip()


def clamp_text(s: str | None, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[: max(0, n - 1)].rstrip()
    return cut + "…"


def iso_from_ms(ms: Optional[int]) -> str:
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def ms_from_iso(s: str | None) -> Optional[int]:
    """Convert an ISO-8601 timestamp (Z or offset) to epoch ms."""
    if not s:
        return None
    txt = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def now_ms() -> int:
    return int(time.time() * 1000)


# ----------------------------------------------------------------------
# Read-only SQLite helpers
# ----------------------------------------------------------------------
@contextmanager
def ro_sqlite(path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a SQLite database without ever writing to it.

    mode=ro works when -shm exists (harness running) or after a clean
    shutdown (WAL checkpointed). Otherwise (orphaned WAL), use a
    temporary copy.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"SQLite database not found: {p}")
    conn: sqlite3.Connection | None = None
    tmpdir: str | None = None
    try:
        try:
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.Error:
            conn = None
            tmpdir = tempfile.mkdtemp(prefix="amlink-ro-")
            copied = Path(tmpdir) / p.name
            shutil.copy2(p, copied)
            for suffix in ("-wal", "-shm"):
                side = Path(str(p) + suffix)
                if side.exists():
                    shutil.copy2(side, str(copied) + suffix)
            conn = sqlite3.connect(str(copied))
            conn.row_factory = sqlite3.Row
        yield conn
    finally:
        if conn is not None:
            conn.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def json_loads_safe(raw: Any) -> Any:
    """Decode a SQLite JSON field that may be invalid or empty."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    import json

    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
