# Feuille de route

## Objectifs à long terme

Offrir la **programmation orientée agent (AOP)** comme nouveau paradigme de programmation pour organiser la conception et l'implémentation des applications de nouvelle génération propulsées par les LLM.

## Focus actuel (Janvier 2026 - )

### 🎙️ Voice Agent

Les **agents vocaux** sont un domaine sur lequel nous sommes très concentrés, et AgentScope continuera à investir dans cette direction.

AgentScope vise à construire des agents vocaux **prêts pour la production** plutôt que des prototypes de démonstration. Cela signifie que nos agents vocaux :

- Supporteront un déploiement de **qualité production**, incluant une intégration frontend transparente
- Supporteront l'**invocation d'outils**, pas seulement les conversations vocales
- Supporteront les interactions vocales **multi-agents**

#### Feuille de route de développement

Notre stratégie de développement pour les agents vocaux se compose de **trois jalons progressifs** :

1. **Modèles TTS** → 2. **Modèles multimodaux** → 3. **Modèles multimodaux en temps réel**

---

#### Phase 1 : Modèles TTS (Text-to-Speech)

- **Construire l'infrastructure de la classe de base des modèles TTS**
  - Concevoir et implémenter une classe de base unifiée pour les modèles TTS
  - Établir des interfaces standardisées pour l'intégration des modèles TTS

- **Expansion horizontale des API**
  - Supporter les API TTS courantes (par ex., OpenAI TTS, Google TTS, Azure TTS, ElevenLabs, etc.)
  - Assurer un comportement cohérent entre les différents fournisseurs TTS

---

#### Phase 2 : Modèles multimodaux (non temps réel)

- **Doter les agents ReAct du support multimodal**
  - Intégrer les modèles multimodaux (par ex., qwen3-omni, gpt-audio) dans le framework d'agent ReAct existant
  - Supporter l'entrée/sortie audio en mode non temps réel

- **Capacités avancées des agents multimodaux**
  - Permettre l'invocation d'outils au sein de conversations multimodales
  - Supporter les workflows multi-agents avec communication multimodale

---

#### Phase 3 : Modèles multimodaux en temps réel


- **Au-delà du requête-réponse** : Explorer le streaming, la gestion des interruptions et le traitement multimodal concurrent
- **Nouveaux paradigmes de programmation** : Concevoir des modèles de programmation d'agents spécifiquement adaptés aux interactions en temps réel
- **Prêt pour la production** : Assurer des performances à faible latence, la stabilité et la scalabilité pour le déploiement en production

### 🛠️ Agent Skill

Fournir des solutions d'intégration de skills d'agents **prêtes pour la production**.

### 🌐 Expansion de l'écosystème

- **A2UI (Agent-to-UI)** : Permettre des interactions transparentes entre agents et interfaces utilisateur
- **A2A (Agent-to-Agent)** : Améliorer les capacités de communication inter-agents

### 🚀 RL agentique

- Supporter l'utilisation du backend [Tinker](https://tinker-docs.thinkingmachines.ai/) pour affiner les applications d'agents sur des appareils sans GPU.
- Supporter l'affinage des applications d'agents basé sur leur historique d'exécution.
- Intégrer avec AgentScope Runtime pour fournir une meilleure abstraction d'environnement.
- Ajouter plus de tutoriels et d'exemples sur la construction de fonctions de jugement complexes avec l'aide du module d'évaluation.
- Ajouter plus de tutoriels et d'exemples sur la sélection et l'augmentation de données.

### 📈 Qualité du code

Raffinement et amélioration continus de la qualité du code et de la maintenabilité.

# Jalons accomplis

### Feuille de route AgentScope V1.0.0

Nous sommes profondément reconnaissants du soutien continu de la communauté open source qui a accompagné la croissance d'AgentScope. Tout au long de notre parcours, nous avons maintenu la **transparence centrée sur le développeur** comme principe fondamental, ce qui continuera de guider notre développement futur.

Alors que l'écosystème des agents IA évolue rapidement, nous reconnaissons le besoin d'adapter AgentScope pour répondre aux tendances et exigences émergentes. Nous sommes ravis d'annoncer la publication prochaine d'AgentScope v1.0.0, qui marque un virage significatif vers une direction axée sur le déploiement et le développement secondaire. Cette nouvelle version fournira un support complet aux développeurs d'agents avec des capacités de déploiement améliorées et des fonctionnalités pratiques. Plus précisément, la mise à jour inclura :

- ✨Nouvelles fonctionnalités
  - 🛠️ Tool/MCP
    - Support des fonctions outils sync/async
    - Support des fonctions outils en streaming
    - Support de l'exécution parallèle des fonctions outils
    - Support plus flexible du serveur MCP

  - 💾 Memory
    - Amélioration de la mémoire court terme existante
    - Support de la mémoire long terme

  - 🤖 Agent
    - Fourniture d'agents puissants basés sur ReAct prêts à l'emploi

- 👨‍💻 Développement
  - Fourniture d'un AgentScope Studio amélioré avec des composants visuels pour le développement, le tracing et le débogage
  - Fourniture d'un copilote intégré pour le développement/brouillon d'applications AgentScope

- 🔍 Évaluation
  - Fourniture d'un toolkit intégré de benchmarking et d'évaluation pour les agents
  - Support de la visualisation des résultats

- 🏗️ Déploiement
  - Support de l'exécution asynchrone des agents
  - Support de la gestion de session/état
  - Fourniture d'un sandbox pour l'exécution des outils

Restez à l'écoute pour nos notes de version détaillées et la version bêta, qui seront disponibles prochainement. Suivez notre dépôt GitHub et nos canaux officiels pour les dernières mises à jour. Nous sommes impatients de recevoir vos précieux retours et votre soutien continu pour façonner l'avenir d'AgentScope.
