# Exemple AlibabaCloud MySQL Vector Store

Cet exemple montre comment utiliser la classe `AlibabaCloudMySQLStore` dans le système RAG d'AgentScope pour le stockage vectoriel et les opérations de recherche par similarité en utilisant AlibabaCloud MySQL (RDS) avec les fonctions vectorielles natives.

## Fonctionnalités

AlibabaCloudMySQLStore fournit :
- Stockage vectoriel utilisant le type de données natif VECTOR de MySQL
- Création automatique d'index vectoriels (CREATE VECTOR INDEX) basée sur la métrique de distance
- Fonctions vectorielles (VEC_FROMTEXT, VEC_DISTANCE_COSINE, VEC_DISTANCE_EUCLIDEAN)
- Calcul de distance et tri au niveau de la base de données via ORDER BY
- Deux métriques de distance : COSINE et EUCLIDEAN (prises en charge par AlibabaCloud MySQL)
- Support du filtrage par métadonnées
- Opérations CRUD (Create, Read, Update, Delete)
- Support des documents découpés en fragments
- Accès direct à la connexion MySQL sous-jacente pour les opérations avancées
- Intégration complète avec les instances AlibabaCloud RDS MySQL

## Prérequis

### 1. Instance AlibabaCloud RDS MySQL

Vous avez besoin d'une instance AlibabaCloud RDS MySQL avec le support vectoriel :

- **Version** : MySQL 8.0+
- **Plugin vectoriel** : Assurez-vous que le plugin de recherche vectorielle est activé (vérifiez que le paramètre `vidx_disabled` est à OFF)
- **Accès réseau** : Configurez le groupe de sécurité et la liste blanche pour autoriser l'accès

#### Créer une instance RDS MySQL sur AlibabaCloud :

