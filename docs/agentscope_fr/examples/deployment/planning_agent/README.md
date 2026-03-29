# Déploiement high-code d'un Routing Agent

Cet exemple montre comment déployer un système multi-agents en utilisant AgentScope. Le système est composé d'un agent de routage principal équipé d'une fonction outil nommée `create_worker` pour dispatcher les tâches vers des agents travailleurs spécialisés.

Concrètement, l'agent de routage est déployé comme un endpoint de chat dans un serveur géré par la bibliothèque `Quart`.
Lors de la réception d'une requête entrante, nous
- configurons un agent de routage
- chargeons l'état de la session s'il existe
- invoquons l'agent de routage pour traiter la requête, et renvoyons la réponse en streaming
- sauvegardons l'état de la session


# Structure de l'exemple

```
planning_agent/
    ├── main.py              # Entry point to start the Quart server with routing agent
    ├── tool.py              # Tool function to create worker agents
    └── test_post.py         # Preset test script to send requests to the server
```


## Note

1. Les messages d'affichage des sous-agents/agents travailleurs sont convertis en réponse en streaming de la fonction outil `create_worker`, ce qui signifie que le sous-agent ne sera pas directement exposé à l'utilisateur.

2. Le sous-agent dans `tool.py` est équipé des outils suivants. Pour les outils GitHub et AMap, ils ne seront activés que si les variables d'environnement correspondantes sont définies.
Vous pouvez personnaliser l'ensemble d'outils en modifiant le fichier `tool.py`.

| Tool                  | Description                                         | Required Environment Variable |
|-----------------------|-----------------------------------------------------|-------------------------------|
| write/view text files | Read and write text files                           | -                             |
| Playwright MCP server | Automate browser actions using Microsoft Playwright | -                             |
| GitHub MCP server     | Access GitHub repositories and data                 | GITHUB_TOKEN                  |
| AMap MCP server       | Access AMap services for location-based tasks       | GAODE_API_KEY                 |


3. Optionnellement, vous pouvez également exposer la réponse du sous-agent à l'utilisateur en modifiant le fichier `tool.py`.

## Démarrage rapide

Installez les dernières versions des packages agentscope et Quart :

```bash
pip install agentscope quart
```

Assurez-vous d'avoir défini `DASHSCOPE_API_KEY` dans votre environnement pour l'API LLM DashScope, ou changez le modèle utilisé dans
`main.py` et `tool.py` (N'oubliez pas de changer le formatter en conséquence).

Définissez les variables d'environnement pour les outils GitHub et AMap si nécessaire.

Lancez le serveur Quart :

```bash
python main.py
```

Dans un autre terminal, exécutez le script de test pour envoyer une requête au serveur :

```bash
python test_post.py
```
