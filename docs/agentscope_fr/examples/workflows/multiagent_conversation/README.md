# Conversation multi-agents

Cet exemple démontre comment construire un workflow de conversation multi-agents en utilisant ``MsgHub`` dans AgentScope,
où plusieurs agents diffusent des messages les uns aux autres dans un espace de conversation partagé.

## Configuration

L'exemple est construit sur l'API LLM DashScope dans [main.py](https://github.com/agentscope-ai/agentscope/blob/main/examples/workflows/multiagent_conversation/main.py). Vous pouvez passer à d'autres LLM en modifiant les paramètres ``model`` et ``formatter`` dans le code.

Pour exécuter l'exemple, installez d'abord la dernière version d'AgentScope, puis lancez :

```bash
python examples/workflows/multiagent_conversation/main.py
```

## Workflow principal

- Créer plusieurs agents participants avec différents attributs (par ex. Alice, Bob, Charlie).
- Les agents se présentent et interagissent dans le hub de messages.
- Supporte l'ajout et la suppression dynamique d'agents, ainsi que la diffusion de messages.

> Note : L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer le modèle dans cet exemple, n'oubliez pas
> de changer le formatter en même temps ! La correspondance entre les modèles intégrés et les formatters est
> listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
