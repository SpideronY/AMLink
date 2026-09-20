#!/usr/bin/env python3
"""AMLink (Agent Memory Link) — universal session bridge MCP server (FastMCP, stdio).

Locally indexes sessions from Codex, OpenCode, ZCode and Google Antigravity
(SQLite / JSONL / protobuf, read-only, zero LLM tokens) and injects a
condensed payload the receiving agent can resume from.

Modes:
  (no argument)      MCP server over stdio
  --search "query"   CLI search (debug)
  --list             list indexed sessions (debug)
  --selftest         validate each adapter against real data
  --detect           print detected paths per harness
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastmcp import FastMCP

from adapters.checkpoint import save_checkpoint
from formatter import format_payload
from indexer import Indexer, load_config

mcp = FastMCP(
    "session-bridge",
    instructions=(
        "Serveur d'index local des sessions des agents de code (Codex, OpenCode, "
        "ZCode, Antigravity). Utilise locate_and_load_session pour retrouver une "
        "session par ID, titre ou 'latest' — jamais de recherche disque."
    ),
)

_indexer: Indexer | None = None


def idx() -> Indexer:
    global _indexer
    if _indexer is None:
        _indexer = Indexer(load_config())
    return _indexer


HARNESS_VALUES = "codex | opencode | zcode | antigravity | checkpoint | auto"


# ----------------------------------------------------------------------
# MCP tools
# ----------------------------------------------------------------------
@mcp.tool()
def locate_and_load_session(
    query: str, harness: str = "auto", detail: str = "standard"
) -> str:
    """Locate a session from another harness and return a condensed,
    ready-to-resume payload (objective, recent exchanges, files touched,
    next step). 100% local search, zero LLM tokens.

    Args:
        query: session ID (partial accepted), exact or partial title,
            context keywords, or "latest" for the most recent session.
        harness: one of codex | opencode | zcode | antigravity |
            checkpoint | auto (default: search everywhere).
        detail: minimal | standard | full (condensation level).

    Returns:
        Condensed Markdown/YAML payload, or search assistance if nothing
        matches.
    """
    i = idx()
    if harness not in ("auto", "all", "codex", "opencode", "zcode", "antigravity", "checkpoint"):
        return (
            f"Erreur : harness invalide {harness!r}. Valeurs acceptées : {HARNESS_VALUES}."
        )
    best, alts = i.search(query, harness)
    if best is None:
        records = i.build_index()
        if harness not in ("auto", "all"):
            records = [r for r in records if r.harness == harness]
        recent = records[:10]
        lines = [
            f"Aucune session ne correspond à {query!r}"
            + (f" (harness {harness})" if harness not in ("auto", "all") else "")
            + ".",
            "",
            "Sessions les plus récentes (appelle à nouveau l'outil avec un de ces titres/IDs) :",
        ]
        for r in recent:
            lines.append(f"- [{r.harness}] {r.title or 'sans titre'} — id: `{r.session_id}`")
        lines.append("")
        lines.append(
            "Astuce : query='latest' renvoie la session la plus récente ; "
            "un ID partiel (ex. 8 premiers caractères) suffit."
        )
        return "\n".join(lines)

    loaded = i.load_detail(best)
    if loaded is None:
        return (
            f"Session trouvée mais le détail n'a pas pu être chargé "
            f"({best.harness} / {best.session_id}). "
            f"Source : {best.source_path or 'inconnue'}."
        )
    limits = i.limits(detail if detail in ("minimal", "standard", "full") else "standard")
    return format_payload(loaded, limits, alternatives=alts)


@mcp.tool()
def list_sessions(
    harness: str = "all", limit: int = 20, project: str = ""
) -> str:
    """List indexed sessions (id, title, project, last activity).
    Useful to pick a session before calling locate_and_load_session.

    Args:
        harness: all | codex | opencode | zcode | antigravity | checkpoint.
        limit: max number of sessions (most recent first).
        project: optional filter by project path (substring).
    """
    i = idx()
    records = i.build_index()
    if harness not in ("all", "auto"):
        records = [r for r in records if r.harness == harness]
    if project:
        pn = project.casefold()
        records = [
            r for r in records if pn in (r.project or "").casefold()
        ]
    records = records[: max(1, min(limit, 100))]
    if not records:
        return "Aucune session indexée" + (f" pour harness={harness}" if harness != "all" else "") + "."
    lines = [f"{len(records)} sessions (les plus récentes d'abord) :", ""]
    for r in records:
        upd = ""
        from adapters.base import iso_from_ms

        upd = iso_from_ms(r.updated_ms or r.created_ms)
        proj = (r.project or "").replace(str(Path.home()), "~")
        lines.append(
            f"- [{r.harness}] {r.title or 'sans titre'}\n"
            f"    id: `{r.session_id}` | projet: {proj or '?'} | MAJ: {upd or '?'}"
        )
    return "\n".join(lines)


@mcp.tool()
def save_active_session(
    title: str,
    summary: str,
    files_touched: list[str] | None = None,
    project: str = "",
    next_steps: str = "",
    source_harness: str = "",
) -> str:
    """Save a checkpoint of the current session (unified checkpoint) so
    another harness can retrieve it via locate_and_load_session.

    Call it before stopping (rate limit reached, end of session).

    Args:
        title: short session title.
        summary: objective + progress state (a few sentences).
        files_touched: paths of modified files.
        project: project path.
        next_steps: what remains to be done / next action.
        source_harness: origin harness (codex, opencode, zcode, antigravity).
    """
    i = idx()
    result = save_checkpoint(
        i.config.get("checkpoints_dir"),
        title=title,
        summary=summary,
        files_touched=files_touched,
        project=project,
        next_steps=next_steps,
        source_harness=source_harness,
    )
    i.refresh()
    return (
        f"Checkpoint enregistré : {result['session_id']}\n"
        f"Fichier : {result['path']}\n"
        f"Retrouvable depuis n'importe quel harness via "
        f"locate_and_load_session(query={result['session_id']!r}) ou query='latest'."
    )


@mcp.tool()
def refresh_index() -> str:
    """Force a rebuild of the session index (e.g. right after a large
    session has just finished)."""
    idx().refresh()
    count = len(idx().build_index(force=True))
    return f"Index reconstruit : {count} sessions ({', '.join(idx().adapter_names())})."


# ----------------------------------------------------------------------
# Debug CLI (outside MCP)
# ----------------------------------------------------------------------
def cli_detect() -> int:
    i = idx()
    print("Chemins détectés (lecture seule) :")
    for name, a in i.adapters.items():
        try:
            status = "OK" if a.available() else "absent"
        except Exception as e:  # pragma: no cover
            status = f"erreur: {e}"
        extra = ""
        for attr in ("home", "db_path", "root", "dir"):
            if hasattr(a, attr):
                try:
                    extra = str(getattr(a, attr)())
                except Exception:
                    pass
                break
        print(f"  {name:<13} {status:<8} {extra}")
    return 0


def cli_list(harness: str, limit: int) -> int:
    print(list_sessions(harness=harness, limit=limit))
    return 0


def cli_search(query: str, harness: str, detail: str) -> int:
    print(locate_and_load_session(query=query, harness=harness, detail=detail))
    return 0


def cli_selftest() -> int:
    i = idx()
    print("SELFTEST session-bridge")
    print("=" * 72)
    failures = 0
    for name, a in i.adapters.items():
        try:
            available = a.available()
        except Exception as e:
            print(f"[{name}] CRASH available(): {e}")
            failures += 1
            continue
        if not available:
            print(f"[{name}] absent (ignoré)")
            continue
        try:
            t0 = __import__("time").perf_counter()
            recs = a.list_sessions()
            t1 = __import__("time").perf_counter()
            print(f"[{name}] {len(recs)} sessions indexées en {t1 - t0:.2f}s")
            if not recs:
                continue
            mains = [r for r in recs if not i._is_subagent(r)] or recs
            d = a.load_detail(mains[0].session_id)
            if d is None:
                print(f"  !! détail introuvable pour {mains[0].session_id}")
                failures += 1
                continue
            payload = format_payload(d, i.limits("standard"))
            print(
                f"  payload: {len(payload)} caractères | "
                f"titre={d['title'][:60]!r} | "
                f"msgs={len(d['last_messages'])} | "
                f"fichiers={len(d['files_touched'])}"
            )
            first_lines = "\n".join(payload.splitlines()[:14])
            print("  " + first_lines.replace("\n", "\n  "))
        except Exception as e:
            print(f"[{name}] ERREUR: {type(e).__name__}: {e}")
            failures += 1
    print("=" * 72)
    total = len(i.build_index())
    if failures:
        print(f"RESULTAT : ÉCHEC ({failures} échec(s)) — index total : {total} sessions")
        return 1
    print(f"RESULTAT : OK — index total : {total} sessions ({', '.join(i.adapter_names())})")
    return 0


def main(argv: list[str]) -> int:
    if "--detect" in argv:
        return cli_detect()
    if "--selftest" in argv:
        return cli_selftest()
    if "--list" in argv:
        harness = "all"
        limit = 20
        if "--harness" in argv:
            harness = argv[argv.index("--harness") + 1]
        if "--limit" in argv:
            limit = int(argv[argv.index("--limit") + 1])
        return cli_list(harness, limit)
    if "--search" in argv:
        query = argv[argv.index("--search") + 1]
        harness = "auto"
        detail = "standard"
        if "--harness" in argv:
            harness = argv[argv.index("--harness") + 1]
        if "--detail" in argv:
            detail = argv[argv.index("--detail") + 1]
        return cli_search(query, harness, detail)
    mcp.run()  # stdio by default
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
