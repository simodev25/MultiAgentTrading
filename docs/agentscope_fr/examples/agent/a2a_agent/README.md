# Exemple du protocole Agent-to-Agent

L'`A2AAgent` dans AgentScope est un client A2A qui se connecte à un serveur d'agents externe via le protocole Agent-to-Agent (A2A).
Cet exemple démontre comment configurer et utiliser l'`A2AAgent` pour interagir avec un agent hébergé sur un serveur A2A.

Notez que la fonctionnalité A2A est expérimentale et susceptible de changer, et en raison des limitations du protocole A2A, l'`A2AAgent`
actuellement

1. ne supporte que les scénarios de chatbot, où seuls un utilisateur et un agent sont impliqués
2. ne supporte pas le pilotage/interruption en temps réel pendant la conversation
3. ne supporte pas les sorties structurées agentiques
4. stocke les messages observés localement et les envoie avec le(s) message(s) d'entrée de la fonction `reply`

## Fichiers

L'exemple contient les fichiers suivants :

```
examples/agent/a2a_agent
├── main.py                  # The main script to run the A2A agent example
├── setup_a2a_server.py      # The script to set up a simple A2A server
├── agent_card.py            # The agent card definition for the A2A agent
└── README.md                # This README file
```

## Configuration

Cet exemple fournit une configuration simple pour démontrer comment utiliser l'`A2AAgent` dans AgentScope.
Vous devez d'abord installer les dépendances requises :

```bash
uv pip install a2a-sdk[http-server] agentscope[a2a]
#  or
pip install a2a-sdk[http-server] agentscope[a2a]
```

Ensuite, nous configurons d'abord un simple serveur A2A qui héberge un agent ReAct :
```bash
uvicorn setup_a2a_server:app --host 0.0.0.0 --port 8000
```
Cela démarrera un serveur A2A localement sur le port 8000.

Après cela, vous pouvez exécuter l'exemple d'agent A2A pour lancer une conversation de chatbot avec l'agent hébergé sur le serveur A2A :
```bash
python main.py
```
