# Limites connues de `agentic_v2`

Ce document décrit les limites actuelles du runtime `agentic_v2`, les écarts restants par rapport à la logique OpenClaw visée, et les prochaines priorités de durcissement.

Le but n'est pas de lister des bugs au hasard, mais de clarifier ce qui est:

- déjà robuste;
- encore partiellement implémenté;
- volontairement borné pour des raisons de sécurité trading;
- à traiter ensuite si l'objectif est un runtime encore plus proche d'OpenClaw.

## 1. Résumé exécutif

`agentic_v2` est désormais:

- planifié;
- sessionné;
- traçable;
- partiellement reprenable;
- doté d'un stockage SQL pour sessions et messages.

Mais ce n'est pas encore un clone fonctionnel d'OpenClaw.

Les principaux écarts restants sont:

- les événements runtime sont encore stockés dans `analysis_runs.trace`;
- le planner LLM utilise du JSON piloté par prompt, pas du tool-calling provider natif;
- `sessions_resume` relance un `source_tool`, il ne réattache pas un worker vivant suspendu;
- il n'existe pas encore de table dédiée pour les événements ni de vrai bus persistant;
- les sous-agents ne peuvent pas encore eux-mêmes ouvrir d'autres sous-agents en profondeur arbitraire.

## 2. Limites de boucle agentique

### 2.1 Graphe borné

Le runtime choisit le prochain outil dans un ensemble candidat déterminé par le code.

Conséquence:

- le planner est libre à l'intérieur d'un graphe borné;
- il n'explore pas un espace de capacités ouvert comme un agent généraliste.

Impact:

- plus sûr pour le trading;
- moins flexible qu'un runtime OpenClaw généraliste.

### 2.2 Borne `max_turns`

Le runtime s'arrête à `AGENTIC_RUNTIME_MAX_TURNS`.

Conséquence:

- impossible de boucler indéfiniment;
- possible arrêt prématuré si le graphe devient plus complexe sans adapter la borne.

### 2.3 Pas de plan réécrit librement

Le `plan` est conservé dans l'état, mais il ne se reconstruit pas comme un vrai graphe auto-modifiant multi-objectifs.

## 3. Limites du planner LLM

### 3.1 Pas de tool-calling provider natif

Le planner repose aujourd'hui sur:

- un prompt structuré;
- `chat_json(...)`;
- une extraction/validation JSON;
- un fallback déterministe.

Conséquence:

- le système est robuste;
- mais pas encore au niveau d'une API provider avec `function calling` ou `tool calling` natif.

### 3.2 Planner contraint par le backend

Même si le LLM veut choisir un outil non prévu, le backend le refuse.

C'est intentionnel, mais cela signifie que le planner n'est pas souverain.

### 3.3 Pas de mémoire planner dédiée

Le planner lit l'état courant du runtime, mais n'a pas encore:

- une mémoire d'échec spécifique;
- une stratégie de replanning hiérarchique;
- une politique de coût/budget explicite.

## 4. Limites des sessions et sous-agents

### 4.1 `sessions_resume` ne réattache pas un worker vivant

`sessions_resume` reprend la session logique en relançant le `source_tool` correspondant avec la `session_key`.

Conséquence:

- la reprise est logique;
- ce n'est pas une reprise d'exécution suspendue au milieu d'une coroutine ou d'un process.

### 4.2 `sessions_send` est synchrone

`sessions_send`:

- écrit un message;
- peut éventuellement déclencher immédiatement `sessions_resume`.

Ce n'est pas encore:

- une file de messages indépendante;
- une inbox consommée par un worker session dédié;
- un protocole asynchrone parent/enfant.

### 4.3 Profondeur limitée

Les sessions enfant créées actuellement sont des feuilles:

- `can_spawn = false`
- `control_scope = none`

Conséquence:

- pas encore de hiérarchie profonde de sous-agents.

### 4.4 Pas de lease ou heartbeat par session

Il manque encore:

- un verrou de possession de session;
- un heartbeat;
- un mécanisme d'expiration de worker;
- une reprise de lease.

## 5. Limites de persistance

### 5.1 Événements encore en JSON dans `analysis_runs.trace`

Les sessions et messages sont sortis du `trace`, mais pas les événements runtime.

Conséquence:

- le `trace` peut encore grossir;
- les requêtes d'audit sur les événements sont moins efficaces;
- le websocket lit encore indirectement depuis le blob du run.

### 5.2 Pas d'outbox ni de bus persistant

Le système n'a pas encore:

- table `agent_runtime_events`;
- outbox transactionnelle;
- publication vers Redis Stream, Kafka ou équivalent.

### 5.3 Politique de rétention simple

La rétention actuelle est bornée en nombre:

- `AGENTIC_RUNTIME_EVENT_LIMIT`
- `AGENTIC_RUNTIME_HISTORY_LIMIT`

Il n'y a pas encore de politique:

- par âge;
- par taille disque;
- par niveau de criticité;
- d'archivage long terme.

## 6. Limites de reprise après crash

### 6.1 Reprise logique, pas reprise d'instruction

Le snapshot permet de reprendre la boucle runtime, pas de reprendre l'instruction exacte interrompue.

Exemple:

- si le process tombe pendant un appel outil, on reprend depuis l'état persisté avant ou après l'appel, pas au milieu de l'appel.

