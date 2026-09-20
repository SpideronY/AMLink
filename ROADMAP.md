# AMLink — Registre des améliorations & verdicts

> **AMLink = Agent Memory Link.** Ce fichier conserve la trace des axes
> d'amélioration envisagés et de leur **verdict concluant** (accepté / différé /
> refusé), pour ne jamais re-débattre une décision déjà tranchée.
> Dernière mise à jour : 2026-09-20.

## État actuel (v0.1.0)

Système complet et validé en conditions réelles : 4 adaptateurs (Codex,
OpenCode, ZCode, Antigravity) en lecture seule stricte, recherche ID/titre/
projet + `latest`, payload condensé 3 niveaux, checkpoints, cache mtime,
intégrations installées dans les 4 harnesses (backups `.bak`), directives
anti-gaspillage actives. **194+ sessions indexées, ~0,1 s par harness.**

Décision cadre : **utiliser tel quel pendant 1-2 semaines** avant tout nouvel
axe — c'est l'usage réel qui priorise.

---

## Axes acceptés (à faire, par ordre de valeur)

### 1. Recherche plein-texte dans le contenu des sessions — ✅ ACCEPTÉ (priorité 1)
- **Manque comblé** : rechercher par mots-clés du contexte (« la session où on
  parlait des webhooks Stripe ») alors que le titre ne le contient pas.
- **Implémentation prévue** : `LIKE` SQL sur les parts texte (OpenCode/ZCode),
  scan filtré des rollouts (Codex), fusion dans le scoring existant.
- **Effort estimé** : 1-2 h.
- **Déclencheur** : premier « ça n'a pas retrouvé ma session » en usage réel,
  ou décision anticipée.

### 2. Lignée de sessions (`session_lineage`) — ✅ ACCEPTÉ (priorité 2)
- **Manque comblé** : reconstruire le fil chronologique d'une même tâche à
  travers les harnesses (ex. « audit paywalls » vu chez Codex 18/09,
  Antigravity 18/09, OpenCode 11-15/09, ZCode 20/09).
- **Implémentation prévue** : heuristique projet commun + fenêtres temporelles
  + similarité de titres, sur l'index existant. Transformerait le pont en
  « mémoire continue » des tâches.
- **Effort estimé** : 2-3 h.

### 3. Filtres temporels — ✅ ACCEPTÉ (priorité 3)
- **Manque comblé** : « la session d'hier soir », « celle de la semaine
  dernière sur Restoflow ».
- **Implémentation prévue** : `since`/`before` sur `list_sessions` et
  `locate_and_load_session` ; se combine avec l'axe 2.
- **Effort estimé** : ~30 min.

---

## Axes différés (utiles, pas urgents — à débloquer au besoin)

### 4. Masquage de secrets dans le payload — ⏸ DIFFÉRÉ
Passe de rédaction des motifs évidents (`sk-…`, `AKIA…`, tokens) dans les
sections extraites. ~30 min. **À débloquer si** : partage de payloads ou de
screenshots à des tiers.

### 5. Commande `amlink` globale — ⏸ DIFFÉRÉ
Wrapper CLI installé (`uv tool install`) pour taper `amlink search …` au lieu
de `uv run --project …/AMLink server.py …`. Confort terminal uniquement.
**À débloquer si** : usage CLI fréquent constaté.

### 6. `--doctor` ciblé — ⏸ DIFFÉRÉ
Diagnostic fin quand un adaptateur lâche après une mise à jour de harness
(schema drift). Le `--selftest` couvre déjà ~80 % du besoin.
**À débloquer si** : une mise à jour de harness casse un adaptateur.

---

## Axes refusés (verdict définitif, avec justification)

### 7. Injection du raisonnement du modèle — ❌ REFUSÉ (2026-09-20)
- **Verdict** : le payload actuel est déjà bon ; le raisonnement n'apporte pas
  assez pour sa place.
- **Justification** :
  1. La narration mi-tour est déjà capturée (messages assistant intermédiaires
     — vérifié sur données réelles) : l'intention se formule dans les faits
     peu après être pensée.
  2. Le raisonnement est de la *pensée dépassée* : fausses pistes, dead-ends
     corrigées par le message final — risque de faire suivre une impasse
     abandonnée à l'agent reprenant.
  3. Fenêtre utile étroite (coupure mi-tour avec étape pensée mais ni dite ni
     exécutée) ; l'agent reprenant re-dérive son plan depuis objectif +
     fichiers + todos.
  4. De toute façon illisible chez Codex (`encrypted_content`) et
     Antigravity (protobuf opaque) — l'axe ne couvrirait que OpenCode/ZCode.
- **Réversibilité** : si une reprise réelle rate la suite du travail, ajout
  borné à 1 bloc clampé (~500 car.), activable par config — 5 min de travail.
  Les données existent sur disque (≈4 800 blocs en clair).

### 8. Daemon HTTP partagé + watch inotify — ❌ REFUSÉ (2026-09-20)
Cold-start stdio ~0,5 s et cache mtime suffisent ; complexité réseau et
processus persistant pour un bénéfice nul en usage réel. Le stdio par harness
(zéro port, zéro état) est le bon choix.

### 9. Auto-checkpoint par hooks en fin de tour — ❌ REFUSÉ (2026-09-20)
Les 4 harnesses persistent nativement leurs sessions sur le disque ; les
checkpoints manuels (`save_active_session`) restent un filet de sécurité
volontaire, déclenché par la directive « propose un checkpoint avant
interruption ». Automatiser n'ajouterait que du bruit.

---

## Journal des décisions

| Date | Décision | Motif |
|---|---|---|
| 2026-09-20 | Création du registre ; axes 1-3 acceptés, 4-6 différés, 7-9 refusés | Revue post-livraison, tests réels concluants sur les 4 harnesses |
| 2026-09-20 | Renommage projet : `session-bridge` → **`amlink`** (Agent Memory Link) | Identité du projet ; sans effet sur les intégrations (chemins absolus) |