1. Accédez à la [Console AlibabaCloud RDS](https://rdsnext.console.aliyun.com/)
2. Cliquez sur "Create Instance"
3. Sélectionnez MySQL 8.0 ou supérieur
4. Configurez les spécifications selon vos besoins
5. Configurez les paramètres réseau et de sécurité
6. Notez le point de terminaison de connexion (par ex., `rm-xxxxx.mysql.rds.aliyuncs.com`)

#### Configurer la base de données :

```sql
-- Se connecter à votre instance RDS MySQL
mysql -h rm-xxxxx.mysql.rds.aliyuncs.com -P 3306 -u your_username -p

-- Vérifier si la fonctionnalité vectorielle est activée (vidx_disabled doit être à OFF)
SHOW VARIABLES LIKE 'vidx_disabled';
-- Résultat attendu : vidx_disabled | OFF
-- Si OFF, la fonctionnalité vectorielle est activée
-- Si ON, contactez le support AlibabaCloud pour activer le plugin de recherche vectorielle

-- Créer la base de données
CREATE DATABASE agentscope_test;

-- Utiliser la base de données
USE agentscope_test;

-- Vérifier que les fonctions vectorielles sont disponibles
SELECT VEC_FROMTEXT('[1,2,3]');
```

### 2. Dépendances Python

```bash
pip install mysql-connector-python agentscope
```

### 3. Configuration réseau

Assurez-vous que votre machine locale ou serveur peut accéder à l'instance RDS :
- Ajoutez votre IP à la liste blanche RDS
- Configurez les règles du groupe de sécurité
- Utilisez une connexion SSL si nécessaire

## Configuration

Mettez à jour les paramètres de connexion dans `main.py` :

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",  # Votre point de terminaison RDS
    port=3306,
    user="your_username",        # Votre nom d'utilisateur RDS
    password="your_password",    # Votre mot de passe RDS
    database="agentscope_test",
    table_name="test_vectors",
    dimensions=768,              # Définir selon la dimension de votre embedding
    distance="COSINE",
    # Optionnel : configuration SSL
    # connection_kwargs={
    #     "ssl_ca": "/path/to/ca.pem",
    #     "ssl_verify_cert": True,
    # }
)
```

## Exécution de l'exemple

```bash
python main.py
```

## Tests de l'exemple

L'exemple inclut trois tests complets :

### 1. Opérations CRUD de base
- Initialiser AlibabaCloudMySQLStore
- Ajouter des documents avec des embeddings
- Rechercher des documents similaires
- Supprimer des documents
- Obtenir la connexion MySQL sous-jacente

### 2. Recherche avec filtrage par métadonnées
- Ajouter des documents avec différentes catégories
- Rechercher avec et sans filtres
- Utiliser des clauses SQL WHERE pour le filtrage

### 3. Différentes métriques de distance
- Tester la similarité COSINE (idéale pour les vecteurs normalisés)
- Tester la distance EUCLIDEAN (idéale pour les distances absolues)

## Explication des fonctionnalités clés

### Métriques de distance

AlibabaCloud MySQL prend en charge deux métriques de distance :

- **COSINE** : Mesure le cosinus de l'angle entre les vecteurs. Les valeurs vont de 0 (identique) à 2 (opposé). Idéal pour les embeddings de texte et les vecteurs normalisés.
- **EUCLIDEAN** : Mesure la distance euclidienne en ligne droite entre les vecteurs. Des valeurs plus basses indiquent une similarité. Idéal pour les mesures de distance absolue.

**Note** : Contrairement à certaines autres bases de données vectorielles, AlibabaCloud MySQL ne prend actuellement en charge que les fonctions de distance COSINE et EUCLIDEAN. Le produit scalaire (IP) n'est pas pris en charge.

### Filtrage par métadonnées

Utilisez des clauses SQL WHERE pour filtrer les résultats de recherche :

```python
results = await store.search(
    query_embedding=embedding,
    limit=10,
    filter='doc_id LIKE "ai%" AND chunk_id > 0',
)
```

### Structure de la table

L'implémentation crée automatiquement une table avec la structure suivante :

```sql
CREATE TABLE IF NOT EXISTS table_name (
    id VARCHAR(255) PRIMARY KEY,
    embedding VECTOR(dimensions) NOT NULL,
    doc_id VARCHAR(255) NOT NULL,
    chunk_id INT NOT NULL,
    content TEXT NOT NULL,
    total_chunks INT NOT NULL,
    INDEX idx_doc_id (doc_id),
    INDEX idx_chunk_id (chunk_id),
    VECTOR INDEX (embedding) M=16 DISTANCE=cosine  -- ou DISTANCE=euclidean
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Note** : L'index vectoriel est créé directement dans l'instruction `CREATE TABLE`, et non comme une commande SQL séparée. Le paramètre `M` contrôle la connectivité du graphe de l'algorithme HNSW (par défaut : 16).

### Considérations de performance

- **Type de données VECTOR** : Utilise le type natif VECTOR de MySQL pour un stockage efficace
- **Index vectoriel** : Crée automatiquement un index vectoriel avec la métrique de distance spécifiée pour une recherche par similarité rapide
- **Calcul de distance au niveau de la base de données** : Les calculs de distance vectorielle sont effectués au niveau de la base de données en utilisant les fonctions vectorielles natives de MySQL (VEC_DISTANCE_COSINE, VEC_DISTANCE_EUCLIDEAN), avec un tri via SQL ORDER BY
- **Support vectoriel natif** : MySQL 8.0+ dispose de fonctions vectorielles intégrées hautement optimisées pour les opérations vectorielles
- **Métriques de distance prises en charge** : Seules COSINE et EUCLIDEAN sont prises en charge
- **Jeux de données petits à moyens** : AlibabaCloudMySQLStore fonctionne bien pour des jeux de données jusqu'à 100K vecteurs
- **Grands jeux de données** : Pour des jeux de données avec des millions de vecteurs, envisagez d'utiliser des bases de données vectorielles dédiées (MilvusLite, Qdrant) avec une indexation spécialisée (HNSW, IVF, etc.)
- **Performances RDS** : Tirez parti des fonctionnalités AlibabaCloud RDS comme les réplicas en lecture, la sauvegarde et la surveillance

## Utilisation avancée

### Accès direct à la base de données

```python
# Obtenir la connexion MySQL pour les opérations avancées
conn = store.get_client()
cursor = conn.cursor()

# Exécuter des requêtes SQL personnalisées
cursor.execute("SELECT COUNT(*) FROM test_vectors")
count = cursor.fetchone()
print(f"Total vectors: {count[0]}")
```

### Utilisation des fonctions vectorielles natives de MySQL

Les fonctions vectorielles natives de MySQL peuvent être utilisées directement dans les requêtes SQL :

```python
conn = store.get_client()
cursor = conn.cursor()

# Utiliser les fonctions vectorielles natives de MySQL directement
query_vector = "[0.1,0.2,0.3,0.4]"
cursor.execute("""
    SELECT
        doc_id,
        VEC_DISTANCE_COSINE(vector, VEC_FROMTEXT(%s)) as distance
    FROM test_vectors
    ORDER BY distance ASC
    LIMIT 10
""", (query_vector,))

results = cursor.fetchall()

# Fonctions vectorielles MySQL disponibles dans AlibabaCloud :
# - VEC_FROMTEXT(text) - Convertir du texte "[1,2,3]" en vecteur
# - VEC_DISTANCE_COSINE(v1, v2) - Distance cosinus
# - VEC_DISTANCE_EUCLIDEAN(v1, v2) - Distance euclidienne
```

### Connexion SSL

Pour des connexions sécurisées vers AlibabaCloud RDS :

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="agentscope_test",
    table_name="vectors",
    dimensions=768,
    distance="COSINE",
    connection_kwargs={
        "ssl_ca": "/path/to/ca.pem",
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    },
)
```

### Opérations par lots

```python
# Ajouter de grands lots de documents
batch_size = 1000
for i in range(0, len(all_documents), batch_size):
    batch = all_documents[i:i + batch_size]
    await store.add(batch)
