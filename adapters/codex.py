"""Codex adapter (CLI / Desktop).

Sources (as observed on a real install):
- Index  : ~/.codex/state_<N>.sqlite -> threads table
           (id, title, name, cwd, first_user_message, rollout_path,
            created_at_ms, updated_at_ms, archived, model, git_branch)
- Titles : ~/.codex/session_index.jsonl (id, thread_name, updated_at)
- Detail : rollout JSONL (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)
  lines {timestamp, ordinal, type, payload} with type in
  {session_meta, turn_context, response_item, event_msg, compacted}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .base import (
    BaseAdapter,
    SessionRecord,
    clamp_text,
    iso_from_ms,
    normalize_text,
    ro_sqlite,
    table_columns,
)

# Prefixes of "user" messages injected by Codex (excluded from the real
# first prompt)
INJECTED_MARKERS = (
    "# agents.md instructions",
    "<environment_context",
    "<environment_context>",
    "<app-context",
    "please implement this plan",
    "<user_instructions",
    "<turn_aborted",
    "<permissions",
    "# recommended plugins",
    "<runtime_context",
    "<system_reminder",
    "<system-reminder",
    "<task-notification",
    "<recommended_plugins",
    "the todowrite tool hasn't been used",
)

_ROLLOUT_RE = re.compile(r"rollout-.*\.jsonl$", re.IGNORECASE)
_TITLE_CUT_RE = re.compile(r"\\?---|\\#{1,3} ")


def _is_injected(text: str) -> bool:
    low = normalize_text(text)[:120]
    return any(low.startswith(m) for m in INJECTED_MARKERS)


class CodexAdapter(BaseAdapter):
    name = "codex"

    # -- paths ----------------------------------------------------------
    def home(self) -> Path:
        raw = self.cfg.get("home")
        return Path(raw).expanduser() if raw else Path.home() / ".codex"

    def _state_db(self) -> Optional[Path]:
        raw = self.cfg.get("state_db")
        if raw:
            p = Path(raw).expanduser()
            return p if p.exists() else None
        home = self.home()
        if not home.exists():
            return None
        # state_5.sqlite, state_4.sqlite, ... -> highest version number
        candidates = []
        for f in home.glob("state_*.sqlite"):
            m = re.search(r"state_(\d+)\.sqlite$", f.name)
            if m:
                candidates.append((int(m.group(1)), f))
        return max(candidates)[1] if candidates else None

    def sessions_dir(self) -> Path:
        raw = self.cfg.get("sessions_dir")
        return Path(raw).expanduser() if raw else self.home() / "sessions"

    def available(self) -> bool:
        return self.home().exists() and (
            self._state_db() is not None or self.sessions_dir().exists()
        )

    def fingerprint(self):
        parts = []
        db = self._state_db()
        if db:
            parts.append((str(db), db.stat().st_mtime_ns))
        sdir = self.sessions_dir()
        if sdir.exists():
            parts.append((str(sdir), sdir.stat().st_mtime_ns))
        idx = self.home() / "session_index.jsonl"
        if idx.exists():
            parts.append((str(idx), idx.stat().st_mtime_ns))
        return parts

    # -- index ----------------------------------------------------------
    def _title_index(self) -> dict[str, str]:
        """session_index.jsonl : id -> thread_name (title fallback)."""
        out: dict[str, str] = {}
        path = self.home() / "session_index.jsonl"
        if not path.exists():
            return out
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("id"):
                    name = obj.get("thread_name") or obj.get("title") or ""
                    if name:
                        out[obj["id"]] = name
        except OSError:
            pass
        return out

    def list_sessions(self) -> list[SessionRecord]:
        db = self._state_db()
        if db:
            records = self._list_from_state(db)
        else:
            records = self._list_from_rollouts()
        titles = self._title_index()
        by_id = {r.session_id: r for r in records}
        # threads only known through session_index.jsonl (missing from the
        # state db)
        for sid, name in titles.items():
            if sid not in by_id:
                records.append(
                    SessionRecord(
                        harness=self.name,
                        session_id=sid,
                        title=clamp_text(" ".join(name.split()), 100),
                        project="",
                        created_ms=None,
                        updated_ms=None,
                        source_path=self._resolve_rollout("", sid),
                        extra={},
                    )
                )
                by_id[sid] = records[-1]
        for r in records:
            if not r.title:
                raw = " ".join(titles.get(r.session_id, "").split())
                r.title = clamp_text(raw, 100)
            if not r.title:
                first = " ".join((r.extra.pop("first_user_message", "") or "").split())
                r.title = clamp_text(first, 70)
            # a title can be a whole pasted report: cut at the first
            # markdown separator
            r.title = clamp_text(_TITLE_CUT_RE.split(r.title)[0].strip() or r.title, 100)
            # generated title (session_index): kept for fuzzy search
            alt = " ".join(titles.get(r.session_id, "").split())
            if alt and alt != r.title:
                r.extra["alt_title"] = clamp_text(alt, 100)
        records.sort(key=lambda r: r.updated_ms or r.created_ms or 0, reverse=True)
        return records

    def _list_from_state(self, db: Path) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        try:
            with ro_sqlite(db) as conn:
                cols = table_columns(conn, "threads")
                wanted = [
                    "id", "title", "name", "cwd", "first_user_message",
                    "rollout_path", "created_at_ms", "updated_at_ms",
                    "archived", "model", "git_branch",
                ]
                use = [c for c in wanted if c in cols]
                for row in conn.execute(f"SELECT {', '.join(use)} FROM threads"):
                    rid = row["id"] or ""
                    if not rid:
                        continue
                    rollout = row["rollout_path"] or ""
                    raw_title = (row["title"] or row["name"] or "").strip()
                    raw_title = " ".join(raw_title.split())  # single-line title
                    records.append(
                        SessionRecord(
                            harness=self.name,
                            session_id=rid,
                            title=clamp_text(raw_title.lstrip("\\# "), 100),
                            project=row["cwd"] or "",
                            created_ms=row["created_at_ms"],
                            updated_ms=row["updated_at_ms"],
                            source_path=self._resolve_rollout(rollout, rid),
                            extra={
                                "model": row["model"] or "",
                                "git_branch": (row["git_branch"] or "") if "git_branch" in cols else "",
                                "archived": bool(row["archived"]) if "archived" in cols else False,
                                "first_user_message": row["first_user_message"] or ""
                                if "first_user_message" in cols
                                else "",
                            },
                        )
                    )
        except Exception:
            return self._list_from_rollouts()
        return records

    def _list_from_rollouts(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        seen: set[str] = set()
        for base in (self.sessions_dir(), self.home() / "archived_sessions"):
            if not base.exists():
                continue
            for f in sorted(base.rglob("rollout-*.jsonl")):
                meta = self._read_meta(f)
                if not meta:
                    continue
                sid = meta.get("session_id") or ""
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                ts = meta.get("timestamp") or ""
                from .base import ms_from_iso

                created = ms_from_iso(ts)
                records.append(
                    SessionRecord(
                        harness=self.name,
                        session_id=sid,
                        title="",
                        project=meta.get("cwd") or "",
                        created_ms=created,
                        updated_ms=int(f.stat().st_mtime * 1000),
                        source_path=str(f),
                        extra={"archived": base.name == "archived_sessions"},
                    )
                )
        return records

    def _resolve_rollout(self, rollout: str, session_id: str) -> str:
        if rollout:
            p = Path(rollout)
            if not p.is_absolute():
                p = self.home() / rollout
            if p.exists():
                return str(p)
        # fallback: locate by uuid under sessions/ and archived_sessions/
        sid = session_id.split("_")[-1] if session_id else ""
        if sid:
            for base in (self.sessions_dir(), self.home() / "archived_sessions"):
                if base.exists():
                    for f in base.rglob(f"rollout-*{sid}*.jsonl"):
                        return str(f)
        return ""

    @staticmethod
    def _read_meta(path: Path) -> Optional[dict]:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("type") == "session_meta":
                        return obj.get("payload") or {}
                    break
        except OSError:
            pass
        return None

    # -- detail ---------------------------------------------------------
    def load_detail(self, session_id: str) -> Optional[dict]:
        records = {r.session_id: r for r in self.list_sessions()}
        rec = records.get(session_id)
        if rec is None:
            # tolerance: partial id
            for r in records.values():
                if session_id.lower() in r.session_id.lower():
                    rec = r
                    break
        if rec is None:
            return None

        first_user: Optional[str] = None
        messages: list[tuple[str, str, str]] = []  # (role, text, ts_iso)
        files: dict[str, int] = {}
        commands: list[str] = []
        last_agent_msg = ""
        last_cwd = rec.project

        path = rec.source_path
        if path and Path(path).exists():
            with Path(path).open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    ltype = obj.get("type")
                    payload = obj.get("payload") or {}
                    ts = obj.get("timestamp") or ""

                    if ltype == "turn_context":
                        last_cwd = payload.get("cwd") or last_cwd
                    elif ltype == "response_item":
                        if payload.get("type") != "message":
                            continue
                        role = payload.get("role")
                        text = "\n".join(
                            c.get("text", "")
                            for c in payload.get("content", [])
                            if isinstance(c, dict)
                        ).strip()
                        if not text:
                            continue
                        if role == "user":
                            if not _is_injected(text):
                                if first_user is None:
                                    first_user = text
                                messages.append(("user", text, ts))
                        elif role == "assistant":
                            messages.append(("assistant", text, ts))
                    elif ltype == "event_msg":
                        et = payload.get("type")
                        if et == "item_completed":
                            item = payload.get("item") or {}
                            itype = item.get("type")
                            if itype == "FileChange":
                                for fp in (item.get("changes") or {}):
                                    fp = fp.replace("file://", "")
                                    files[fp] = files.get(fp, 0) + 1
                            elif itype == "CommandExecution":
                                cmd = " ".join(item.get("command") or [])
                                if cmd:
                                    commands.append(cmd)
                        elif et == "task_complete":
                            m = payload.get("last_agent_message")
                            if isinstance(m, str) and m.strip():
                                last_agent_msg = m.strip()

        if first_user is None:
            first_user = rec.extra.get("first_user_message") or ""
        last_assistant = last_agent_msg or next(
            (t for r, t, _ in reversed(messages) if r == "assistant"), ""
        )

        return {
            "harness": self.name,
            "session_id": rec.session_id,
            "title": rec.extra.get("alt_title") or rec.title,
            "project": last_cwd or rec.project,
            "created_ms": rec.created_ms,
            "updated_ms": rec.updated_ms,
            "created": iso_from_ms(rec.created_ms),
            "updated": iso_from_ms(rec.updated_ms),
            "model": rec.extra.get("model", ""),
            "git_branch": rec.extra.get("git_branch", ""),
            "first_prompt": first_user or "",
            "last_messages": [
                {"role": r, "text": t, "time": iso_short(ts)} for r, t, ts in messages
            ],
            "files_touched": files,
            "recent_actions": commands,
            "last_assistant_message": last_assistant,
            "todos": [],
            "source_file": rec.source_path,
        }


def iso_short(ts_iso: str) -> str:
    """'2026-09-18T16:31:35.063Z' -> '09-18 16:31' (local)."""
    if not ts_iso:
        return ""
    from .base import ms_from_iso

    ms = ms_from_iso(ts_iso)
    if not ms:
        return ""
    return iso_from_ms(ms)[5:] if iso_from_ms(ms) else ""
