# Affichage en flux des messages

L'agent AgentScope est conçu pour communiquer avec l'utilisateur et les autres agents en passant des messages de manière explicite.
Cependant, nous avons identifié le besoin d'obtenir les messages affichés par l'agent de manière continue (en streaming).
Par conséquent, dans cet exemple, nous montrons comment collecter et produire les messages affichés par un agent unique et
par des systèmes multi-agents de manière continue.


## Démarrage rapide

Exécutez la commande suivante pour voir l'affichage en flux des messages de l'agent.
Notez que les messages avec le même ID sont les fragments du même message de manière accumulée.

- Pour un agent unique :

```bash
python single_agent.py
```

- Pour plusieurs agents :

```bash
python multi_agent.py
```

> Note : L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer de modèle dans cet exemple, n'oubliez pas
> de changer le formatter en même temps ! La correspondance entre les modèles intégrés et les formatters est
> listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
