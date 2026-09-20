"""ZCode adapter — ~/.zcode/cli/db/db.sqlite (SQLite)."""
from __future__ import annotations

from pathlib import Path

from .aifamily import AIFamilyAdapter
from .base import json_loads_safe

_DEFAULT_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"


class ZCodeAdapter(AIFamilyAdapter):
    name = "zcode"
    _default_db = str(_DEFAULT_DB)

    def _fetch_model(self, conn, session_id, rec) -> str:
        """Model taken from the first messages (data.modelId, assistant msgs)."""
        try:
            rows = conn.execute(
                "SELECT data FROM message WHERE session_id = ? "
                "ORDER BY rowid LIMIT 20",
                (session_id,),
            ).fetchall()
            for row in rows:
                data = json_loads_safe(row["data"])
                if isinstance(data, dict) and data.get("modelId"):
                    mid = data["modelId"]
                    provider = data.get("providerId") or ""
                    return f"{provider}/{mid}".strip("/")
        except Exception:
            pass
        return ""
