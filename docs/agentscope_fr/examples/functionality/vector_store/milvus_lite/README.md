# MilvusLite Vector Store

Cet exemple montre comment utiliser **MilvusLiteStore** pour le stockage vectoriel et la recherche sémantique dans AgentScope.
Il comprend quatre scénarios de test couvrant les opérations CRUD, le filtrage par métadonnées, le découpage de documents et les métriques de distance.

### Démarrage rapide

Installez d'abord agentscope, puis la dépendance MilvusLite :

```bash
# Sous MacOS/Linux
pip install pymilvus\[milvus_lite\]

# Sous Windows
pip install pymilvus[milvus_lite]
```

Exécutez le script d'exemple, qui présente l'ajout, la recherche avec et sans filtres dans le vector store MilvusLite :

```bash
python milvuslite_store.py
```

> **Note :** Le script crée des fichiers `.db` dans le répertoire courant. Vous pouvez les supprimer après les tests.

## Utilisation

### Initialiser le store
```python
from agentscope.rag import MilvusLiteStore

store = MilvusLiteStore(
    uri="./milvus_test.db",
    collection_name="test_collection",
    dimensions=768,              # Correspondre à votre modèle d'embedding
    distance="COSINE",           # COSINE, L2, ou IP
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
    embedding=[0.1, 0.2, ...],  # Votre vecteur d'embedding
)

await store.add([doc])
```

### Rechercher

```python
results = await store.search(
    query_embedding=[0.15, 0.25, ...],
    limit=5,
    score_threshold=0.9,                # Optionnel
    filter='doc_id like "prefix%"',     # Optionnel
)
```

### Supprimer

```python
await store.delete(filter_expr='doc_id == "doc_1"')
```

## Métriques de distance

| Métrique | Description | Idéal pour |
|--------|-------------|----------|
| **COSINE** | Similarité cosinus | Embeddings de texte (recommandé) |
| **L2** | Distance euclidienne | Données spatiales |
| **IP** | Produit scalaire | Systèmes de recommandation |

## Expressions de filtrage

```python
# Correspondance exacte
filter='doc_id == "doc_1"'

# Correspondance par motif
filter='doc_id like "prefix%"'

# Opérateurs numériques et logiques
filter='chunk_id >= 0 and total_chunks > 1'
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

**Comment supprimer la base de données ?**
Supprimez le fichier `.db` spécifié dans le paramètre `uri`.

**Est-ce adapté à la production ?**
MilvusLite fonctionne bien pour le développement et les applications à petite échelle. Pour la production à grande échelle, envisagez le mode standalone ou cluster de Milvus.

## Références

- [Documentation Milvus](https://milvus.io/docs)
- [Tutoriel RAG AgentScope](https://doc.agentscope.io/tutorial/task_rag.html)
