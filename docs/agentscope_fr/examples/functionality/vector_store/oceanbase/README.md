# OceanBase Vector Store

Cet exemple montre comment utiliser **OceanBaseStore** pour le stockage vectoriel et la recherche sémantique dans AgentScope.
Il comprend les opérations CRUD, le filtrage par métadonnées, le découpage de documents et les tests de métriques de distance.

### Démarrage rapide

Installez les dépendances (y compris `pyobvector`) :

```bash
pip install -e .[full]
```

Démarrez seekdb (une instance minimale compatible OceanBase) :

```bash
docker run -d -p 2881:2881 oceanbase/seekdb
```

Exécutez le script d'exemple :

```bash
python main.py
```

> **Note :** Le script utilise par défaut `127.0.0.1:2881`, utilisateur `root`, base de données `test`.
> Si vous utilisez un compte OceanBase multi-tenant (par ex., `root@test`), remplacez via les variables d'environnement.

## Utilisation

### Initialiser le store

```python
from agentscope.rag import OceanBaseStore

store = OceanBaseStore(
    collection_name="test_collection",
    dimensions=768,
    distance="COSINE",
    uri="127.0.0.1:2881",
    user="root",
    password="",
    db_name="test",
)
```

### Ajouter des documents

```python
from agentscope.rag import Document, DocMetadata
from agentscope.message import TextBlock

doc = Document(
    metadata=DocMetadata(
        content=TextBlock(type="text", text="Your document text"),
        doc_id="doc_1",
        chunk_id=0,
        total_chunks=1,
    ),
    embedding=[0.1, 0.2, 0.3],
)

await store.add([doc])
```

### Rechercher

```python
results = await store.search(
    query_embedding=[0.1, 0.2, 0.3],
    limit=5,
    score_threshold=0.9,
)
```

### Recherche avec filtre

```python
client = store.get_client()
table = client.load_table(collection_name="test_collection")

results = await store.search(
    query_embedding=[0.1, 0.2, 0.3],
    limit=5,
    flter=[table.c["doc_id"].like("doc%")],
)
```

> Note : Le nom du paramètre est `flter` (sans le "i") pour éviter le conflit avec
> le `filter` intégré de Python et suit la convention de la bibliothèque sous-jacente.

### Supprimer

```python
client = store.get_client()
table = client.load_table(collection_name="test_collection")

await store.delete(where=[table.c["doc_id"] == "doc_1"])
```

## Métriques de distance

| Métrique | Description | Idéal pour |
|--------|-------------|----------|
| **COSINE** | Similarité cosinus | Embeddings de texte (recommandé) |
| **L2** | Distance euclidienne | Données spatiales |
| **IP** | Produit scalaire | Systèmes de recommandation |

## Expressions de filtrage

Construisez des filtres en utilisant des expressions SQLAlchemy et passez-les via `flter` :

```python
table = store.get_client().load_table("test_collection")

filters = [
    table.c["doc_id"] == "doc_1",
    table.c["doc_id"].like("prefix%"),
    table.c["chunk_id"] >= 0,
]
```

## Utilisation avancée

### Accéder au client sous-jacent

```python
client = store.get_client()
stats = client.get_collection_stats(collection_name="test_collection")
```

### Métadonnées de document

- `content` : Contenu textuel (TextBlock)
- `doc_id` : Identifiant unique du document
- `chunk_id` : Position du fragment (indexé à partir de 0)
- `total_chunks` : Nombre total de fragments dans le document

## FAQ

**Quelle dimension d'embedding dois-je utiliser ?**
Correspondez à la dimension de sortie de votre modèle d'embedding (par ex., 768 pour BERT, 1536 pour OpenAI ada-002).

**Puis-je changer la métrique de distance après la création ?**
Non, créez une nouvelle collection avec la métrique souhaitée.

**Comment nettoyer les données de test ?**
Supprimez la collection via le client sous-jacent ou supprimez le volume du conteneur seekdb.

## Variables d'environnement

Le script prend en charge les variables d'environnement suivantes pour remplacer les paramètres de connexion :

```bash
export OCEANBASE_URI="127.0.0.1:2881"
export OCEANBASE_USER="root"
export OCEANBASE_PASSWORD=""
export OCEANBASE_DB="test"
```

## Références

- [OceanBase Vector Store](https://github.com/oceanbase/pyobvector)
- [Tutoriel RAG AgentScope](https://doc.agentscope.io/tutorial/task_rag.html)
