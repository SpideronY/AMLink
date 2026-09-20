---
name: session-bridge
description: Localiser et reprendre une session d'un autre agent IA (Codex, OpenCode, ZCode, Antigravity) via le serveur MCP session-bridge. À utiliser DÈS QUE l'utilisateur mentionne une session précédente, un ID de session, un titre ou des mots-clés venant d'un autre harness — à la place de toute recherche de fichiers sur le disque.
---

# Session Bridge — reprise de session inter-harness

Le serveur MCP `session-bridge` indexe localement les sessions de **Codex**,
**OpenCode**, **ZCode** et **Antigravity** (SQLite/JSONL en lecture seule).
La recherche et l'extraction sont 100 % locales : **0 token** LLM.

## Règles strictes (obligatoires)

1. **INTERDICTION ABSOLUE** de chercher une session avec des commandes système
   (`find`, `grep`, `rg`, `ls` récursif, scans de disque) ou en fouillant
   `~/.codex`, `~/.local/share/opencode`, `~/.zcode`, `~/.gemini`.
   C'est inutile et coûteux : l'index le fait déjà instantanément.
2. Dès que l'utilisateur mentionne une session précédente, un titre, un ID de
   session, ou « reprends là où … s'est arrêté » (Codex / OpenCode / ZCode /
   Antigravity) → appelle **immédiatement** l'outil MCP
   `locate_and_load_session`. Si la mention est ambiguë, appelle
   `list_sessions` pour départager, ou pose UNE question courte.
3. Dès réception du payload condensé (objectif, derniers échanges, fichiers
   touchés, prochaine étape) → **reprends directement le travail**.
   Pas de relecture des logs, pas de phase de confirmation superflue,
   pas de re-scan des fichiers listés sauf besoin avéré pour continuer.
4. Avant une interruption prévisible (rate limit, fin de session longue),
   propose d'appeler `save_active_session` pour poser un checkpoint
   récupérable depuis n'importe quel autre harness.

## Outils MCP disponibles

| Outil | Usage |
|---|---|
| `locate_and_load_session(query, harness="auto", detail="standard")` | `query` = ID (partiel OK), titre (flou OK), ou `latest`. `harness` ∈ codex \| opencode \| zcode \| antigravity \| checkpoint \| auto. Renvoie le payload condensé. |
| `list_sessions(harness="all", limit=20, project="")` | Liste les sessions indexées (id, titre, projet, MAJ) pour choisir. |
| `save_active_session(title, summary, files_touched, project, next_steps, source_harness)` | Enregistre un checkpoint de la session courante. |
| `refresh_index()` | Force la reconstruction de l'index (après une grosse session). |

## Exemples

- « reprends la session Codex sur l'audit paywalls » →
  `locate_and_load_session(query="audit paywalls", harness="codex")`
- « continue la session 01a0b55b » →
  `locate_and_load_session(query="01a0b55b")` (ID partiel suffisant)
- « reprends là où tu t'étais arrêté » →
  `locate_and_load_session(query="latest")`
- « on avait travaillé sur Restoflow » →
  `list_sessions(project="Restoflow")` puis `locate_and_load_session(...)`

## Dépannage

- Aucun résultat → raffine le titre, ou `list_sessions` pour voir l'index.
- Session très récente non visible → `refresh_index()` (cache 60 s).
- Le payload suffit pour reprendre : les chemins de fichiers qu'il liste
  sont les fichiers réellement modifiés pendant la session.
