"""Formatting of the condensed payload injected into the receiving agent.

Goal: give the new harness everything it needs to resume the work,
without raw logs, with hard size caps per section.
"""
from __future__ import annotations

from pathlib import Path

from adapters.base import clamp_text

HOME = str(Path.home())


def _short(path: str) -> str:
    return path.replace(HOME, "~") if path.startswith(HOME) else path


def _sanitize(text: str) -> str:
    """Neutralize code fences and trim whitespace."""
    return (text or "").replace("```", "´´´").strip()


def format_payload(detail: dict, limits: dict, alternatives: list | None = None) -> str:
    L: list[str] = []
    # a title can embed an entire pasted report: single-line + clamped
    title = clamp_text(" ".join(_sanitize(detail.get("title") or "").split()), 120) or "sans titre"
    L.append(f"# 🔗 SESSION BRIDGE — {title}")
    L.append("")

    # -- YAML metadata block
    L.append("```yaml")
    L.append(f"harness: {detail.get('harness', '?')}")
    L.append(f"session_id: {detail.get('session_id', '?')}")
    if detail.get("project"):
        L.append(f"project: {_short(str(detail['project']))}")
    if detail.get("created"):
        L.append(f"created: {detail['created']}")
    if detail.get("updated"):
        L.append(f"last_activity: {detail['updated']}")
    if detail.get("model"):
        L.append(f"model: {detail['model']}")
    if detail.get("git_branch"):
        L.append(f"git_branch: {detail['git_branch']}")
    if detail.get("source_harness"):
        L.append(f"checkpoint_saved_from: {detail['source_harness']}")
    L.append("```")
    L.append("")

    # -- objective
    first_prompt = _sanitize(detail.get("first_prompt") or "")
    if first_prompt:
        L.append("## 🎯 Objectif (prompt initial)")
        L.append("")
        L.append(clamp_text(first_prompt, limits.get("first_prompt_max_chars", 1500)))
        L.append("")

    # -- recent exchanges
    msgs = detail.get("last_messages") or []
    n_msgs = limits.get("last_messages", 6)
    max_chars = limits.get("message_max_chars", 600)
    if msgs:
        L.append(f"## 🔄 Derniers échanges ({len(msgs)} derniers)")
        L.append("")
        for m in msgs[-n_msgs:]:
            role = m.get("role", "?")
            icon = "👤" if role == "user" else "🤖"
            ts = m.get("time", "")
            head = f"**{icon} [{role}]**" + (f" _{ts}_" if ts else "")
            L.append(head)
            L.append(clamp_text(_sanitize(m.get("text", "")), max_chars))
            L.append("")

    # -- recent actions
    actions = detail.get("recent_actions") or []
    n_act = limits.get("recent_actions", 5)
    if actions and n_act:
        L.append(f"## 🛠 Dernières actions exécutées")
        L.append("")
        for a in actions[-n_act:]:
            L.append(f"- `{clamp_text(_sanitize(str(a)), 160)}`")
        L.append("")

    # -- files touched
    files: dict = detail.get("files_touched") or {}
    files_max = limits.get("files_max", 40)
    if files:
        total_ops = sum(files.values())
        note = detail.get("files_note") or ""
        header = f"## 📁 Fichiers touchés ({len(files)} fichiers, {total_ops} opérations"
        header += f" — {note})" if note else ")"
        L.append(header)
        L.append("")
        ordered = sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))
        for fp, n in ordered[:files_max]:
            count = f" ({n}×)" if n > 1 else ""
            L.append(f"- `{_short(fp)}`{count}")
        if len(ordered) > files_max:
            L.append(f"- … et {len(ordered) - files_max} autres")
        L.append("")

    # -- artifacts (Antigravity)
    artifacts = detail.get("artifacts") or []
    if artifacts:
        L.append("## 📄 Artefacts de plan")
        L.append("")
        for a in artifacts[:6]:
            L.append(f"- {clamp_text(_sanitize(str(a)), 200)}")
        L.append("")

    # -- todos
    todos = detail.get("todos") or []
    todos_max = limits.get("todos_max", 15)
    if todos:
        L.append("## ✅ Todos restants")
        L.append("")
        for t in todos[-todos_max:]:
            status = str(t.get("status", "")).lower()
            box = "x" if status in ("done", "completed", "true", "1") else " "
            L.append(f"- [{box}] {clamp_text(_sanitize(t.get('content', '')), 120)}")
        L.append("")

    # -- resume
    last = _sanitize(detail.get("last_assistant_message") or "")
    if last:
        L.append("## ▶️ Reprise — dernier message de l'agent")
        L.append("")
        L.append(clamp_text(last, limits.get("message_max_chars", 600)))
        L.append("")

    L.append(
        "_Payload généré par session-bridge (index local, 0 token LLM). "
        "Reprends le travail directement à partir de ces informations._"
    )

    # -- alternatives
    if alternatives:
        L.append("")
        L.append("## ℹ️ Autres sessions correspondantes")
        L.append("")
        for alt in alternatives[:5]:
            L.append(
                f"- [{alt.harness}] {_sanitize(alt.title) or 'sans titre'} "
                f"— id: `{alt.session_id}`"
            )

    return "\n".join(L)
