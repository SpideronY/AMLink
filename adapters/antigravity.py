"""Google Antigravity adapter — ~/.gemini/antigravity.

Conversation files (<cascade>.db) are opaque protobuf blobs: we do NOT
parse them. Readable sources used instead:
- brain/<cascade-id>/.system_generated/logs/transcript.jsonl
    {step_index, source: USER_EXPLICIT|MODEL|SYSTEM, type, status, created_at, content}
- ~/.gemini/antigravity/agyhub_summaries_proto.pb
    (protobuf wire-format walk => titles, file:// workspaces, git branch)
- artifacts at the cascade root (implementation_plan.md, walkthrough.md...)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator, Optional

from .base import (
    BaseAdapter,
    SessionRecord,
    clamp_text,
    iso_from_ms,
    ms_from_iso,
    normalize_text,
)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
META_RE = re.compile(r"<(ADDITIONAL_METADATA|SYSTEM_MESSAGE)>.*?</\1>", re.S)
FILE_MENTION_RE = re.compile(
    r"/?[\w][\w./-]*\.(?:py|ts|tsx|js|jsx|json|md|yml|yaml|toml|sql|css|scss|html|vue|svelte|"
    r"swift|kt|go|rs|java|rb|php|sh|env|cfg|ini|txt)\b"
)
NON_TITLE_PREFIXES = ("file://", "git@", "http", "projects/", "research", "code reviewer")


# ----------------------------------------------------------------------
# Generic protobuf wire-format walker — string extraction
# ----------------------------------------------------------------------
def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _try_utf8(payload: bytes) -> Optional[str]:
    try:
        s = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not s:
        return None
    # reject binary blobs that happen to decode
    printable = sum(1 for c in s if c.isprintable() or c in "\n\r\t")
    if printable / len(s) < 0.9:
        return None
    return s


def walk_wire_strings(buf: bytes, out: list[str] | None = None, depth: int = 0) -> list[str]:
    """Collect strings from length-delimited fields, recursing into
    nested messages."""
    if out is None:
        out = []
    if depth > 6 or len(out) > 8000:
        return out
    i, n = 0, len(buf)
    while i < n:
        try:
            tag, i = _read_varint(buf, i)
        except ValueError:
            break
        field, wt = tag >> 3, tag & 7
        if field == 0:
            break
        if wt == 0:
            try:
                _, i = _read_varint(buf, i)
            except ValueError:
                break
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        elif wt == 2:
            try:
                ln, i = _read_varint(buf, i)
            except ValueError:
                break
            if ln > n - i:
                break
            payload = buf[i : i + ln]
            i += ln
            s = _try_utf8(payload)
            if s is not None and len(s) >= 2:
                out.append(s)
            elif ln > 1:
                walk_wire_strings(payload, out, depth + 1)
        else:
            break
    return out


def _plausible_title(s: str) -> bool:
    if len(s) < 4 or len(s) > 120 or UUID_RE.match(s):
        return False
    low = normalize_text(s)
    if low.startswith(NON_TITLE_PREFIXES) or "/" in s and " " not in s:
        return False
    return any(c.isalpha() for c in s)


class AntigravityAdapter(BaseAdapter):
    name = "antigravity"

    # -- paths ----------------------------------------------------------
    def root(self) -> Path:
        raw = self.cfg.get("root")
        return Path(raw).expanduser() if raw else Path.home() / ".gemini" / "antigravity"

    def brain(self) -> Path:
        return self.root() / "brain"

    def summaries_pb(self) -> Path:
        return self.root() / "agyhub_summaries_proto.pb"

    def available(self) -> bool:
        return self.brain().exists() and any(self.brain().iterdir())

    def fingerprint(self):
        parts = []
        pb = self.summaries_pb()
        if pb.exists():
            parts.append((str(pb), pb.stat().st_mtime_ns))
        b = self.brain()
        if b.exists():
            for d in b.iterdir():
                if d.is_dir():
                    parts.append((d.name, d.stat().st_mtime_ns))
        return parts

    # -- title index (protobuf) ------------------------------------------
    def _summaries(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        pb = self.summaries_pb()
        if not pb.exists():
            return out
        try:
            strings = walk_wire_strings(pb.read_bytes())
        except OSError:
            return out
        cascade_ids = {d.name for d in self.brain().iterdir() if d.is_dir()}
        # known valid workspaces (the walker may emit clipped fragments)
        valid_ws = sorted(
            {
                s[len("file://") :]
                for s in strings
                if s.startswith("file://") and Path(s[len("file://") :]).is_dir()
            }
        )

        def repair_ws(ws: str) -> str:
            if not ws or Path(ws).is_dir():
                return ws
            for v in valid_ws:
                if v.endswith(ws) or ws.endswith(v):
                    return v
            return ws

        for idx, s in enumerate(strings):
            if s not in cascade_ids:
                continue
            info = out.setdefault(s, {})
            window = strings[idx + 1 : idx + 15]
            if "title" not in info:
                for t in window[:4]:
                    if _plausible_title(t):
                        info["title"] = t
                        break
            if "workspace" not in info:
                for t in window:
                    if t.startswith("file://"):
                        info["workspace"] = repair_ws(t[len("file://") :])
                        break
            if "branch" not in info:
                for j, t in enumerate(window):
                    if t.startswith("git@") and j + 1 < len(window):
                        cand = window[j + 1]
                        if not UUID_RE.match(cand) and "/" not in cand and " " not in cand:
                            info["branch"] = cand
                            break
        return out

    # -- index ----------------------------------------------------------
    def list_sessions(self) -> list[SessionRecord]:
        brain = self.brain()
        if not brain.exists():
            return []
        summaries = self._summaries()
        records: list[SessionRecord] = []
        for d in brain.iterdir():
            if not d.is_dir():
                continue
            cid = d.name
            transcript = d / ".system_generated" / "logs" / "transcript.jsonl"
            # technical folders without a transcript (e.g. tempmediaStorage):
            # skipped
            if not transcript.exists() and not UUID_RE.match(cid):
                continue
            created = updated = None
            first_user = ""
            if transcript.exists():
                entries = self._read_transcript(transcript)
                if entries:
                    stamps = [e.get("created_at") for e in entries if e.get("created_at")]
                    if stamps:
                        created = ms_from_iso(stamps[0])
                        updated = ms_from_iso(stamps[-1])
                    for e in entries:
                        if e.get("kind") == "user":
                            first_user = e["text"]
                            break
            if updated is None:
                updated = int(d.stat().st_mtime * 1000)
            if created is None:
                created = updated
            info = summaries.get(cid, {})
            title = info.get("title") or clamp_text(first_user, 70) or f"cascade {cid[:8]}"
            records.append(
                SessionRecord(
                    harness=self.name,
                    session_id=cid,
                    title=title,
                    project=info.get("workspace", ""),
                    created_ms=created,
                    updated_ms=updated,
                    source_path=str(d),
                    extra={"git_branch": info.get("branch", "")},
                )
            )
        records.sort(key=lambda r: r.updated_ms or 0, reverse=True)
        return records

    # -- transcript -----------------------------------------------------
    @staticmethod
    def _clean_user_content(content: str) -> str:
        m = USER_REQ_RE.search(content)
        text = m.group(1) if m else content
        text = META_RE.sub("", text)
        return text.strip()

    @staticmethod
    def _read_transcript(path: Path) -> list[dict]:
        entries: list[dict] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    source = o.get("source") or ""
                    etype = o.get("type") or ""
                    content = (o.get("content") or "").strip()
                    if not content:
                        continue
                    if etype == "USER_INPUT" and source == "USER_EXPLICIT":
                        entries.append(
                            {
                                "kind": "user",
                                "text": AntigravityAdapter._clean_user_content(content),
                                "created_at": o.get("created_at") or "",
                            }
                        )
                    elif source == "MODEL" and etype == "GENERIC":
                        entries.append(
                            {
                                "kind": "tool",
                                "text": content,
                                "created_at": o.get("created_at") or "",
                            }
                        )
        except OSError:
            pass
        return entries

    @staticmethod
    def _summarize_tool_log(content: str) -> str:
        """'Created At.. Completed At.. / The command exited with code 0 / Output:' -> one line."""
        body = re.sub(r"^Created At:.*?\n", "", content, flags=re.S)
        body = re.sub(r"^Completed At:.*?\n", "", body, flags=re.S)
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        return clamp_text(" | ".join(lines[:2]), 160)

    # -- detail ---------------------------------------------------------
    def load_detail(self, session_id: str) -> Optional[dict]:
        rec = next(
            (r for r in self.list_sessions() if r.session_id == session_id), None
        )
        if rec is None:
            return None
        cascade_dir = Path(rec.source_path)
        transcript = cascade_dir / ".system_generated" / "logs" / "transcript.jsonl"
        entries = self._read_transcript(transcript) if transcript.exists() else []

        user_entries = [e for e in entries if e["kind"] == "user"]
        tool_entries = [e for e in entries if e["kind"] == "tool"]
        first_prompt = user_entries[0]["text"] if user_entries else ""
        last_users = user_entries[-3:]

        # mentioned files (honest approximation: plans + git outputs)
        files: dict[str, int] = {}
        project = rec.project
        blob = "\n".join(e["text"] for e in entries)
        for m in FILE_MENTION_RE.finditer(blob):
            fp = m.group(0)
            if fp.startswith(("http://", "https://")):
                continue
            if not fp.startswith("/") and project:
                fp = f"{project.rstrip('/')}/{fp}"
            files[fp] = files.get(fp, 0) + 1

        # optional artifacts (implementation_plan.md, walkthrough.md, ...)
        artifacts: list[str] = []
        for f in sorted(cascade_dir.glob("*.md")):
            artifacts.append(f.name)
        for f in sorted(cascade_dir.glob("*.metadata.json")):
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
                if meta.get("summary"):
                    artifacts.append(f"{f.name}: {clamp_text(meta['summary'], 200)}")
            except (OSError, ValueError):
                pass

        created_at = entries[0]["created_at"] if entries else ""
        updated_at = entries[-1]["created_at"] if entries else ""
        return {
            "harness": self.name,
            "session_id": rec.session_id,
            "title": rec.title,
            "project": project,
            "created_ms": rec.created_ms,
            "updated_ms": rec.updated_ms,
            "created": iso_from_ms(rec.created_ms) or clamp_text(created_at, 16),
            "updated": iso_from_ms(rec.updated_ms) or clamp_text(updated_at, 16),
            "model": "",
            "git_branch": rec.extra.get("git_branch", ""),
            "first_prompt": first_prompt,
            "last_messages": [
                {"role": "user", "text": t["text"], "time": clamp_text(t["created_at"], 16)}
                for t in last_users
            ],
            "files_touched": files,
            "recent_actions": [
                self._summarize_tool_log(t["text"]) for t in tool_entries[-8:]
            ],
            "last_assistant_message": "",
            "todos": [],
            "source_file": str(cascade_dir),
            "artifacts": artifacts,
            "files_note": "fichiers mentionnés (transcript)",
        }
