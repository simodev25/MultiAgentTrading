# Exemple Deep Research Agent

## Ce que cet exemple illustre

Cet exemple montre une implémentation d'un **DeepResearch Agent** utilisant le framework AgentScope. Le DeepResearch Agent est spécialisé dans la réalisation de recherches en plusieurs étapes pour collecter et intégrer des informations provenant de sources multiples, et génère des rapports complets pour résoudre des tâches complexes.
## Prérequis

- Python 3.10 ou supérieur
- Node.js et npm (pour le serveur MCP)
- Clé API DashScope depuis [Alibaba Cloud](https://dashscope.console.aliyun.com/)
- Clé API de recherche Tavily depuis [Tavily](https://www.tavily.com/)

## Comment exécuter cet exemple
1. **Définir les variables d'environnement** :
   ```bash
   export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   export TAVILY_API_KEY="your_tavily_api_key_here"
   export AGENT_OPERATION_DIR="your_own_direction_here"
   ```
2. **Tester le serveur MCP Tavily** :
    ```bash
    npx -y tavily-mcp@latest
    ```

3. **Exécuter le script** :
    ```bash
   python main.py
   ```

Si vous souhaitez avoir des conversations multi-tours avec le Deep Research Agent, vous pouvez modifier le code comme suit :
```python
from agentscope.agent import UserAgent
user = UserAgent("User")
user_msg = None
msg = []
while True:
    user_msg = await user(user_msg)
    if user_msg.get_text_content() == "exit":
        break
    msg.append(user_msg)
    assistant_msg = await agent(user_msg)
    msg.append(assistant_msg)
```
## Connexion au client MCP de recherche web
Le DeepResearch Agent ne prend actuellement en charge la recherche web que via le client MCP Tavily. Pour utiliser cette fonctionnalité, vous devez démarrer le serveur MCP localement et établir une connexion avec celui-ci.
```
from agentscope.mcp import StdIOStatefulClient

tavily_search_client= StdIOStatefulClient(
    name="tavily_mcp",
    command="npx",
    args=["-y", "tavily-mcp@latest"],
    env={"TAVILY_API_KEY": os.getenv("TAVILY_API_KEY", "")},
)
await tavily_search_client.connect()
```

> Note : L'exemple est construit avec le modèle de chat DashScope. Si vous souhaitez changer le modèle dans cet exemple, n'oubliez pas
> de changer le formatter en même temps ! La correspondance entre les modèles intégrés et les formatters est
> listée dans [notre tutoriel](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
