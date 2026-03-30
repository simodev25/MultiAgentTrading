# Débat multi-agents

Le workflow de débat simule une discussion multi-tours entre différents agents, principalement plusieurs solveurs et un agrégateur.
Typiquement, les solveurs génèrent et échangent leurs réponses, tandis que l'agrégateur collecte et résume les réponses.

Nous implémentons les exemples de [EMNLP 2024](https://aclanthology.org/2024.emnlp-main.992/), où deux agents débatteurs
discutent d'un sujet dans un ordre fixe et expriment leurs arguments en se basant sur l'historique du débat précédent.
À chaque tour, un agent modérateur décide si la réponse correcte peut être obtenue dans l'itération courante.

## Configuration

L'exemple est construit sur l'API LLM DashScope dans [main.py](https://github.com/agentscope-ai/agentscope/blob/main/examples/workflows/multiagent_debate/main.py).
Vous pouvez également passer à d'autres LLM en modifiant les paramètres ``model`` et ``formatter`` dans le code.

Pour exécuter l'exemple, installez d'abord la dernière version d'AgentScope, puis lancez :

```bash
python examples/workflows/multiagent_debate/main.py
```


> Note : L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer de modèle dans cet exemple, n'oubliez pas
> de changer le formatter en même temps ! La correspondance entre les modèles intégrés et les formatters est
> listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
