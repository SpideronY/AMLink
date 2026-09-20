"""Multi-harness indexer: adapter registry, cache, search.

Search is 100% local (SQLite/JSONL/protobuf), no LLM calls.
"""
from __future__ import annotations

import difflib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from adapters.antigravity import AntigravityAdapter
from adapters.base import BaseAdapter, SessionRecord, normalize_text
from adapters.checkpoint import CheckpointAdapter
from adapters.codex import CodexAdapter
from adapters.opencode import OpenCodeAdapter
from adapters.zcode import ZCodeAdapter

ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = Path.home() / ".session-bridge" / "index-cache.json"

LATEST_SYNONYMS = {"latest", "last", "derniere", "dernier", "recent", "recents", "récente"}


def load_config(path: Path | str | None = None) -> dict:
    p = Path(path).expanduser() if path else ROOT / "config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Indexer:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        hcfg = self.config.get("harnesses") or {}
        self.adapters: dict[str, BaseAdapter] = {
            "codex": CodexAdapter(hcfg.get("codex") or {}),
            "opencode": OpenCodeAdapter(hcfg.get("opencode") or {}),
            "zcode": ZCodeAdapter(hcfg.get("zcode") or {}),
            "antigravity": AntigravityAdapter(hcfg.get("antigravity") or {}),
            "checkpoint": CheckpointAdapter(
                {"checkpoints_dir": self.config.get("checkpoints_dir")}
            ),
        }
        cache_cfg = self.config.get("cache") or {}
        self.cache_enabled = bool(cache_cfg.get("enabled", True))
        raw_path = cache_cfg.get("path")
        self.cache_path = Path(raw_path).expanduser() if raw_path else DEFAULT_CACHE
        self.ttl = int(cache_cfg.get("ttl_seconds", 60))
        self._fingerprint: list | None = None

    # -- adapters ---------------------------------------------------------
    def enabled_adapters(self, which: str = "all") -> list[BaseAdapter]:
        if which and which not in ("all", "auto"):
            names = [which]
        else:
            names = list(self.adapters)
        out = []
        for n in names:
            a = self.adapters.get(n)
            if a is None:
                continue
            if not (a.cfg or {}).get("enabled", True):
                continue
            try:
                if a.available():
                    out.append(a)
            except Exception:
                continue
        return out

    def adapter_names(self) -> list[str]:
        return [a.name for a in self.enabled_adapters()]

    # -- cache ------------------------------------------------------------
    def _current_fingerprint(self) -> list:
        if self._fingerprint is not None:
            return self._fingerprint
        fp: list = []
        for a in self.enabled_adapters():
            try:
                fp.extend((a.name, item) for item in a.fingerprint())
            except Exception:
                continue
        self._fingerprint = fp
        return fp

    def _read_cache(self) -> Optional[list[SessionRecord]]:
        if not self.cache_enabled or not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data.get("fingerprint") != self._current_fingerprint():
                return None
            if time.time() - data.get("ts", 0) > self.ttl:
                return None
            return [SessionRecord(**r) for r in data.get("records", [])]
        except (OSError, ValueError, TypeError):
            return None

    def _write_cache(self, records: list[SessionRecord]) -> None:
        if not self.cache_enabled:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ts": time.time(),
                "fingerprint": self._current_fingerprint(),
                "records": [asdict(r) for r in records],
            }
            self.cache_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def refresh(self) -> None:
        self._fingerprint = None
        try:
            self.cache_path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- index ------------------------------------------------------------
    def build_index(self, force: bool = False) -> list[SessionRecord]:
        if not force:
            cached = self._read_cache()
            if cached is not None:
                return cached
        records: list[SessionRecord] = []
        for a in self.enabled_adapters():
            try:
                records.extend(a.list_sessions())
            except Exception:
                continue
        records.sort(key=lambda r: r.updated_ms or r.created_ms or 0, reverse=True)
        self._write_cache(records)
        return records

    # -- search -----------------------------------------------------------
    @staticmethod
    def _is_subagent(r: SessionRecord) -> bool:
        return bool(r.extra.get("subagent")) or "subagent" in r.session_id

    @staticmethod
    def score_record(r: SessionRecord, qn: str) -> float:
        sid = normalize_text(r.session_id)
        if qn == sid:
            return 1000.0
        if len(qn) >= 6 and qn in sid:
            return 500.0
        titles = [normalize_text(r.title)]
        alt = normalize_text(r.extra.get("alt_title") or "")
        if alt:
            titles.append(alt)
        score = 0.0
        for title in titles:
            if qn in title:
                score = max(score, 300.0 + 50.0 * len(qn) / max(len(title), 1))
            ratio = difflib.SequenceMatcher(None, qn, title).ratio()
            if ratio >= 0.45:
                score = max(score, ratio * 250.0)
            q_tokens = set(qn.split())
            t_tokens = set(title.split())
            if q_tokens and t_tokens:
                overlap = len(q_tokens & t_tokens) / len(q_tokens)
                if overlap > 0.4:
                    score = max(score, overlap * 200.0)
        proj = normalize_text(r.project)
        if len(qn) >= 4 and qn in proj:
            score = max(score, 120.0)
        return score

    def search(
        self, query: str, harness: str = "auto"
    ) -> tuple[Optional[SessionRecord], list[SessionRecord]]:
        """Return (best match, other matches)."""
        records = self.build_index()
        if harness not in ("auto", "all", ""):
            records = [r for r in records if r.harness == harness]

        qn = normalize_text(query)
        if qn in LATEST_SYNONYMS:
            mains = [r for r in records if not self._is_subagent(r)]
            pool = mains or records
            return (pool[0] if pool else None), []

        scored: list[tuple[float, SessionRecord]] = []
        for r in records:
            s = self.score_record(r, qn)
            if self._is_subagent(r):
                s *= 0.5
            if s > 0:
                scored.append((s, r))
        if not scored:
            return None, []
        scored.sort(key=lambda sr: sr[0], reverse=True)
        best = scored[0][1]
        alts = [r for s, r in scored[1:6] if s >= 30]
        return best, alts

    # -- detail -----------------------------------------------------------
    def load_detail(self, record: SessionRecord) -> Optional[dict]:
        a = self.adapters.get(record.harness)
        if a is None:
            return None
        try:
            return a.load_detail(record.session_id)
        except Exception:
            return None

    def limits(self, detail: str = "standard") -> dict:
        all_limits = self.config.get("payload_limits") or {}
        return all_limits.get(detail) or all_limits.get("standard") or {
            "first_prompt_max_chars": 1500,
            "last_messages": 6,
            "message_max_chars": 600,
            "files_max": 40,
            "recent_actions": 5,
            "todos_max": 15,
        }
