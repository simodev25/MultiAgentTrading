# CHANGELOG de v1.0.0

> ➡️ modification ; ✅ nouvelle fonctionnalité ; ❌ dépréciation

Les changements globaux de v0.x.x à v1.0.0 sont résumés ci-dessous.

## Vue d'ensemble
- ✅ Support de l'exécution asynchrone dans l'ensemble de la bibliothèque
- ✅ Support complet de l'API tools


## ✨Session
- ✅ Support de la gestion automatique de l'état
- ✅ Support de la gestion de l'état au niveau session/application


## ✨Tracing
- ✅ Support du tracing basé sur OpenTelemetry
- ✅ Support des plateformes de tracing tierces, par ex. Arize-Phoenix, Langfuse, etc.


## ✨MCP
- ✅ Support du contrôle au niveau client et au niveau fonction sur MCP via un nouveau module MCP
- ✅ Support de la gestion de session « pay-as-you-go » et persistante
- ✅ Support des protocoles de transport streamable HTTP, SSE et StdIO


## ✨Memory
- ✅ Support de la mémoire long terme via une classe `LongTermMemoryBase`
- ✅ Fourniture d'une implémentation de mémoire long terme basée sur Mem0
- ✅ Support des modes de mémoire long terme statique et contrôlé par l'agent


## Formatter
- ✅ Support de la construction/mise en forme de prompts avec estimation du nombre de tokens
- ✅ Support de l'API tools dans le formatage de prompts multi-agents


## Model
- ❌ Dépréciation de la configuration de modèle, utilisation de l'instanciation explicite d'objets à la place
- ✅ Fourniture d'une nouvelle classe `ModelResponse` pour les réponses structurées de modèles
- ✅ Support de l'invocation asynchrone de modèles
- ✅ Support des modèles de raisonnement
- ✅ Support de toute combinaison streaming/non-streaming, raisonnement/non-raisonnement et API tools


## Agent
- ❌ Dépréciation de `DialogAgent`, `DictDialogAgent` et de la classe d'agent ReAct basée sur les prompts
- ➡️ Exposition des interfaces memory et formatter dans le constructeur de l'agent dans ReActAgent
- ➡️ Unification de la signature des hooks pre- et post-agent
- ✅ Support des hooks pre-/post-reasoning et pre-/post-acting dans la classe ReActAgent
- ✅ Support de l'exécution asynchrone des agents
- ✅ Support de l'interruption de la réponse de l'agent et de la gestion personnalisée des interruptions
- ✅ Support de la gestion automatique de l'état
- ✅ Support des appels d'outils parallèles
- ✅ Support de la mémoire long terme en deux modes dans la classe ReActAgent


## Tool
- ✅ Fourniture d'une classe `Toolkit` plus puissante pour la gestion des outils
- ✅ Fourniture d'une nouvelle classe `ToolResponse` pour les réponses d'outils structurées et multimodales
- ✅ Support de la gestion des outils par groupes
- ✅ Support de la gestion des outils par l'agent lui-même
- ✅ Support du post-traitement des réponses d'outils
- Fonction outil
  - ✅ Support des fonctions async et sync
  - ✅ Support du retour en streaming et non-streaming


## Evaluation
- ✅ Support de l'évaluation orientée agent ReAct
- ✅ Support de l'évaluation distribuée et concurrente basée sur Ray
- ✅ Support de l'analyse statistique sur les résultats d'évaluation


## AgentScope Studio
- ✅ Support du tracing en temps réel
- ✅ Fourniture d'un agent copilote intégré nommé Friday


## Logging
- ❌ Dépréciation de `loguru` et utilisation du module natif Python `logging` à la place


## Distribution
- ❌ Dépréciation temporaire de la fonctionnalité de distribution, un nouveau module de distribution arrive prochainement


## RAG
- ❌ Dépréciation temporaire de la fonctionnalité RAG, un nouveau module RAG arrive prochainement


## Parsers
- ❌ Dépréciation du module parsers


## WebBrowser
- ❌ Dépréciation de la classe `WebBrowser` et passage à la navigation web basée sur MCP
