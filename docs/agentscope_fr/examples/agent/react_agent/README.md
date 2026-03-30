# Exemple de ReAct Agent

Cet exemple présente un agent **ReAct** dans AgentScope. Concrètement, l'agent ReAct dialogue avec l'utilisateur de manière alternée, c'est-à-dire en mode chatbot. Il est équipé d'un ensemble d'outils pour aider à répondre aux requêtes de l'utilisateur.

> 💡 Astuce : Essayez ``Ctrl+C`` pour interrompre la réponse de l'agent et expérimenter la fonctionnalité de pilotage/interruption en temps réel !

## Démarrage rapide

Assurez-vous d'avoir installé agentscope et défini ``DASHSCOPE_API_KEY`` dans vos variables d'environnement.

Exécutez les commandes suivantes pour configurer et lancer l'exemple :

```bash
python main.py
```

> Note :
> - L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer de modèle dans cet exemple, n'oubliez pas
> de changer le formatter en même temps ! La correspondance entre les modèles intégrés et
> les formatters est listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
> - Pour les modèles locaux, assurez-vous que le service de modèle (comme Ollama) est en cours d'exécution avant de démarrer l'agent.
