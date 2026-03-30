# Agent Skills dans AgentScope

[Agent Skill](https://claude.com/blog/skills) est une approche proposée par
Anthropic pour améliorer les capacités des agents sur des tâches spécifiques.

Dans cet exemple, nous démontrons comment intégrer les Agent Skills dans un
agent ReAct dans AgentScope via l'API `toolkit.register_agent_skill`.

Plus précisément, nous préparons un skill de démonstration qui aide l'agent à
en apprendre davantage sur le framework AgentScope lui-même dans le répertoire `skill`.
Dans `main.py`, nous enregistrons ce skill dans le toolkit de l'agent et lui demandons
de répondre à des questions sur AgentScope.

## Démarrage rapide

Installez la dernière version d'AgentScope pour exécuter cet exemple :

```bash
pip install agentscope --upgrade
```

Puis, exécutez l'exemple avec :

```bash
python main.py
```

> Note :
> - L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer le modèle utilisé dans cet exemple,
> n'oubliez pas de changer le formatter en même temps ! La correspondance entre les modèles intégrés et
> les formatters est listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
> - Pour les modèles locaux, assurez-vous que le service de modèle (comme Ollama) est en cours d'exécution avant de démarrer l'agent.
