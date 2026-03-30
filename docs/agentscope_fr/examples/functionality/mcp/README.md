# MCP dans AgentScope

Cet exemple démontre comment

- créer un client MCP avec différents transports (SSE et Streamable HTTP) et types (Stateless et Stateful),
- enregistrer des fonctions d'outils MCP et les utiliser dans un agent ReAct, et
- obtenir une fonction d'outil MCP en tant qu'objet callable local depuis le client MCP.


## Prérequis

- Python 3.10 ou supérieur
- Clé API DashScope d'Alibaba Cloud

## Installation

### Installer AgentScope

```bash
# Install from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
```

## Démarrage rapide

Installez agentscope et assurez-vous d'avoir une clé API DashScope valide dans vos variables d'environnement.

> Note : L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer le modèle dans cet exemple, n'oubliez pas
> de changer le formatter en même temps ! La correspondance entre les modèles intégrés et les formatters est
> listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)

```bash
pip install agentscope
```

Démarrez les serveurs MCP avec les commandes suivantes dans deux terminaux séparés :

```bash
# In one terminal, run:
python mcp_add.py

# In another terminal, run:
python mcp_multiply.py
```

Deux serveurs MCP seront démarrés sur `http://127.0.0.1:8001` (serveur SSE) et `http://127.0.0.1:8002` (serveur
streamable HTTP).

Après avoir démarré les serveurs MCP, vous pouvez exécuter l'exemple de l'agent :

```bash
python main.py
```

L'agent va :
1. Enregistrer les outils MCP depuis les serveurs
2. Utiliser un agent ReAct pour résoudre un problème de calcul (multiplier deux nombres puis ajouter un autre nombre)
3. Retourner une sortie structurée avec le résultat final
