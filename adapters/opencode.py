"""OpenCode adapter — ~/.local/share/opencode/opencode.db (SQLite)."""
from __future__ import annotations

from pathlib import Path

from .aifamily import AIFamilyAdapter
from .base import json_loads_safe

_DEFAULT_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


class OpenCodeAdapter(AIFamilyAdapter):
    name = "opencode"
    _default_db = str(_DEFAULT_DB)

    def _fetch_model(self, conn, session_id, rec) -> str:
        """session.model is a JSON {"id","providerID","variant"}."""
        try:
            row = conn.execute(
                "SELECT model FROM session WHERE id = ?", (session_id,)
            ).fetchone()
            if row and row["model"]:
                data = json_loads_safe(row["model"])
                if isinstance(data, dict):
                    provider = data.get("providerID") or ""
                    mid = data.get("id") or ""
                    return f"{provider}/{mid}".strip("/")
        except Exception:
            pass
        return ""
