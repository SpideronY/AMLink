#!/usr/bin/env python3
"""Install AMLink (session-bridge) integrations into the 4 harnesses.

Actions (idempotent; every modified file is backed up as .bak on the
first run):
- Codex       : ~/.codex/config.toml                    -> [mcp_servers.session_bridge]
- OpenCode    : ~/.config/opencode/opencode.jsonc       -> mcp["session-bridge"]
- ZCode       : ~/.zcode/cli/config.json                -> mcp.servers["session-bridge"]
- Antigravity : ~/.gemini/config/mcp_config.json        -> mcpServers["session-bridge"]
- ZCode skill : ~/.agents/skills/session-bridge/SKILL.md (copied from the repo)
- Directives  : ~/.codex/AGENTS.md, ~/.config/opencode/AGENTS.md,
                ~/.gemini/AGENTS.md (marked section)

Usage:
  uv run install.py             # install everything
  uv run install.py --detect    # show detected targets, change nothing
  uv run install.py --uninstall # remove the added entries/sections
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "server.py"
VENV_PY = ROOT / ".venv" / (
    "Scripts/python.exe" if sys.platform.startswith("win") else "bin/python"
)
SKILL_SRC = ROOT / "skills" / "session-bridge" / "SKILL.md"

JSON_NAME = "session-bridge"
TOML_NAME = "session_bridge"

BEGIN_MARK = "<!-- BEGIN session-bridge -->"
END_MARK = "<!-- END session-bridge -->"

DIRECTIVE_MD = f"""{BEGIN_MARK}
## Session Bridge — reprise de session inter-harness (RÈGLES OBLIGATOIRES)

Le serveur MCP `session-bridge` indexe localement toutes les sessions de
Codex / OpenCode / ZCode / Antigravity (recherche 100 % locale, 0 token).

1. **INTERDICTION ABSOLUE** de chercher une session précédente avec des
   commandes système (`find`, `grep`, `rg`, `ls` récursif, scan de disque)
   ou en fouillant `~/.codex`, `~/.local/share/opencode`, `~/.zcode`,
   `~/.gemini` : l'index le fait déjà, instantanément et gratuitement.
2. Dès que l'utilisateur mentionne une session précédente, un ID de session,
   un titre, ou « reprends là où … s'est arrêté » (Codex / OpenCode / ZCode /
   Antigravity) → appelle **immédiatement** l'outil MCP
   `locate_and_load_session` avec `query` = (ID | titre | `latest`) et
   `harness` si connu. Si la mention est ambiguë : `list_sessions` d'abord,
   ou UNE question courte.
3. Dès réception du payload condensé (objectif, derniers échanges, fichiers
   touchés, prochaine étape) → **reprends directement le travail**. Pas de
   relecture des logs, pas de confirmation superflue, pas de re-scan des
   fichiers listés sauf besoin avéré pour continuer.
