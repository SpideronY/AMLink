"""Manual checkpoints adapter — ~/.session-bridge/checkpoints/*.json.

`save_active_session` writes a condensed snapshot here, useful when a
harness does not persist its sessions in plain sight on disk.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from .base import BaseAdapter, SessionRecord, iso_from_ms, now_ms

_DEFAULT_DIR = Path.home() / ".session-bridge" / "checkpoints"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, n: int = 24) -> str:
    s = _SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return (s[:n].rstrip("-") or "session")


class CheckpointAdapter(BaseAdapter):
    name = "checkpoint"

    def dir(self) -> Path:
        # the "checkpoints_dir" cfg key lives at the config root
        raw = self.cfg.get("checkpoints_dir")
        return Path(raw).expanduser() if raw else _DEFAULT_DIR

    def available(self) -> bool:
        return self.dir().exists()

    def fingerprint(self):
        d = self.dir()
        if not d.exists():
            return []
        return sorted((f.name, f.stat().st_mtime_ns) for f in d.glob("*.json"))

    def list_sessions(self) -> list[SessionRecord]:
        d = self.dir()
        if not d.exists():
            return []
        records = []
        for f in sorted(d.glob("*.json")):
            data = self._read(f)
            if not data:
                continue
            records.append(
                SessionRecord(
                    harness=self.name,
                    session_id=data.get("session_id") or f.stem,
                    title=data.get("title") or f.stem,
                    project=data.get("project") or "",
                    created_ms=data.get("created_ms"),
                    updated_ms=data.get("updated_ms") or data.get("created_ms"),
                    source_path=str(f),
                    extra={"source_harness": data.get("source_harness") or ""},
                )
            )
        records.sort(key=lambda r: r.updated_ms or 0, reverse=True)
        return records

    def load_detail(self, session_id: str) -> Optional[dict]:
        rec = next(
            (r for r in self.list_sessions() if r.session_id == session_id), None
        )
        if rec is None:
            return None
        data = self._read(Path(rec.source_path)) or {}
        return {
            "harness": self.name,
            "session_id": rec.session_id,
            "title": rec.title,
            "project": rec.project,
            "created_ms": rec.created_ms,
            "updated_ms": rec.updated_ms,
            "created": iso_from_ms(rec.created_ms),
            "updated": iso_from_ms(rec.updated_ms),
            "model": "",
            "git_branch": "",
            "first_prompt": data.get("summary") or "",
            "last_messages": [],
            "files_touched": {fp: 1 for fp in data.get("files_touched") or []},
            "recent_actions": [],
            "last_assistant_message": data.get("next_steps") or "",
            "todos": [],
            "source_file": rec.source_path,
            "source_harness": data.get("source_harness") or "",
        }

    @staticmethod
    def _read(f: Path) -> Optional[dict]:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def save_checkpoint(
    checkpoints_dir: Path | str | None,
    title: str,
    summary: str,
    files_touched: list[str] | None,
    project: str,
    next_steps: str,
    source_harness: str,
) -> dict:
    """Write a checkpoint and return a summary (used by the MCP tool)."""
    d = Path(checkpoints_dir).expanduser() if checkpoints_dir else _DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    ts = now_ms()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts / 1000))
    session_id = f"ckpt_{stamp}_{slugify(title, 12)}"
    payload = {
        "session_id": session_id,
        "title": title or "checkpoint",
        "summary": summary or "",
        "files_touched": list(dict.fromkeys(files_touched or [])),
        "project": project or "",
        "next_steps": next_steps or "",
        "source_harness": source_harness or "",
        "created_ms": ts,
        "saved_by": "session-bridge",
    }
    path = d / f"{session_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"session_id": session_id, "path": str(path)}
