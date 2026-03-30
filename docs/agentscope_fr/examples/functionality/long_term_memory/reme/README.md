# ReMe Long-Term Memory dans AgentScope

Cet exemple montre comment :

- Utiliser ReMe (Reflection Memory) pour fournir trois types spécialisés de stockage de mémoire persistante pour les agents AgentScope
- Enregistrer et récupérer des informations personnelles, des trajectoires d'exécution de tâches et des modèles d'utilisation d'outils entre les sessions
- Intégrer la mémoire à long terme avec ReActAgent pour des conversations contextuelles et un apprentissage continu
- Configurer les modèles d'embedding DashScope et les vector stores pour une gestion efficace de la mémoire

## Vue d'ensemble

ReMe (Reflection Memory) fournit trois types de mémoire à long terme pour les agents intelligents :

1. **Personal Memory** (`ReMePersonalLongTermMemory`) - Enregistre et récupère les informations personnelles persistantes, les préférences et les faits concernant les utilisateurs
2. **Task Memory** (`ReMeTaskLongTermMemory`) - Apprend à partir des trajectoires d'exécution de tâches et récupère les expériences passées pertinentes pour des tâches similaires
3. **Tool Memory** (`ReMeToolLongTermMemory`) - Enregistre les résultats d'exécution d'outils et génère des directives d'utilisation pour améliorer l'appel d'outils

## Prérequis

- Python 3.12 ou supérieur
- Clé API DashScope d'Alibaba Cloud (pour les exemples)

## Démarrage rapide

### Installation

```bash
# Install agentscope from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .

# Install required dependencies
pip install reme-ai python-dotenv
```

### Configuration

Configurez votre clé API :

```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```

Ou créez un fichier `.env` :

```bash
DASHSCOPE_API_KEY=YOUR_API_KEY
```

### Exécuter les exemples

```bash
# Personal Memory Example - 5 core interfaces
python personal_memory_example.py

# Task Memory Example - 5 core interfaces
python task_memory_example.py

# Tool Memory Example - Complete workflow with ReActAgent
python tool_memory_example.py
```

> **Note** : Les exemples utilisent les modèles DashScope par défaut. Pour utiliser OpenAI ou d'autres modèles, modifiez l'initialisation du modèle dans le code de l'exemple en conséquence.

## Fonctionnalités clés

