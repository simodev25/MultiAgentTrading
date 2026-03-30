# Plan avec ReAct Agent

Cet exemple démontre comment utiliser le module plan dans AgentScope pour permettre à un agent de créer et gérer un plan de manière formelle.

Plus précisément, nous fournissons deux exemples : le plan à spécification manuelle et le plan géré par l'agent.

## Plan à spécification manuelle

Dans cet exemple, nous spécifions d'abord manuellement un plan que l'agent doit suivre, puis nous laissons l'agent exécuter le plan étape par étape.

Pour exécuter cet exemple, lancez :

```bash
python main_manual_plan.py
```

## Plan géré par l'agent

Dans cet exemple, nous laissons l'agent créer et gérer son propre plan.
Plus précisément, nous utilisons la requête « Review the recent changes in AgentScope GitHub repository over the past month. »

Pour exécuter l'exemple, lancez :

```bash
python main_agent_managed_plan.py
```

> Note : L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer le modèle dans cet exemple, n'oubliez pas
> de changer le **formatter** en même temps ! La correspondance entre les modèles intégrés et les formatters
> est listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