```

### Pool de connexions

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="agentscope_test",
    table_name="vectors",
    dimensions=768,
    distance="COSINE",
    connection_kwargs={
        "pool_name": "mypool",
        "pool_size": 10,
        "pool_reset_session": True,
    },
)
```

## Dépannage

### Vérification de la version MySQL

Assurez-vous que votre version RDS MySQL prend en charge les fonctions vectorielles :

```sql
SELECT VERSION();
-- Doit être MySQL 8.0 ou supérieur

-- Vérifier si la fonctionnalité vectorielle est activée (vérification critique)
SHOW VARIABLES LIKE 'vidx_disabled';
-- Résultat attendu : vidx_disabled | OFF (fonctionnalité vectorielle activée)

-- Tester les fonctions vectorielles
SELECT VEC_FROMTEXT('[1,2,3]');
```

### Erreurs de connexion

Si vous obtenez des erreurs de connexion :

1. **Vérifier la liste blanche** : Assurez-vous que votre IP est dans la liste blanche RDS
2. **Groupe de sécurité** : Vérifiez que les règles du groupe de sécurité autorisent le port 3306
3. **Type de réseau** : Assurez-vous d'utiliser le bon point de terminaison (public/privé)
4. **Identifiants** : Vérifiez le nom d'utilisateur et le mot de passe

```bash
# Tester la connexion depuis la ligne de commande
mysql -h rm-xxxxx.mysql.rds.aliyuncs.com -P 3306 -u your_username -p
```

### Erreurs de fonctions vectorielles

Si vous obtenez des erreurs concernant VEC_DISTANCE_COSINE ou le type VECTOR non reconnu :