- **Trois types de mémoire spécialisés** : Personal, Task et Tool memory pour différents cas d'utilisation
- **Conception à double interface** : Fonctions d'outils (pour l'appel par les agents) et méthodes directes (pour l'utilisation programmatique)
- **Récupération vectorielle** : Recherche sémantique efficace utilisant des modèles d'embedding et des vector stores
- **Architecture async-first** : Support complet async/await pour des opérations non bloquantes
- **Intégration ReActAgent** : Intégration transparente avec le ReActAgent et le Toolkit d'AgentScope
- **Gestion automatique du contexte** : Utilise des gestionnaires de contexte asynchrones pour une gestion appropriée des ressources

## Concepts fondamentaux

### Types de mémoire et leurs cas d'utilisation

| Type de mémoire | Objectif | Quand l'utiliser |
|------------|---------|-------------|
| **Personal Memory** | Stocker les préférences, habitudes et faits personnels des utilisateurs | Profils utilisateurs, assistants personnalisés, contexte utilisateur à long terme |
| **Task Memory** | Apprendre à partir des trajectoires d'exécution de tâches | Résolution de problèmes, débogage, workflows répétitifs, apprentissage à partir des réussites passées |
| **Tool Memory** | Enregistrer les modèles d'utilisation d'outils et générer des directives | Agents utilisant des outils, amélioration de la précision des appels d'outils, évitement des erreurs passées |

### Conception des interfaces

**Personal Memory** et **Task Memory** fournissent **5 interfaces principales** :

1. **`record_to_memory()`** - Fonction d'outil permettant aux agents d'enregistrer des souvenirs (retourne `ToolResponse`)
2. **`retrieve_from_memory()`** - Fonction d'outil permettant aux agents de récupérer des souvenirs (retourne `ToolResponse`)
3. **`record()`** - Méthode directe pour l'enregistrement programmatique (retourne `None`)
4. **`retrieve()`** - Méthode directe pour la récupération programmatique (retourne `str`)
5. **Intégration ReActAgent** - Utilisation de la mémoire avec les paramètres `long_term_memory` et `long_term_memory_mode`

**Tool Memory** fournit **2 interfaces principales** (pas de fonctions d'outils) :

1. **`record()`** - Méthode directe pour enregistrer les résultats d'exécution d'outils (retourne `None`)
2. **`retrieve()`** - Méthode directe pour récupérer les directives d'utilisation d'outils (retourne `str`)

## Exemples d'utilisation

### 1. Personal Memory

**Cas d'utilisation** : Enregistrer et récupérer les préférences, habitudes et informations personnelles des utilisateurs.

```python
import asyncio
import os
from agentscope.memory import ReMePersonalLongTermMemory
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel


async def main():
    # Initialize personal memory
    personal_memory = ReMePersonalLongTermMemory(
        agent_name="Friday",
        user_name="user_123",
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        embedding_model=DashScopeTextEmbedding(
            model_name="text-embedding-v4",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            dimensions=1024,
        ),
    )

    # Use async context manager (required!)
    async with personal_memory:
        # Interface 1: record_to_memory (tool function)
        result = await personal_memory.record_to_memory(
            thinking="User sharing travel preferences",
            content=[
                "I prefer to stay in homestays when traveling to Hangzhou",
                "I like to visit the West Lake in the morning",
                "I enjoy drinking Longjing tea",
            ],
        )

        # Interface 2: retrieve_from_memory (tool function)
        result = await personal_memory.retrieve_from_memory(
            keywords=["Hangzhou travel", "tea preference"],
        )

        # Interface 3: record (direct method)
        await personal_memory.record(
            msgs=[
                Msg(role="user", content="I work as a software engineer", name="user"),
                Msg(role="assistant", content="Got it!", name="assistant"),
            ],
        )

        # Interface 4: retrieve (direct method)
        memories = await personal_memory.retrieve(
            msg=Msg(role="user", content="What do you know about my work?", name="user"),
        )
        print(memories)


asyncio.run(main())
```

**Intégration avec ReActAgent** (Interface 5) :

```python
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

async def use_with_agent():
    personal_memory = ReMePersonalLongTermMemory(...)

    async with personal_memory:
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are Friday with long-term memory. Always record user information and retrieve memories when needed.",
            model=DashScopeChatModel(...),
            formatter=DashScopeChatFormatter(),
            toolkit=Toolkit(),
            memory=InMemoryMemory(),
            long_term_memory=personal_memory,  # Attach personal memory
            long_term_memory_mode="both",  # Enable both record and retrieve tools
        )

        # Agent can now use record_to_memory and retrieve_from_memory as tools
        msg = Msg(role="user", content="I prefer staying in homestays", name="user")
        response = await agent(msg)
```

### 2. Task Memory

**Cas d'utilisation** : Apprendre à partir des trajectoires d'exécution de tâches et récupérer les expériences pertinentes.

```python
from agentscope.memory import ReMeTaskLongTermMemory


async def main():
    # Initialize task memory
    task_memory = ReMeTaskLongTermMemory(
        agent_name="TaskAssistant",
        user_name="task_workspace_123",  # Acts as workspace_id
        model=DashScopeChatModel(...),
        embedding_model=DashScopeTextEmbedding(...),
    )

    async with task_memory:
        # Interface 1: record_to_memory with score
        result = await task_memory.record_to_memory(
            thinking="Recording successful debugging approach",
            content=[
                "For API 404 errors: Check route definition, verify URL path, ensure correct port",
                "Always use linter to catch typos in route paths",
            ],
            score=0.95,  # High score for successful trajectory
        )

        # Interface 2: retrieve_from_memory
        result = await task_memory.retrieve_from_memory(
            keywords=["debugging", "API errors"],
        )

        # Interface 3: record with score in direct method
        await task_memory.record(
            msgs=[
                Msg(role="user", content="I'm getting a 404 error", name="user"),
                Msg(role="assistant", content="Let's check the route path...", name="assistant"),
                Msg(role="user", content="Found the typo!", name="user"),
            ],
            score=0.95,  # Optional score for this trajectory
        )

        # Interface 4: retrieve (direct method)
        experiences = await task_memory.retrieve(
            msg=Msg(role="user", content="How to debug API errors?", name="user"),
        )
        print(experiences)


asyncio.run(main())
```

**Intégration avec ReActAgent** (Interface 5) :

```python
async def use_with_agent():
    task_memory = ReMeTaskLongTermMemory(...)

    async with task_memory:
        agent = ReActAgent(
            name="TaskAssistant",
            sys_prompt="You are a task assistant. Record solutions and retrieve past experiences before solving problems.",
            model=DashScopeChatModel(...),
            formatter=DashScopeChatFormatter(),
            toolkit=Toolkit(),
            memory=InMemoryMemory(),
            long_term_memory=task_memory,
            long_term_memory_mode="both",
        )

        # Agent learns from task executions over time
        msg = Msg(role="user", content="How should I optimize database queries?", name="user")
        response = await agent(msg)
```

### 3. Tool Memory

**Cas d'utilisation** : Enregistrer les résultats d'exécution d'outils et générer des directives d'utilisation pour de meilleurs appels d'outils.

**Workflow complet** :

```python
import json
from datetime import datetime
from agentscope.memory import ReMeToolLongTermMemory
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import Msg, TextBlock


# Step 1: Define tools
async def web_search(query: str, max_results: int = 5) -> ToolResponse:
    """Search the web for information."""
    result = f"Found {max_results} results for query: '{query}'"
    return ToolResponse(content=[TextBlock(type="text", text=result)])


async def main():
    # Initialize tool memory
    tool_memory = ReMeToolLongTermMemory(
        agent_name="ToolBot",
        user_name="tool_workspace_demo",
        model=DashScopeChatModel(...),
        embedding_model=DashScopeTextEmbedding(...),
    )

    async with tool_memory:
        # Step 2: Record tool execution history (accepts JSON strings in msgs)
        tool_result = {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "web_search",
            "input": {"query": "Python asyncio tutorial", "max_results": 10},
            "output": "Found 10 results for query: 'Python asyncio tutorial'",
            "token_cost": 150,
            "success": True,
            "time_cost": 2.3
        }

        # Interface 1: record (accepts JSON strings in message content)
        await tool_memory.record(
            msgs=[Msg(role="assistant", content=json.dumps(tool_result), name="assistant")],
        )

        # Step 3: Retrieve tool guidelines
        # Interface 2: retrieve returns summarized guidelines
        guidelines = await tool_memory.retrieve(
            msg=Msg(role="user", content="web_search", name="user"),
        )

        # Step 4: Inject guidelines into agent system prompt
        toolkit = Toolkit()
        toolkit.register_tool_function(web_search)

        base_prompt = "You are ToolBot, a helpful AI assistant."
        enhanced_prompt = f"{base_prompt}\n\n# Tool Guidelines:\n{guidelines}"

        agent = ReActAgent(
            name="ToolBot",
            sys_prompt=enhanced_prompt,  # Guidelines enhance tool usage
            model=DashScopeChatModel(...),
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
            memory=InMemoryMemory(),
        )

        # Agent now uses tools with learned guidelines
        msg = Msg(role="user", content="Search for Python design patterns", name="user")
        response = await agent(msg)


asyncio.run(main())
```

> **Note** : Tool Memory ne fournit PAS les fonctions d'outils `record_to_memory()` et `retrieve_from_memory()`. Il fournit uniquement les méthodes directes `record()` et `retrieve()`. Tool Memory est conçu pour être utilisé de manière programmatique afin d'enrichir les prompts système des agents, et non comme des outils appelables par les agents.

## Référence API

### Paramètres communs

Tous les types de mémoire partagent ces paramètres d'initialisation :

```python
ReMePersonalLongTermMemory(
    agent_name: str,           # Name of the agent using this memory
    user_name: str,            # User identifier (acts as workspace_id in ReMe)
    model: ModelWrapper,       # LLM for summarization and processing
    embedding_model: EmbeddingWrapper,  # Embedding model for vector retrieval
    vector_store_dir: str = "./memory_vector_store",  # Storage location
)
```

### Spécifications des interfaces

#### Personal Memory

| Interface | Type | Signature | Retour | Description |
|-----------|------|-----------|---------|-------------|
| `record_to_memory` | Fonction d'outil | `(thinking: str, content: list[str])` | `ToolResponse` | Enregistrer des informations personnelles avec un raisonnement |
| `retrieve_from_memory` | Fonction d'outil | `(keywords: list[str], limit: int = 3)` | `ToolResponse` | Récupérer des souvenirs par mots-clés |
| `record` | Méthode directe | `(msgs: list[Msg])` | `None` | Enregistrer des conversations de messages |
| `retrieve` | Méthode directe | `(msg: Msg, top_k: int = 3)` | `str` | Récupération basée sur les requêtes |

**Paramètres** :
- `thinking` : Raisonnement sur ce qu'il faut enregistrer
- `content` : Liste de chaînes de caractères à mémoriser
- `keywords` : Mots-clés de recherche
- `limit` : Résultats par mot-clé (fonction d'outil, défaut : 3)
- `top_k` : Nombre total de résultats à récupérer (méthode directe, défaut : 3)

#### Task Memory

| Interface | Type | Signature | Retour | Description |
|-----------|------|-----------|---------|-------------|
| `record_to_memory` | Fonction d'outil | `(thinking: str, content: list[str], score: float = 1.0)` | `ToolResponse` | Enregistrer une trajectoire de tâche avec un score |
| `retrieve_from_memory` | Fonction d'outil | `(keywords: list[str], top_k: int = 5)` | `ToolResponse` | Récupérer des expériences par mots-clés |
| `record` | Méthode directe | `(msgs: list[Msg], score: float = 1.0)` | `None` | Enregistrer des conversations de messages avec un score |
| `retrieve` | Méthode directe | `(msg: Msg, top_k: int = 5)` | `str` | Récupération d'expériences basée sur les requêtes |

**Paramètres** :
- `thinking` : Raisonnement sur l'exécution de la tâche
- `content` : Informations et observations sur l'exécution de la tâche
- `score` : Score de réussite de la trajectoire (0.0-1.0, défaut : 1.0)
- `keywords` : Mots-clés de recherche (ex. : type de tâche, domaine)
- `top_k` : Nombre de résultats à récupérer (défaut : 5)

#### Tool Memory

| Interface | Type | Signature | Retour | Description |
|-----------|------|-----------|---------|-------------|
| `record` | Méthode directe | `(msgs: list[Msg])` | `None` | Enregistrer les résultats d'outils sous forme de messages (format JSON) |
| `retrieve` | Méthode directe | `(msg: Msg)` | `str` | Récupérer les directives pour les outils |

**Paramètres** :
- `msgs` : Liste de messages dont le `content` contient des chaînes JSON avec les métadonnées d'exécution des outils :
  - `create_time` : Horodatage (`"%Y-%m-%d %H:%M:%S"`)
  - `tool_name` : Identifiant de l'outil
  - `input` : Paramètres utilisés (dict)
  - `output` : Résultat de l'exécution (str)
  - `token_cost` : Utilisation de tokens (int)
  - `success` : Statut de l'exécution (bool)
  - `time_cost` : Durée en secondes (float)
- `msg` : Message contenant le nom de l'outil pour lequel récupérer les directives
- **Note** : Tool Memory ne fournit PAS de fonctions d'outils (`record_to_memory` et `retrieve_from_memory`). Il fournit uniquement des méthodes directes pour une utilisation programmatique.

### Modes d'intégration ReActAgent

Lors de l'attachement de **Personal Memory** ou **Task Memory** à ReActAgent, utilisez le paramètre `long_term_memory_mode` :

```python
agent = ReActAgent(
    name="Assistant",
    long_term_memory=memory,  # ReMePersonalLongTermMemory or ReMeTaskLongTermMemory
    long_term_memory_mode="both",  # Options: "record", "retrieve", "both"
    # ... other parameters
)
```

**Modes** :
- `"record"` : Ajoute uniquement l'outil `record_to_memory` à l'agent
- `"retrieve"` : Ajoute uniquement l'outil `retrieve_from_memory` à l'agent
- `"both"` : Ajoute les deux outils (recommandé pour la plupart des cas d'utilisation)

> **Note** : Tool Memory ne prend PAS en charge l'intégration ReActAgent avec des fonctions d'outils. Utilisez Tool Memory de manière programmatique pour enrichir les prompts système comme montré dans l'exemple Tool Memory.

### Gestionnaire de contexte asynchrone (obligatoire !)

Tous les types de mémoire ReMe **doivent** être utilisés avec des gestionnaires de contexte asynchrones :

```python
async with long_term_memory:
    # All memory operations must be within this context
    await long_term_memory.record(msgs=[...])
    result = await long_term_memory.retrieve(msg=...)
```

Cela garantit :
- L'initialisation correcte du backend ReMe
- Le nettoyage des ressources après les opérations
- La gestion des connexions au vector store

### Configuration personnalisée

```python
from agentscope.memory import ReMePersonalLongTermMemory

# Custom storage location and models
memory = ReMePersonalLongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    model=your_custom_model,  # Any AgentScope-compatible LLM
    embedding_model=your_embedding,  # Any AgentScope-compatible embedding model
    vector_store_dir="./custom_path",  # Custom storage directory
)
```

## Aperçu des fichiers d'exemple

### `personal_memory_example.py`

Démontre les **5 interfaces principales** pour la mémoire personnelle :

1. **`record_to_memory()`** - Enregistrer les préférences utilisateur via une fonction d'outil
2. **`retrieve_from_memory()`** - Rechercher des souvenirs par mots-clés via une fonction d'outil
3. **`record()`** - Enregistrement direct de conversations de messages
4. **`retrieve()`** - Récupération directe basée sur les requêtes
5. **Intégration ReActAgent** - L'agent utilise les outils de mémoire de manière autonome

**Fonctionnalités clés** :
- Enregistrement de préférences de voyage, d'habitudes de travail et d'informations personnelles
- Récupération par mots-clés et par requêtes
- Directives de prompt système pour l'utilisation de la mémoire par l'agent
- Appel automatique des outils de mémoire par ReActAgent

### `task_memory_example.py`

Démontre les **5 interfaces principales** pour la mémoire de tâches :

1. **`record_to_memory()`** - Enregistrer des expériences de tâches avec des scores
2. **`retrieve_from_memory()`** - Récupérer des expériences pertinentes par mots-clés
3. **`record()`** - Enregistrement direct avec des scores de trajectoire
4. **`retrieve()`** - Récupération directe d'expériences
5. **Intégration ReActAgent** - L'agent apprend des exécutions de tâches passées

**Fonctionnalités clés** :
- Enregistrement d'expériences de planification de projets, de débogage et de développement
- Évaluation de trajectoire basée sur les scores (0.0-1.0)
- Apprentissage à partir des tentatives réussies et échouées
- Amélioration continue grâce à la récupération d'expériences

### `tool_memory_example.py`

Démontre le **workflow complet** pour la mémoire d'outils :

1. **Mock tools** - Définir et enregistrer des outils dans le Toolkit
2. **Record tool history** - Stocker les résultats d'exécution avec des métadonnées via `record()`
3. **Retrieve guidelines** - Obtenir des directives d'utilisation résumées via `retrieve()`
4. **Enhance agent prompt** - Injecter les directives dans le prompt système
5. **Use ReActAgent** - L'agent utilise les outils avec les directives apprises

**Fonctionnalités clés** :
- Enregistrement d'exécution d'outils au format JSON via la méthode directe `record()`
- Génération automatique de directives par résumé
- Récupération de directives multi-outils via la méthode directe `retrieve()`
- Enrichissement du prompt système pour une meilleure utilisation des outils
- **Note** : Tool Memory ne fournit PAS de fonctions d'outils appelables par les agents

## Architecture

### Hiérarchie d'héritage

```
ReMeLongTermMemoryBase (abstract base)
├── ReMePersonalLongTermMemory
├── ReMeTaskLongTermMemory
└── ReMeToolLongTermMemory
```

**`ReMeLongTermMemoryBase`** fournit :
- Intégration avec `ReMeApp` de la bibliothèque ReMe
- Implémentation du gestionnaire de contexte asynchrone
- Définitions d'interfaces communes
- Gestion du vector store et des embeddings

### Stockage de la mémoire

- **Emplacement** : `./memory_vector_store/` (configurable)
- **Isolation** : Chaque `user_name` maintient un stockage séparé
- **Persistance** : Les souvenirs persistent entre les sessions
- **Format** : Embeddings vectoriels avec métadonnées

## Bonnes pratiques

### 1. Conception du prompt système

Pour les agents avec mémoire à long terme, spécifiez clairement quand enregistrer et récupérer :

```python
sys_prompt = """
You are an assistant with long-term memory.

Recording Guidelines:
- Record when users share personal information, preferences, or important facts
- Record successful task execution approaches and solutions
- Record tool execution results with detailed metadata

Retrieval Guidelines:
- ALWAYS retrieve before answering questions about past information
- Retrieve when dealing with similar tasks to past executions
- Check tool guidelines before using tools
"""
```

### 2. Attribution des scores (Task Memory)

Utilisez des scores significatifs pour prioriser les expériences :

```python
# Successful trajectory
await task_memory.record_to_memory(..., score=0.95)

# Partially successful
await task_memory.record_to_memory(..., score=0.6)

# Failed trajectory (still useful to learn from)
await task_memory.record_to_memory(..., score=0.2)
```

### 3. Workflow de Tool Memory

Suivez ce schéma pour la mémoire d'outils :

```
1. Execute tool → 2. Record result → 3. Trigger summarization → 4. Retrieve guidelines → 5. Use in agent
```

## Dépannage

### Problèmes courants

**Problème** : `RuntimeError: Memory not initialized`
- **Solution** : Utilisez toujours le gestionnaire de contexte `async with memory:`

**Problème** : Aucun souvenir récupéré
- **Solution** : Assurez-vous d'avoir enregistré des souvenirs au préalable et vérifiez que `user_name` correspond

**Problème** : Tool memory ne génère pas de directives
- **Solution** : Enregistrez plusieurs exécutions d'outils pour déclencher la synthèse

**Problème** : L'agent n'utilise pas les outils de mémoire
- **Solution** : Vérifiez que `long_term_memory_mode="both"` et que le prompt système encourage l'utilisation de la mémoire

## Références

- [ReMe Library](https://github.com/modelscope/ReMe) - Implémentation principale de la mémoire
- [AgentScope Documentation](https://github.com/modelscope/agentscope) - Documentation du framework
- [DashScope API](https://dashscope.aliyun.com/) - API de modèles pour les exemples
