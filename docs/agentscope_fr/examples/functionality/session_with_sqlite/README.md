# Gestion de session avec SQLite DB

Cet exemple démontre comment implémenter la gestion de session avec un backend de base de données. Nous utilisons SQLite pour la simplicité,
mais l'approche peut être adaptée pour d'autres bases de données.

Plus précisément, nous implémentons une classe ``SqliteSession`` qui persiste et récupère les données de session depuis une table SQLite.
Le schéma de la table inclut des champs pour l'ID de session, les données de session (stockées en JSON) et les horodatages de création et de dernière
mise à jour.

Nous allons créer un simple agent et discuter avec lui, puis stocker les données de session dans la base de données SQLite. Ensuite, dans la
fonction ``test_load_session``, nous chargerons les données de session depuis la base de données et continuerons la conversation.

## Démarrage rapide

Installez agentscope depuis PyPI ou le code source.

```bash
pip install agentscope
```

Exécutez l'exemple avec la commande suivante

```bash
python main.py
```
