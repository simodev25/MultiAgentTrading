# RAG dans AgentScope

Cet exemple inclut trois scripts pour démontrer comment utiliser le Retrieval-Augmented Generation (RAG) dans AgentScope :

- l'utilisation de base du module RAG dans AgentScope dans ``basic_usage.py``,
- un cas d'utilisation agentique simple de RAG dans ``agentic_usage.py``, et
- l'intégration de RAG dans la classe ``ReActAgent`` en récupérant le(s) message(s) d'entrée au début de chaque réponse dans ``react_agent_integration.py``.
- la construction d'un RAG multimodal dans ``multimodal_rag.py``.

> L'utilisation agentique et l'intégration statique ont leurs propres avantages et limitations.
>  - L'utilisation agentique nécessite des LLM plus puissants pour gérer le processus de récupération, mais elle est plus flexible et l'agent peut ajuster la stratégie de récupération dynamiquement
>  - L'intégration statique est plus directe et plus facile à implémenter, mais elle est moins flexible et le message d'entrée peut ne pas être assez spécifique, conduisant à des résultats de récupération moins pertinents.

> Note : L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer le modèle dans cet exemple, n'oubliez pas
> de changer le formatter en même temps ! La correspondance entre les modèles intégrés et les formatters est
> listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)

## Démarrage rapide

Installez la dernière bibliothèque agentscope depuis PyPI ou les sources, puis exécutez la commande suivante pour lancer l'exemple :

- l'utilisation de base :
```bash
python basic_usage.py
```

- l'utilisation agentique :
```bash
python agentic_usage.py
```

- l'intégration statique :
```bash
python react_agent_integration.py
```

- le RAG multimodal :
```bash
python multimodal_rag.py
```