### 6.2 Pas de réconciliation d'outils in-flight

Il n'y a pas encore de journal explicite des appels outils "started but not resolved".

Conséquence:

- un crash au mauvais moment peut nécessiter une relance du tool concerné;
- l'idempotence dépend des outils appelés.

### 6.3 Pas de reprise distribuée multi-worker contrôlée

Le projet n'a pas encore de protocole fort empêchant deux workers de reprendre le même run en parallèle.

## 7. Limites API et frontend

### 7.1 Détail riche, liste légère

`GET /runs/{id}` réhydrate le runtime. `GET /runs` reste volontairement léger.

Conséquence:

- bon compromis perf;
- la page liste n'est pas la source de vérité pour les détails runtime avancés.

### 7.2 UI principalement en lecture

Le frontend affiche:

- sessions;
- historique;
- événements.

Mais il ne fournit pas encore une console de pilotage native pour:

- envoyer un message à une session;
- reprendre une session depuis l'UI;
- filtrer les streams d'événements;
- rejouer un tool.

### 7.3 Pas de visualisation graphe native

Les graphes existent dans la documentation, pas encore comme composant interactif dans l'UI.

## 8. Limites de sécurité et de gouvernance

### 8.1 Pas de policy fine par sous-agent

Le runtime applique une policy globale de registre d'outils, pas encore:

- un allowlist par session;
- une policy par profondeur;
- une négociation de capabilities comme OpenClaw.

### 8.2 Pas de sandbox d'outil par session

Contrairement à un runtime OpenClaw plus généraliste, le projet trading n'introduit pas encore:

- sandbox outil par agent;
- droits système distincts par session.

### 8.3 Limites volontaires sur `risk` et `execution`

`risk-manager` et `execution-manager` restent déterministes.

Ce n'est pas un manque technique, c'est une frontière de sécurité assumée.

## 9. Limites de performance et de scalabilité

### 9.1 Réhydratation détail = requêtes SQL supplémentaires

La reconstruction de `trace.agentic_runtime` à la lecture implique:

- chargement des sessions;
- chargement des messages par session.

À faible volume c'est acceptable. À grande échelle, il faudra:

- pagination;
- requêtes batch plus ciblées;
- éventuellement API dédiée runtime.

### 9.2 Croissance des tables runtime

Les nouvelles tables vont grossir avec les runs.

Il manque encore:

- politique de purge;
- archivage;
- index complémentaires selon la charge réelle.

### 9.3 Événements non normalisés

Tant que les événements restent dans `analysis_runs.trace`, le modèle de données n'est pas complètement normalisé.

## 10. Limites de compatibilité

### 10.1 Migration requise

Les environnements déjà existants doivent exécuter:

```bash
cd backend
alembic upgrade head
```

### 10.2 Coexistence legacy / nouveau stockage

Le store garde du fallback legacy sur certaines lectures pour ne pas casser les anciens runs.

Conséquence:

- la compatibilité est meilleure;
- la logique reste un peu plus complexe tant que la migration complète des anciens runs n'est pas faite.

## 11. Écarts précis par rapport à l'objectif OpenClaw

| Sujet | État actuel | Cible plus OpenClaw |
|---|---|---|
| Planner | JSON via prompt | tool-calling natif provider |
| Sessions | sessions logiques persistées | sessions adressables avec worker vivant et lease |
| Messages | `sessions_send` synchrone | vraie messagerie inter-session asynchrone |
| Events | stockés dans `analysis_runs.trace` | table dédiée + bus persistant |
| Capabilities | policy globale registre | policy fine par session / profondeur |
| Sous-agents | profondeur 1 | hiérarchie plus profonde |
| Reprise | snapshot logique | reprise durable avec réconciliation des tools in-flight |

## 12. Ce qui est volontairement hors scope pour l'instant

Points volontairement non traités à ce stade:

- agent généraliste capable d'appeler arbitrairement des outils système;
- suppression des garde-fous trading déterministes;
- sous-agents autonomes illimités;
- UI complète de pilotage runtime.

## 13. Priorités recommandées

### P0. Normaliser les événements runtime

Créer:

- table `agent_runtime_events`;
- lecture websocket depuis cette table ou un outbox;
- pagination/filtrage par `stream`, `session_key`, `turn`.

### P1. Mettre un vrai protocole de session

Ajouter:

- inbox/outbox par session;
- `sessions_send` asynchrone;
- lease/heartbeat;
- reprise contrôlée des sessions actives.

### P2. Passer au tool-calling natif

Faire évoluer le planner vers:

- schémas d'outils natifs provider;
- validation structurée plus forte;
- meilleure séparation entre raisonnement et appel outil.

### P3. Durcir l'exploitation

Ajouter:

- purge et archivage;
- métriques runtime SQL;
- garde-fous concurrence par run;
- écrans UI de diagnostic runtime.

## 14. Conclusion

`agentic_v2` a déjà franchi un cap important:

- boucle agentique réelle;
- sessions et sous-agents;
- reprise d'état;
- stockage SQL dédié pour les sessions et messages.

La prochaine vraie étape de maturité n'est plus "ajouter plus d'agents". C'est:

- sortir aussi les événements du `trace`;
- rendre les sessions vraiment adressables en asynchrone;
- durcir la reprise et la concurrence;
- conserver les barrières déterministes sur le trading réel.
