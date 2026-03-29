# MemoryWithCompress

- [ ] TODO: Le module de mémoire avec compression sera ajouté à la bibliothèque agentscope dans le futur.

## Vue d'ensemble

MemoryWithCompress est un système de gestion de mémoire conçu pour le `ReActAgent` d'AgentScope. Il compresse automatiquement l'historique des conversations lorsque la taille de la mémoire dépasse une limite de tokens spécifiée, en utilisant un Large Language Model (LLM) pour créer des résumés concis qui préservent les informations clés. Cela permet aux agents de maintenir le contexte sur de longues conversations tout en respectant les contraintes de tokens.

Le système maintient deux mécanismes de stockage distincts :
- **`chat_history_storage`** : Stocke l'historique complet et non modifié des conversations (utilise l'interface `MessageStorageBase`)
- **`memory_storage`** : Stocke les messages qui peuvent être compressés lorsque les limites de tokens sont dépassées (utilise l'interface `MessageStorageBase`)

Les deux mécanismes de stockage sont abstraits via l'interface `MessageStorageBase`, permettant des backends de stockage flexibles. Par défaut, `InMemoryMessageStorage` est utilisé pour les deux.

## Fonctionnalités principales

### Compression automatique de la mémoire
- **Déclenchement basé sur les tokens** : Compresse automatiquement la mémoire lorsque le nombre total de tokens dépasse `max_token`
- **Résumé par LLM** : Utilise un LLM pour compresser intelligemment l'historique des conversations tout en préservant les informations essentielles
- **Sortie structurée** : Utilise des schémas Pydantic pour garantir un format de compression cohérent

### Système de stockage double
- **Historique complet** : Conserve les messages originaux non modifiés dans `_chat_history` pour référence
- **Mémoire compressée** : Stocke les messages potentiellement compressés dans `_memory` pour une gestion efficace du contexte

### Gestion flexible de la mémoire
- **Support du filtrage** : Fournit le paramètre `filter_func` pour un filtrage personnalisé de la mémoire
- **Récupération des N derniers** : Prend en charge la récupération des N messages les plus récents uniquement
- **Persistance de l'état** : Inclut les méthodes `state_dict()` et `load_state_dict()` pour sauvegarder et charger l'état de la mémoire
- **Abstraction du stockage** : Utilise l'interface `MessageStorageBase` pour des backends de stockage flexibles
- **Déclencheurs de compression** : Prend en charge les fonctions de déclenchement basées sur les tokens et personnalisées pour la compression
- **Contrôle du moment de compression** : Compression configurable à l'ajout (`compression_on_add`) et à la récupération (`compression_on_get`)

## Structure des fichiers

```
memory_with_compression/
├── README.md                   # Ce fichier de documentation
├── main.py                     # Exemple démontrant l'utilisation de MemoryWithCompress
├── _memory_with_compress.py    # Implémentation principale de MemoryWithCompress
├── _memory_storage.py          # Couche d'abstraction du stockage (MessageStorageBase, InMemoryMessageStorage)
├── _mc_utils.py                # Fonctions utilitaires (formatage, comptage de tokens, schéma de compression)

```

## Prérequis

### Cloner le dépôt AgentScope
Cet exemple dépend d'AgentScope. Veuillez cloner le dépôt complet sur votre machine locale.

### Installer les dépendances
**Recommandé** : Python 3.10+

Installez les dépendances requises :
```bash
pip install agentscope
```

### Clés API
Cet exemple utilise les API DashScope par défaut. Vous devez définir votre clé API comme variable d'environnement :
```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```

Vous pouvez facilement passer à d'autres modèles en modifiant la configuration dans `main.py`.

## Fonctionnement

### 1. Flux d'ajout en mémoire
1. **Entrée de message** : Les nouveaux messages sont ajoutés via la méthode asynchrone `add()`
2. **Double stockage** : Les messages sont copiés en profondeur et ajoutés à la fois dans `chat_history_storage` et `memory_storage`
3. **Compression optionnelle à l'ajout** : Si `compression_on_add=True`, la compression peut être déclenchée immédiatement après l'ajout des messages

### 2. Flux de récupération et compression de la mémoire
Lorsque `get_memory()` est appelé (si `compression_on_get=True`) :
1. **Comptage des tokens** : Le système calcule le nombre total de tokens de tous les messages dans `memory_storage`
2. **Vérification de la compression** :
   - Vérifie d'abord si le nombre de tokens dépasse `max_token` (compression automatique)
   - Vérifie ensuite si `compression_trigger_func` renvoie `True` (déclencheur personnalisé)
3. **Compression par LLM** : Si la compression est nécessaire, tous les messages de `memory_storage` sont envoyés au LLM avec une invite de compression
4. **Sortie structurée** : Le LLM renvoie une réponse structurée contenant le résumé compressé
5. **Remplacement de la mémoire** : L'ensemble du `memory_storage` est mis à jour avec le(s) message(s) compressé(s)
6. **Filtrage et sélection** : Le filtrage optionnel et la sélection des recent_n sont appliqués
7. **Retour** : La mémoire traitée est renvoyée

### 3. Processus de compression
La compression utilise une approche de sortie structurée :
- **Invite** : Instruit le LLM de résumer l'historique des conversations tout en préservant les informations clés
- **Invite personnalisable** : Prend en charge le paramètre `customized_compression_prompt` pour des modèles d'invite personnalisés
- **Schéma** : Utilise `MemoryCompressionSchema` (modèle Pydantic) pour garantir un format de sortie cohérent
- **Format de sortie** : Renvoie un message avec le contenu encapsulé dans des balises `<compressed_memory>`
- **Support asynchrone** : Toutes les opérations de compression sont asynchrones

## Exemples d'utilisation

### Exécution de l'exemple
Pour voir `MemoryWithCompress` en action, exécutez le script d'exemple :
```bash
python ./main.py
```

### Initialisation de base
Voici un extrait de `main.py` montrant comment configurer l'agent et la mémoire :

```python
from agentscope.agent import ReActAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.token import OpenAITokenCounter
from agentscope.message import Msg
from _memory_with_compress import MemoryWithCompress

# 1. Créer le modèle pour l'agent et la compression de mémoire
model = DashScopeChatModel(
    api_key=os.environ.get("DASHSCOPE_API_KEY"),
    model_name="qwen-max",
    stream=False,
)

# 2. Optionnel : Définir une fonction de déclenchement de compression personnalisée
async def trigger_compression(msgs: list[Msg]) -> bool:
    # Déclencher la compression si le nombre de messages dépasse 2
    # et que le dernier message provient de l'assistant
    return len(msgs) > 2 and msgs[-1].role == "assistant"

# 3. Initialiser MemoryWithCompress
memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=DashScopeChatFormatter(),
    max_token=3000,  # Compresser lorsque la mémoire dépasse 3000 tokens
    token_counter=OpenAITokenCounter(model_name="qwen-max"),
    compression_trigger_func=trigger_compression,  # Déclencheur personnalisé optionnel
    compression_on_add=False,  # Ne pas compresser à l'ajout (par défaut)
    compression_on_get=True,   # Compresser à la récupération (par défaut)
)

# 4. Initialiser ReActAgent avec l'instance de mémoire
agent = ReActAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant named Friday.",
    model=model,
    formatter=DashScopeChatFormatter(),
    memory=memory_with_compress,
)
```

### Fonction de compression personnalisée
Vous pouvez fournir une fonction de compression personnalisée :

```python
async def custom_compress(messages: List[Msg]) -> List[Msg]:
    # Votre logique de compression personnalisée
    # Doit renvoyer une List[Msg], pas un seul Msg
    compressed_content = "..."
    return [Msg("assistant", compressed_content, "assistant")]

memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=formatter,
    max_token=300,
    compress_func=custom_compress,
)
```

### Backend de stockage personnalisé
Vous pouvez fournir des backends de stockage personnalisés en implémentant l'interface `MessageStorageBase` :

```python
from _memory_storage import MessageStorageBase

class CustomStorage(MessageStorageBase):
    # Implémenter les méthodes requises : start, stop, health, add, delete, clear, get, replace, __aenter__, __aexit__
    ...

memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=formatter,
    max_token=300,
    chat_history_storage=CustomStorage(),
    memory_storage=CustomStorage(),
)
```

## Référence de l'API

### Classe MemoryWithCompress

#### `__init__(...)`
Initialise le système de mémoire. Les paramètres clés incluent :

- `model` (ChatModelBase) : Le modèle LLM à utiliser pour la compression
- `formatter` (FormatterBase) : Le formateur à utiliser pour le formatage des messages
- `max_token` (int) : Le nombre maximum de tokens pour `memory_storage`. Par défaut : 28000. La compression est déclenchée lorsque cette limite est dépassée
- `chat_history_storage` (MessageStorageBase) : Backend de stockage pour l'historique complet des conversations. Par défaut : `InMemoryMessageStorage()`
- `memory_storage` (MessageStorageBase) : Backend de stockage pour la mémoire compressée. Par défaut : `InMemoryMessageStorage()`
- `token_counter` (Optional[TokenCounterBase]) : Le compteur de tokens pour compter les tokens. Par défaut : None. Si None, renvoie le nombre de caractères de la représentation JSON en chaîne des messages (c.-à-d. len(json.dumps(messages, ensure_ascii=False))).
- `compress_func` (Callable[[List[Msg]], Awaitable[List[Msg]]] | None) : Fonction de compression personnalisée. Doit être asynchrone et renvoyer `List[Msg]`. Si None, utilise la méthode par défaut `_compress_memory`
- `compression_trigger_func` (Callable[[List[Msg]], Awaitable[bool]] | None) : Fonction optionnelle pour déclencher la compression lorsque le nombre de tokens est inférieur à `max_token`. Doit être asynchrone et renvoyer `bool`. Si None, la compression ne se produit que lorsque le nombre de tokens dépasse `max_token`
- `compression_on_add` (bool) : Indique s'il faut vérifier et compresser la mémoire lors de l'ajout de messages. Par défaut : False
- `compression_on_get` (bool) : Indique s'il faut vérifier et compresser la mémoire lors de la récupération de messages. Par défaut : True
- `customized_compression_prompt` (str | None) : Modèle d'invite de compression personnalisé optionnel. Doit inclure les espaces réservés : `{max_token}`, `{messages_list_json}`, `{schema_json}`. Par défaut : None (utilise le modèle par défaut)

#### Méthodes principales

**`async add(msgs: Union[Sequence[Msg], Msg, None], compress_func=None, compression_trigger_func=None)`**
- Ajoute de nouveaux messages à la fois dans `chat_history_storage` et `memory_storage`
- Les messages sont copiés en profondeur pour éviter de modifier les originaux
- Lève une `TypeError` si des objets non-Msg sont fournis
- Paramètres :
  - `msgs` : Messages à ajouter
  - `compress_func` (Optionnel) : Remplace la fonction de compression au niveau de l'instance pour cet appel
  - `compression_trigger_func` (Optionnel) : Remplace la fonction de déclenchement au niveau de l'instance pour cet appel
- Si `compression_on_add=True`, peut déclencher la compression après l'ajout

**`async direct_update_memory(msgs: Union[Sequence[Msg], Msg, None])`**
- Met à jour directement le `memory_storage` avec de nouveaux messages (ne met pas à jour `chat_history_storage`)
- Utile pour remplacer directement le contenu de la mémoire

**`async get_memory(recent_n=None, filter_func=None, compress_func=None, compression_trigger_func=None)`**
- Récupère le contenu de la mémoire, en compressant automatiquement si la limite de tokens est dépassée (si `compression_on_get=True`)
- Paramètres :
  - `recent_n` (Optional[int]) : Renvoie uniquement les N messages les plus récents
  - `filter_func` (Optional[Callable[[int, Msg], bool]]) : Fonction de filtrage personnalisée qui prend (index, message) et renvoie bool
  - `compress_func` (Optionnel) : Remplace la fonction de compression au niveau de l'instance pour cet appel
  - `compression_trigger_func` (Optionnel) : Remplace la fonction de déclenchement au niveau de l'instance pour cet appel
- Renvoie : `list[Msg]` - Le contenu de la mémoire (potentiellement compressé)

**`async delete(indices: Union[Iterable[int], int])`**
- Supprime des fragments de mémoire de `memory_storage` (note : ne supprime pas de `chat_history_storage`)
- Les indices peuvent être un seul int ou un itérable d'int

**`async size() -> int`**
- Renvoie le nombre de messages dans `chat_history_storage`

**`async clear()`**
- Efface toute la mémoire de `chat_history_storage` et `memory_storage`

**`state_dict() -> dict`**
- Renvoie un dictionnaire contenant l'état sérialisé :
  - `chat_history_storage` : Liste des dictionnaires de messages de l'historique des conversations
  - `memory_storage` : Liste des dictionnaires de messages de la mémoire
  - `max_token` : Le paramètre max_token
- Note : Cette méthode gère les opérations asynchrones en interne, elle peut donc être appelée depuis des contextes synchrones et asynchrones

**`load_state_dict(state_dict: dict, strict: bool = True)`**
- Charge l'état de la mémoire depuis un dictionnaire
- Restaure `chat_history_storage`, `memory_storage` et les paramètres `max_token`
- Note : Cette méthode gère les opérations asynchrones en interne, elle peut donc être appelée depuis des contextes synchrones et asynchrones

**`async retrieve(*args, **kwargs)`**
- Non implémenté. Utilisez `get_memory()` à la place.
- Lève `NotImplementedError`

## Méthodes internes

**`async _compress_memory(msgs: List[Msg]) -> List[Msg]`**
- Méthode interne qui compresse les messages en utilisant le LLM
- Utilise la sortie structurée avec `MemoryCompressionSchema`
- Renvoie une `List[Msg]` contenant le résumé compressé (typiquement un seul message)
- Prend en charge les modèles en streaming et non-streaming

**`async _check_length_and_compress(compress_func=None) -> bool`**
- Vérifie si le nombre de tokens en mémoire dépasse `max_token` et compresse si nécessaire
- Renvoie `True` si la compression a été déclenchée, `False` sinon

**`async check_and_compress(compress_func=None, compression_trigger_func=None, memory=None) -> tuple[bool, List[Msg]]`**
- Vérifie si la compression est nécessaire en se basant sur `compression_trigger_func`
- Renvoie un tuple : (was_compressed: bool, compressed_memory: List[Msg])
- Si `memory` est fourni, vérifie celui-ci au lieu de `memory_storage`

## Fonctions utilitaires

Le module `_mc_utils.py` fournit :

- **`format_msgs(msgs)`** : Formate une liste d'objets `Msg` en une liste de dictionnaires
- **`async count_words(token_counter, text)`** : Compte les tokens dans le texte (prend en charge les formats string et list[dict]). Doit être attendu avec await.
- **`MemoryCompressionSchema`** : Modèle Pydantic pour la sortie de compression structurée
- **`DEFAULT_COMPRESSION_PROMPT_TEMPLATE`** : Modèle d'invite par défaut pour la compression (inclut les espaces réservés : `{max_token}`, `{messages_list_json}`, `{schema_json}`)

## Abstraction du stockage

Le module `_memory_storage.py` fournit :

- **`MessageStorageBase`** : Classe de base abstraite pour les backends de stockage de messages
  - Méthodes asynchrones requises : `start()`, `stop()`, `health()`, `add()`, `delete()`, `clear()`, `get()`, `replace()`, `__aenter__()`, `__aexit__()`
- **`InMemoryMessageStorage`** : Implémentation par défaut en mémoire
  - Stocke les messages dans une simple liste
  - Convient à la plupart des cas d'utilisation

## Bonnes pratiques

- **Sélection de la limite de tokens** : Choisissez `max_token` en fonction de la fenêtre de contexte de votre modèle et de la longueur typique des conversations
- **Moment de la compression** :
  - Définissez `compression_on_get=True` (par défaut) pour la compression lors de la récupération
  - Définissez `compression_on_add=False` (par défaut) pour éviter la compression lors des opérations d'ajout, car elle pourrait ne pas se terminer avant l'appel de `get_memory()`
- **Opérations asynchrones** : Toutes les méthodes principales sont asynchrones, utilisez donc `await` lors de leur appel
- **Persistance de l'état** : Utilisez `state_dict()` et `load_state_dict()` pour sauvegarder/restaurer l'état des conversations entre les sessions
- **Compression personnalisée** : Pour des besoins de compression spécifiques au domaine, implémentez une `compress_func` personnalisée (doit être asynchrone et renvoyer `List[Msg]`)
- **Déclencheurs de compression** : Utilisez `compression_trigger_func` pour une logique de compression personnalisée au-delà des limites de tokens (par ex., compresser après N messages, compresser sous certaines conditions)
- **Backends de stockage** : Implémentez des sous-classes personnalisées de `MessageStorageBase` pour un stockage persistant (par ex., base de données, système de fichiers)

## Dépannage

- **La compression ne se déclenche pas** :
  - Vérifiez que `compression_on_get=True` si vous attendez une compression lors de la récupération
  - Vérifiez que `max_token` est défini de manière appropriée
  - Assurez-vous que `get_memory()` est appelé (et attendu avec await)
  - Si vous utilisez `compression_trigger_func`, vérifiez qu'elle renvoie `True` lorsque la compression doit avoir lieu
- **Erreurs de sortie structurée** : Assurez-vous que votre modèle prend en charge la sortie structurée (par ex., les modèles DashScope avec le paramètre `structured_model`)
- **Problèmes de comptage de tokens** : Vérifiez que votre `token_counter` est compatible avec votre modèle et correctement configuré
- **Erreurs Async/Await** : N'oubliez pas que la plupart des méthodes sont asynchrones - utilisez `await` lors de leur appel
- **Problèmes de stockage** : Si vous utilisez des backends de stockage personnalisés, assurez-vous que toutes les méthodes requises sont correctement implémentées et asynchrones

## Référence

- [Documentation AgentScope](https://github.com/agentscope-ai/agentscope)
- [Documentation Pydantic](https://docs.pydantic.dev/)