1. **Vérifier si la fonctionnalité vectorielle est activée** :

```sql
-- Vérifier le paramètre vidx_disabled (doit être à OFF)
SHOW VARIABLES LIKE 'vidx_disabled';
-- Résultat attendu : vidx_disabled | OFF
-- Si ON, la fonctionnalité vectorielle est désactivée, contactez le support AlibabaCloud
```

2. Vérifiez que la version MySQL est 8.0 ou supérieure

```sql
SELECT VERSION();
```

3. Testez la disponibilité des fonctions vectorielles :

```sql
-- Vérifier si les fonctions vectorielles sont disponibles
SELECT VEC_FROMTEXT('[1,2,3]');

-- Vérifier si le type VECTOR est pris en charge
CREATE TABLE test_vector (v VECTOR(3));
DROP TABLE test_vector;

-- Lister les index vectoriels
SHOW INDEX FROM your_table_name WHERE Index_type = 'VECTOR';
```

Si `vidx_disabled` est à ON, contactez le support AlibabaCloud pour activer le plugin de recherche vectorielle pour votre instance RDS.

### Optimisation des performances

Pour les grands jeux de données sur AlibabaCloud RDS :

1. **Mettre à niveau l'instance** : Envisagez des spécifications supérieures (CPU, mémoire)
2. **Réplicas en lecture** : Utilisez des réplicas en lecture pour les charges de travail à forte lecture
3. **Index** : Ajoutez des index sur les colonnes fréquemment filtrées
4. **Pool de connexions** : Utilisez le pool de connexions pour les opérations concurrentes
5. **Surveillance** : Utilisez AlibabaCloud CloudMonitor pour des informations sur les performances

### Erreurs de timeout

Si vous rencontrez des erreurs de timeout :

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="agentscope_test",
    table_name="vectors",
    dimensions=768,
    distance="COSINE",
    connection_kwargs={
        "connect_timeout": 30,
        "read_timeout": 60,
        "write_timeout": 60,
    },
)
```

## Bonnes pratiques AlibabaCloud RDS

1. **Sauvegarde** : Activez les sauvegardes automatiques dans la console RDS
2. **Surveillance** : Configurez des alertes pour l'utilisation du CPU, de la mémoire et des connexions
3. **Sécurité** : Utilisez des connexions réseau privées lorsque c'est possible
4. **Mise à l'échelle** : Envisagez des instances en lecture seule pour les charges de travail à forte lecture
5. **Optimisation des coûts** : Utilisez des instances réservées pour une utilisation à long terme

## Ressources associées

- [Documentation AlibabaCloud RDS](https://www.alibabacloud.com/help/en/apsaradb-for-rds)
- [Fonctions vectorielles AlibabaCloud MySQL](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-mysql/vector-storage-1)
- [Tutoriel RAG AgentScope](https://doc.agentscope.io/tutorial/task_rag.html)
- [MySQL Connector Python](https://dev.mysql.com/doc/connector-python/en/)

## Exemples de cas d'utilisation

### Système RAG avec AlibabaCloud

```python
from agentscope.rag import AlibabaCloudMySQLStore, KnowledgeBase

# Initialiser le vector store avec AlibabaCloud RDS
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="rag_system",
    table_name="knowledge_vectors",
    dimensions=768,
    distance="COSINE",
)

# Créer la base de connaissances
kb = KnowledgeBase(store=store)

# Ajouter des documents
await kb.add_documents(documents)

# Rechercher
results = await kb.search("What is AI?", top_k=5)
```

## Support

Pour les problèmes liés à :
- **AlibabaCloudMySQLStore** : Ouvrez une issue sur le GitHub d'AgentScope
- **RDS MySQL** : Contactez le support AlibabaCloud
- **Fonctions vectorielles** : Consultez la documentation MySQL ou le support AlibabaCloud

## Licence

Cet exemple fait partie du projet AgentScope et suit la même licence.