4. Avant une interruption prévisible (rate limit atteint, fin de session
   longue), propose d'appeler `save_active_session` pour poser un checkpoint
   récupérable depuis n'importe quel harness.
{END_MARK}"""


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def backup(path: Path) -> bool:
    """Back up as .bak on the first run only (original state preserved)."""
    if not path.exists():
        return False
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        return False
    shutil.copy2(path, bak)
    return True


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments outside strings (JSONC -> JSON)."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def load_jsonc(path: Path) -> dict:
    return json.loads(strip_jsonc(path.read_text(encoding="utf-8")))


def dump_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def replace_marked_section(path: Path, section: str) -> None:
    """Insert/replace the marked section in an AGENTS.md (created if missing)."""
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK), re.S)
    if pattern.search(current):
        updated = pattern.sub(section, current)
    else:
        updated = current.rstrip("\n") + ("\n\n" if current.strip() else "") + section + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


def remove_marked_section(path: Path) -> None:
    if not path.exists():
        return
    current = path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK) + r"\n?", re.S)
    updated = pattern.sub("", current).strip("\n")
    if updated:
        path.write_text(updated + "\n", encoding="utf-8")
    else:
        path.unlink()


# ----------------------------------------------------------------------
# integrations
# ----------------------------------------------------------------------
def entry_json() -> dict:
    return {
        "type": "local",
        "command": [str(VENV_PY), str(SERVER)],
        "enabled": True,
    }


def install_codex(uninstall: bool = False) -> str:
    home = Path.home() / ".codex"
    cfg = home / "config.toml"
    if not home.exists():
        return "⚠️  ~/.codex absent (Codex non installé ?) — ignoré"
    block_re = re.compile(
        r"(?ms)^\[mcp_servers\." + TOML_NAME + r"\]\n.*?(?=^\[|\Z)"
    )
    text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    if uninstall:
        if not cfg.exists():
            return "— rien à retirer"
        backup(cfg)
        cfg.write_text(block_re.sub("", text).rstrip("\n") + "\n", encoding="utf-8")
        return "✅ entrée retirée de ~/.codex/config.toml"
    backup(cfg)
    new_block = (
        f"\n[mcp_servers.{TOML_NAME}]\n"
        f'command = "{VENV_PY}"\n'
        f'args = ["{SERVER}"]\n'
        f"startup_timeout_sec = 30\n"
    )
    if block_re.search(text):
        text = block_re.sub(new_block.lstrip("\n"), text)
        msg = "✅ ~/.codex/config.toml mis à jour"
    else:
        text = text.rstrip("\n") + "\n" + new_block
        msg = "✅ ~/.codex/config.toml : entrée ajoutée"
    cfg.write_text(text, encoding="utf-8")
    tomllib.loads(cfg.read_text(encoding="utf-8"))  # syntax check
    return msg


def install_opencode(uninstall: bool = False) -> str:
    cfg = Path.home() / ".config" / "opencode" / "opencode.jsonc"
    if not cfg.parent.exists():
        return "⚠️  ~/.config/opencode absent (OpenCode non installé ?) — ignoré"
    if uninstall:
        if not cfg.exists():
            return "— rien à retirer"
        backup(cfg)
        data = load_jsonc(cfg)
        (data.get("mcp") or {}).pop(JSON_NAME, None)
        dump_json(cfg, data)
        return "✅ entrée retirée de opencode.jsonc"
    if not cfg.exists():
        dump_json(cfg, {"mcp": {JSON_NAME: entry_json()}})
        return "✅ opencode.jsonc créé avec l'entrée session-bridge"
    backup(cfg)
    data = load_jsonc(cfg)
    data.setdefault("mcp", {})[JSON_NAME] = entry_json()
    dump_json(cfg, data)
    json.loads(cfg.read_text(encoding="utf-8"))  # syntax check
    return (
        "✅ opencode.jsonc mis à jour "
        "(⚠️ commentaires // éventuels supprimés — .bak conservé)"
    )


def install_zcode(uninstall: bool = False) -> str:
    cfg = Path.home() / ".zcode" / "cli" / "config.json"
    if not cfg.parent.exists():
        return "⚠️  ~/.zcode/cli absent (ZCode non installé ?) — ignoré"
    if not cfg.exists():
        if uninstall:
            return "— rien à retirer"
        dump_json(cfg, {"mcp": {"servers": {JSON_NAME: entry_zcode()}}})
        return "✅ ~/.zcode/cli/config.json créé avec l'entrée session-bridge"
    backup(cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    servers = data.setdefault("mcp", {}).setdefault("servers", {})
    if uninstall:
        servers.pop(JSON_NAME, None)
        msg = "✅ entrée retirée de ~/.zcode/cli/config.json"
    else:
        servers[JSON_NAME] = entry_zcode()
        msg = "✅ ~/.zcode/cli/config.json : entrée ajoutée (contenu existant préservé)"
    dump_json(cfg, data)
    json.loads(cfg.read_text(encoding="utf-8"))  # syntax check
    return msg


def entry_zcode() -> dict:
    return {
        "command": str(VENV_PY),
        "args": [str(SERVER)],
        "enabled": True,
        "timeoutMs": 30000,
    }


def install_antigravity(uninstall: bool = False) -> str:
    cfg = Path.home() / ".gemini" / "config" / "mcp_config.json"
    if not cfg.parent.parent.exists():
        return "⚠️  ~/.gemini absent (Antigravity non installé ?) — ignoré"
    if not cfg.exists():
        if uninstall:
            return "— rien à retirer"
        dump_json(cfg, {"mcpServers": {JSON_NAME: {"command": str(VENV_PY), "args": [str(SERVER)]}}})
        return "✅ ~/.gemini/config/mcp_config.json créé avec l'entrée session-bridge"
    backup(cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    servers = data.setdefault("mcpServers", {})
    if uninstall:
        servers.pop(JSON_NAME, None)
        msg = "✅ entrée retirée de mcp_config.json"
    else:
        servers[JSON_NAME] = {"command": str(VENV_PY), "args": [str(SERVER)]}
        msg = "✅ mcp_config.json : entrée ajoutée"
    dump_json(cfg, data)
    json.loads(cfg.read_text(encoding="utf-8"))  # syntax check
    return msg


def install_skill(uninstall: bool = False) -> str:
    dst = Path.home() / ".agents" / "skills" / "session-bridge" / "SKILL.md"
    if uninstall:
        if dst.parent.exists():
            shutil.rmtree(dst.parent)
            return "✅ skill ~/.agents/skills/session-bridge retiré"
        return "— rien à retirer"
    if not SKILL_SRC.exists():
        return "⚠️ skills/session-bridge/SKILL.md introuvable dans le projet"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL_SRC, dst)
    return f"✅ skill ZCode installé : {dst}"


def install_directives(uninstall: bool = False) -> list[str]:
    targets = [
        ("Codex", Path.home() / ".codex" / "AGENTS.md"),
        ("OpenCode", Path.home() / ".config" / "opencode" / "AGENTS.md"),
        ("Antigravity", Path.home() / ".gemini" / "AGENTS.md"),
    ]
    out = []
    for name, path in targets:
        if uninstall:
            remove_marked_section(path)
            out.append(f"✅ directive retirée de {path}")
            continue
        if path.exists():
            backup(path)
        replace_marked_section(path, DIRECTIVE_MD)
        out.append(f"✅ directive ajoutée à {path}")
    return out


# ----------------------------------------------------------------------
# detect / main
# ----------------------------------------------------------------------
def detect() -> int:
    print("Détection des cibles d'installation :\n")
    print(f"  venv python : {VENV_PY} {'OK' if VENV_PY.exists() else 'ABSENT (uv sync ?)'}")
    print(f"  serveur     : {SERVER} {'OK' if SERVER.exists() else 'ABSENT'}")
    print(f"  skill src   : {SKILL_SRC} {'OK' if SKILL_SRC.exists() else 'ABSENT'}\n")
    checks = [
        ("Codex config.toml", Path.home() / ".codex" / "config.toml"),
        ("OpenCode opencode.jsonc", Path.home() / ".config" / "opencode" / "opencode.jsonc"),
        ("ZCode config.json", Path.home() / ".zcode" / "cli" / "config.json"),
        ("Antigravity mcp_config.json", Path.home() / ".gemini" / "config" / "mcp_config.json"),
        ("Skill ZCode", Path.home() / ".agents" / "skills" / "session-bridge" / "SKILL.md"),
    ]
    for name, p in checks:
        print(f"  {'OK ' if p.exists() else '—  '} {name}: {p}")
    print("\nChemins de données indexés (lecture seule) : voir `uv run server.py --detect`.")
    return 0


def main(argv: list[str]) -> int:
    if "--detect" in argv:
        return detect()
    uninstall = "--uninstall" in argv

    print(f"{'Désinstallation' if uninstall else 'Installation'} session-bridge\n")
    if not uninstall:
        if not VENV_PY.exists():
            print("❌ venv introuvable — lance d'abord : uv sync")
            return 1
        if not SERVER.exists():
            print("❌ server.py introuvable")
            return 1

    results: list[str] = []
    results.append(install_codex(uninstall))
    results.append(install_opencode(uninstall))
    results.append(install_zcode(uninstall))
    results.append(install_antigravity(uninstall))
    results.append(install_skill(uninstall))
    results.extend(install_directives(uninstall))
    for r in results:
        print(" " + r)

    if not uninstall:
        print("\nProchaines étapes :")
        print("  1. relance chaque harness pour charger le serveur MCP")
        print("  2. vérifie      : uv run server.py --selftest")
        print("  3. teste dans un agent : « reprends la session latest »")
    print("\nSauvegardes .bak conservées à côté de chaque fichier modifié.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
