# MongoDB Vector Store

Cet exemple montre comment utiliser **MongoDBStore** pour le stockage vectoriel et la recherche sémantique dans AgentScope en utilisant les capacités Vector Search de MongoDB.
Il comprend des scénarios de test complets couvrant les opérations CRUD, le filtrage par métadonnées, le découpage de documents et les métriques de distance.

### Démarrage rapide

Installez d'abord agentscope, puis la dépendance MongoDB :

```bash
pip install pymongo
```

**Important :** Avant d'exécuter l'exemple, vous devez définir la variable d'environnement `MONGODB_HOST`
avec votre chaîne de connexion MongoDB :

```bash
# Pour MongoDB local
export MONGODB_HOST="mongodb://localhost:27017/?directConnection=true"

# Pour MongoDB Atlas (remplacez par votre chaîne de connexion)
# export MONGODB_HOST=${YOUR_MONGODB_HOST}
```

Exécutez le script d'exemple, qui présente l'ajout, la recherche et la suppression dans le vector store MongoDB :

```bash
python main.py
```

> **Note :** Le script se connecte à MongoDB Atlas ou à une instance MongoDB locale. Assurez-vous d'avoir une chaîne de connexion MongoDB valide.

## Prérequis

- Confirmez que votre instance MongoDB prend en charge la fonctionnalité Vector Search
- Chaîne de connexion MongoDB valide (locale ou Atlas)

## Utilisation

### Initialiser le store

```python
from agentscope.rag import MongoDBStore

# Pour MongoDB Atlas
store = MongoDBStore(
    host="mongodb+srv://username:password@cluster.mongodb.net/",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,              # Correspondre à votre modèle d'embedding
    distance="cosine",           # cosine, euclidean, ou dotProduct
)

# Pour MongoDB local
store = MongoDBStore(
    host="mongodb://localhost:27017/?directConnection=true",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
)

# Pour activer le filtrage dans la recherche, spécifiez filter_fields :
store = MongoDBStore(
    host="mongodb://localhost:27017/?directConnection=true",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
    filter_fields=["payload.doc_id", "payload.chunk_id"],  # Champs pour le filtrage
)

# Aucune initialisation manuelle nécessaire - tout est automatique !
# La base de données, la collection et l'index de recherche vectorielle sont créés automatiquement
# lorsque vous appelez add() ou search() pour la première fois
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
    embedding=[0.1, 0.2, ...],  # Votre vecteur d'embedding
)

await store.add([doc])
```

### Rechercher

```python
results = await store.search(
    query_embedding=[0.15, 0.25, ...],
    limit=5,
    score_threshold=0.9,                                # Optionnel
    filter={"payload.doc_id": {"$in": ["doc_1", "doc_2"]}},  # Filtre optionnel
)
# Note :
# - Pour utiliser filter, le champ doit être déclaré dans filter_fields lors de la création du store
# - Le filtre $vectorSearch de MongoDB prend en charge : $gt, $gte, $lt, $lte,
#   $eq, $ne, $in, $nin, $exists, $not (PAS $regex)
```

### Supprimer

```python
# Supprimer par identifiants de documents (aucune initialisation nécessaire)
await store.delete(ids=["doc_1", "doc_2"])

# Supprimer la collection entière (à utiliser avec précaution)
await store.delete_collection()

# Supprimer la base de données entière (à utiliser avec précaution)
await store.delete_database()
```

## Métriques de distance

| Métrique | Description | Idéal pour |
|--------|-------------|----------|
| **cosine** | Similarité cosinus | Embeddings de texte (recommandé) |
| **euclidean** | Distance euclidienne | Données spatiales |
| **dotProduct** | Produit scalaire | Systèmes de recommandation |

## Utilisation avancée

### Accéder au client sous-jacent

```python
client = store.get_client()
# Utiliser le client MongoDB pour des opérations avancées
stats = await client[store.db_name].command("collStats", store.collection_name)
```

### Métadonnées de document

- `content` : Contenu textuel (TextBlock)
- `doc_id` : Identifiant unique du document
- `chunk_id` : Position du fragment (indexé à partir de 0)
- `total_chunks` : Nombre total de fragments dans le document

### Index Vector Search

MongoDBStore crée automatiquement des index de recherche vectorielle avec la configuration suivante :

```python
{
    "fields": [
        {
            "type": "vector",
            "path": "vector",
            "similarity": "cosine",  # ou euclidean, dotProduct
            "numDimensions": 768
        }
    ]
}
```

## Exemples de connexion

### MongoDB Atlas

```python
store = MongoDBStore(
    host="<YOUR_MONGO_ATLAS_CONNECTION_STRING>",
    db_name="production_db",
    collection_name="documents",
    dimensions=1536,
    distance="cosine",
)
```

### MongoDB local

#### Sans authentification

```python
store = MongoDBStore(
    host="mongodb://localhost:27017?directConnection=true",
    db_name="local_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
)
```

#### Avec authentification

```python
store = MongoDBStore(
    host="mongodb://user:pass@localhost:27017/?directConnection=true",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
)
```

## Références

- [Documentation MongoDB Vector Search](https://www.mongodb.com/docs/atlas/atlas-search/vector-search/)
- [Documentation MongoDB Atlas](https://www.mongodb.com/docs/atlas/)
- [Tutoriel RAG AgentScope](https://doc.agentscope.io/tutorial/task_rag.html)
