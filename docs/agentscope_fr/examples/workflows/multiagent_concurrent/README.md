# Multiagent Concurrent

Cet exemple démontre comment exécuter plusieurs agents simultanément dans AgentScope, où chaque agent opère
indépendamment et peut effectuer des tâches en parallèle.

Plus précisément, nous présentons deux façons d'atteindre la concurrence :

- Utiliser `asyncio.gather` de Python pour exécuter plusieurs agents de manière asynchrone.
- Utiliser `fanout_pipeline` pour exécuter plusieurs agents en parallèle et collecter leurs résultats.

Le fanout pipeline distribue l'entrée à plusieurs agents et collecte leurs sorties, ce qui est approprié pour
des scénarios comme le vote ou la réponse à des questions en parallèle.

## Démarrage rapide

Installez le package agentscope si ce n'est pas déjà fait :

```bash
pip install agentscope
```

Puis exécutez le script d'exemple :

```bash
python main.py
```

## Lectures complémentaires
- [Pipelines](https://doc.agentscope.io/tutorial/task_pipeline.html)
