"""Shared adapter for the OpenCode / ZCode family.

Both harnesses share the same SQLite schema lineage:
- session(id, title, directory, time_created, time_updated, parent_id, ...)
- message(id, session_id, [sequence|time_created], data JSON {role, time, ...})
- part(id, message_id, session_id, data JSON {type: text|tool|patch, ...})
- todo(session_id, content, status, priority, position)

Actual columns vary across versions: we introspect PRAGMA table_info and
adapt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import (
    BaseAdapter,
    SessionRecord,
    clamp_text,
    iso_from_ms,
    json_loads_safe,
    normalize_text,
    ro_sqlite,
    table_columns,
)

INJECTED_MARKERS = (
    "# agents.md instructions",
    "<environment_context",
    "<app-context",
    "<system-reminder",
    "<system_reminder",
    "<task-notification",
    "<user_instructions",
    "<turn_aborted",
    "<permissions",
    "<runtime_context",
    "# recommended plugins",
    "the todowrite tool hasn't been used",
    "the todo tool hasn't been used",
    "[request interrupted by user",
)

WRITE_TOOLS = {"edit", "write", "multiedit"}


def _is_injected(text: str) -> bool:
    low = normalize_text(text)[:120]
    return any(low.startswith(m) for m in INJECTED_MARKERS)


class AIFamilyAdapter(BaseAdapter):
    """Parameterized base; see adapters/opencode.py and adapters/zcode.py."""

    name = "aifamily"
    _default_db = None  # set by subclasses

    def db_path(self) -> Path:
        raw = self.cfg.get("db")
        return Path(raw).expanduser() if raw else Path(self._default_db).expanduser()

    def available(self) -> bool:
        return self.db_path().exists()

    def fingerprint(self):
        db = self.db_path()
        if not db.exists():
            return []
        return [(str(db), db.stat().st_mtime_ns, db.stat().st_size)]

    # -- index ----------------------------------------------------------
    def list_sessions(self) -> list[SessionRecord]:
        db = self.db_path()
        if not db.exists():
            return []
        records: list[SessionRecord] = []
        try:
            with ro_sqlite(db) as conn:
                cols = table_columns(conn, "session")
                if not cols:
                    return []
                wanted = [
                    "id", "title", "directory", "time_created", "time_updated",
                    "parent_id", "model",
                ]
                use = [c for c in wanted if c in cols]
                for row in conn.execute(f"SELECT {', '.join(use)} FROM session"):
                    sid = row["id"] or ""
                    if not sid:
                        continue
                    extra: dict = {}
                    if "parent_id" in cols and row["parent_id"]:
                        extra["parent_id"] = row["parent_id"]
                    if "subagent" in sid or "subagent" in (row["title"] or ""):
                        extra["subagent"] = True
                    records.append(
                        SessionRecord(
                            harness=self.name,
                            session_id=sid,
                            title=clamp_text(" ".join((row["title"] or "").split()), 100),
                            project=row["directory"] or "",
                            created_ms=row["time_created"],
                            updated_ms=row["time_updated"],
                            source_path=str(db),
                            extra=extra,
                        )
                    )
        except Exception:
            return []
        records.sort(key=lambda r: r.updated_ms or r.created_ms or 0, reverse=True)
        return records

    # -- detail ---------------------------------------------------------
    def load_detail(self, session_id: str) -> Optional[dict]:
        db = self.db_path()
        if not db.exists():
            return None
        rec = next(
            (r for r in self.list_sessions() if r.session_id == session_id), None
        )
        if rec is None:
            for r in self.list_sessions():
                if session_id.lower() in r.session_id.lower():
                    rec = r
                    break
        if rec is None:
            return None

        messages: list[dict] = []  # {id, role, created_ms}
        parts_by_msg: dict[str, list[dict]] = {}
        try:
            with ro_sqlite(db) as conn:
                mcols = table_columns(conn, "message")
                if not mcols:
                    return None
                order = next(
                    (c for c in ("sequence", "time_created", "rowid") if c in mcols or c == "rowid"),
                    "rowid",
                )
                for row in conn.execute(
                    f"SELECT rowid AS __rid, id, data FROM message "
                    f"WHERE session_id = ? ORDER BY {order}",
                    (session_id,),
                ):
                    data = json_loads_safe(row["data"]) or {}
                    role = data.get("role") or ""
                    t = (data.get("time") or {}).get("created")
                    messages.append(
                        {"id": row["id"], "role": role, "created_ms": t, "__rid": row["__rid"]}
                    )
                self._fetch_parts(conn, session_id, parts_by_msg)
                todos = self._fetch_todos(conn, session_id)
                model = self._fetch_model(conn, session_id, rec)
        except Exception:
            return None

        if not messages:
            return self._empty_detail(rec, model="")

        # stable sort by (created_ms, read order)
        messages.sort(key=lambda m: (m.get("created_ms") or 0,))

        def text_of(mid: str) -> str:
            chunks = []
            for pdata in parts_by_msg.get(mid, []):
                if pdata.get("type") == "text" and pdata.get("text"):
                    chunks.append(pdata["text"])
            return "\n".join(chunks).strip()

        first_prompt = ""
        for m in messages:
            if m["role"] == "user":
                t = text_of(m["id"])
                if t and not _is_injected(t):
                    first_prompt = t
                    break
                if t and not first_prompt:
                    first_prompt = t  # everything injected? keep the first one
        if not first_prompt:
            # some user messages have no text part (attachments)
            first_prompt = ""

        files: dict[str, int] = {}
        for plist in parts_by_msg.values():
            for pdata in plist:
                ptype = pdata.get("type")
                if ptype == "patch":
                    for fp in pdata.get("files") or []:
                        files[fp] = files.get(fp, 0) + 1
                elif ptype == "tool":
                    tool = normalize_text(pdata.get("tool") or "")
                    if tool in WRITE_TOOLS:
                        state = pdata.get("state") or {}
                        inp = state.get("input") or {}
                        fp = inp.get("filePath") or inp.get("file_path") or ""
                        if fp:
                            files[fp] = files.get(fp, 0) + 1

        last_msgs = []
        for m in reversed(messages):
            if m["role"] not in ("user", "assistant"):
                continue
            t = text_of(m["id"])
            if not t:
                continue
            # messages injected by the harness (reminders, context): skipped
            if m["role"] == "user" and _is_injected(t):
                continue
            last_msgs.append(
                {
                    "role": m["role"],
                    "text": t,
                    "time": iso_from_ms(m.get("created_ms")),
                }
            )
            if len(last_msgs) >= 12:
                break
        last_msgs.reverse()

        last_assistant = next(
            (m["text"] for m in reversed(last_msgs) if m["role"] == "assistant"), ""
        )

        return {
            "harness": self.name,
            "session_id": rec.session_id,
            "title": rec.title,
            "project": rec.project,
            "created_ms": rec.created_ms,
            "updated_ms": rec.updated_ms,
            "created": iso_from_ms(rec.created_ms),
            "updated": iso_from_ms(rec.updated_ms),
            "model": model,
            "git_branch": "",
            "first_prompt": first_prompt,
            "last_messages": last_msgs,
            "files_touched": files,
            "recent_actions": [],
            "last_assistant_message": last_assistant,
            "todos": todos,
            "source_file": str(db),
        }

    def _fetch_parts(self, conn, session_id: str, parts_by_msg: dict) -> None:
        pcols = table_columns(conn, "part")
        if not pcols:
            return
        link = "message_id" if "message_id" in pcols else None
        if link:
            sql = f"SELECT {link}, data FROM part WHERE session_id = ?"
        else:
            sql = "SELECT NULL AS message_id, data FROM part WHERE session_id = ?"
        try:
            for row in conn.execute(sql, (session_id,)):
                data = json_loads_safe(row["data"])
                if not isinstance(data, dict):
                    continue
                parts_by_msg.setdefault(row["message_id"] or "", []).append(data)
        except Exception:
            pass

    def _fetch_todos(self, conn, session_id: str) -> list[dict]:
        if "todo" not in {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            return []
        try:
            rows = conn.execute(
                "SELECT content, status FROM todo WHERE session_id = ? "
                "ORDER BY position",
                (session_id,),
            ).fetchall()
            return [{"content": r["content"], "status": r["status"]} for r in rows]
        except Exception:
            return []

    def _fetch_model(self, conn, session_id: str, rec: SessionRecord) -> str:
        return ""

    def _empty_detail(self, rec: SessionRecord, model: str = "") -> dict:
        return {
            "harness": self.name,
            "session_id": rec.session_id,
            "title": rec.title,
            "project": rec.project,
            "created_ms": rec.created_ms,
            "updated_ms": rec.updated_ms,
            "created": iso_from_ms(rec.created_ms),
            "updated": iso_from_ms(rec.updated_ms),
            "model": model,
            "git_branch": "",
            "first_prompt": clamp_text(rec.extra.get("first_user_message", ""), 2000),
            "last_messages": [],
            "files_touched": {},
            "recent_actions": [],
            "last_assistant_message": "",
            "todos": [],
            "source_file": rec.source_path,
        }
