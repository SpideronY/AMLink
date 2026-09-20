"""Adaptateurs de harness pour le Session Bridge."""
from .base import BaseAdapter, SessionRecord
from .checkpoint import CheckpointAdapter
from .codex import CodexAdapter
from .opencode import OpenCodeAdapter
from .zcode import ZCodeAdapter

__all__ = [
    "BaseAdapter",
    "SessionRecord",
    "CheckpointAdapter",
    "CodexAdapter",
    "OpenCodeAdapter",
    "ZCodeAdapter",
]
