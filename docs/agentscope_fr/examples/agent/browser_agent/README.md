# Exemple de Browser Agent

Cet exemple démontre comment utiliser le BrowserAgent d'AgentScope pour les tâches d'automatisation web. Le BrowserAgent exploite le Model Context Protocol (MCP) pour interagir avec les outils de navigation alimentés par Playwright, permettant une navigation web sophistiquée, l'extraction de données et l'automatisation.


## Prérequis

- Python 3.10 ou supérieur
- Node.js et npm (pour le serveur MCP)
- Clé API DashScope d'Alibaba Cloud

## Installation

### Installer AgentScope

```bash
# Install from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
```

## Configuration

### 1. Configuration de l'environnement

Configurez votre clé API DashScope :

```bash
export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
```

Vous pouvez obtenir une clé API DashScope depuis la [console Alibaba Cloud DashScope](https://dashscope.console.aliyun.com/).

### 2. À propos du serveur MCP PlayWright

Avant d'exécuter le browser agent, vous pouvez tester si vous pouvez démarrer le serveur MCP Playwright :

```bash
npx @playwright/mcp@latest
```

## Utilisation

### Exemple de base
Vous pouvez commencer à exécuter le browser agent dans votre terminal avec la commande suivante
```bash
cd examples/agent/browser_agent
python main.py
```
