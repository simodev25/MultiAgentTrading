# Mémoire à long terme Mem0 dans AgentScope

Cet exemple démontre comment

- utiliser Mem0LongTermMemory pour fournir un stockage de mémoire sémantique persistant aux agents AgentScope,
- enregistrer et récupérer l'historique des conversations et les préférences utilisateur entre les sessions,
- intégrer la mémoire à long terme avec les agents ReAct pour des conversations contextuelles, et
- configurer les modèles d'embedding DashScope et le vector store Qdrant pour la gestion de la mémoire.

## Prérequis

- Python 3.10 ou supérieur
- Clé API DashScope d'Alibaba Cloud


## Démarrage rapide

Installez agentscope et assurez-vous d'avoir une clé API DashScope valide dans vos variables d'environnement.

> Note : L'exemple est construit avec le modèle de chat DashScope et le modèle d'embedding. Si vous souhaitez utiliser les modèles OpenAI à la place,
> modifiez l'initialisation du modèle dans le code de l'exemple en conséquence.

```bash
# Install agentscope from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
# Install dependencies
pip install mem0ai
```

Configurez votre clé API :

```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```

Exécutez l'exemple :

```bash
python memory_example.py
```

L'exemple va :
1. Initialiser une instance Mem0LongTermMemory avec les modèles DashScope et le vector store Qdrant
2. Enregistrer une conversation basique dans la mémoire à long terme
3. Récupérer des souvenirs en utilisant la recherche sémantique
4. Démontrer l'intégration de ReAct agent avec la mémoire à long terme pour stocker et récupérer les préférences utilisateur

## Fonctionnalités clés

- **Stockage basé sur les vecteurs** : Utilise la base de données vectorielle Qdrant pour une recherche et récupération sémantique efficace
- **Configuration flexible** : Support de multiples modèles d'embedding (OpenAI, DashScope) et vector stores
- **Opérations asynchrones** : Support complet de l'async pour des opérations de mémoire non bloquantes
- **Intégration ReAct Agent** : Intégration transparente avec le ReActAgent et le système d'outils d'AgentScope

## Utilisation de base

### Initialiser la mémoire

```python
import os
from agentscope.memory import Mem0LongTermMemory
from agentscope.model import DashScopeChatModel
from agentscope.embedding import DashScopeTextEmbedding
from mem0.vector_stores.configs import VectorStoreConfig

# Initialize with DashScope models and Qdrant vector store
long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    model=DashScopeChatModel(
        model_name="qwen-max-latest",
        api_key=os.environ.get("DASHSCOPE_API_KEY")
    ),
    embedding_model=DashScopeTextEmbedding(
        model_name="text-embedding-v3",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        dimensions=1024
    ),
    vector_store_config=VectorStoreConfig(
        provider="qdrant",
        config={
            "on_disk": True,
            "path": "./qdrant_data",  # Your customized storage path
            "embedding_model_dims": 1024
        }
    )
)
```

> **Important** : Si vous changez de modèle d'embedding ou modifiez `embedding_model_dims`, vous devez soit définir un nouveau chemin de stockage, soit supprimer les fichiers de base de données existants. Sinon, une erreur de non-correspondance de dimensions se produira.

### Intégrer avec ReAct Agent

```python
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

# Create a ReAct agent with long-term memory
toolkit = Toolkit()
agent = ReActAgent(
    name="Friday",
    sys_prompt=(
        "You are a helpful assistant named Friday. "
        "If you think there is relevant information about "
        "the user's preferences, you can record it to long-term "
        "memory using the tool `record_to_memory`. "
        "If you need to retrieve information from long-term "
        "memory, use the tool `retrieve_from_memory`."
    ),
    model=DashScopeChatModel(
        model_name="qwen-max-latest",
        api_key=os.environ.get("DASHSCOPE_API_KEY")
    ),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
    long_term_memory=long_term_memory,
    long_term_memory_mode="both"
)

# Use the agent
msg = Msg(
    role="user",
    content="When I travel to Hangzhou, I prefer to stay in a homestay",
    name="user"
)
response = await agent(msg)
```

## Configuration avancée

Vous pouvez personnaliser la configuration mem0 en définissant directement :

```python
long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    mem0_config=your_mem0_config  # Pass your custom mem0 configuration
)
```

Pour plus d'options de configuration, consultez la [documentation mem0](https://github.com/mem0ai/mem0).

## Contenu de l'exemple

Le fichier `memory_example.py` démontre :

1. **Enregistrement basique de mémoire** : Enregistrer les conversations utilisateur dans la mémoire à long terme
2. **Récupération de mémoire** : Rechercher les souvenirs stockés en utilisant la similarité sémantique
3. **Intégration ReAct Agent** : Utiliser la mémoire à long terme avec les agents ReAct pour stocker et récupérer automatiquement les préférences utilisateur

## Références

- [Documentation mem0](https://github.com/mem0ai/mem0)
- [Base de données vectorielle Qdrant](https://qdrant.tech/)
