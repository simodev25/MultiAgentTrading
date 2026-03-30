# Exemple de connexion au serveur MCP API AlibabaCloud

## Ce que cet exemple démontre

Ce cas d'utilisation montre comment utiliser la connexion OAuth dans agentscope pour se connecter au serveur MCP API d'Alibaba Cloud.

Alibaba Cloud est une entreprise mondiale leader dans le cloud computing et l'intelligence artificielle, engagée à fournir des services de cloud computing et des middleware intégrés pour les entreprises et les développeurs.

Le serveur MCP API d'Alibaba Cloud fournit un accès basé sur MCP à la quasi-totalité des OpenAPI d'Alibaba Cloud. Vous pouvez les créer et les optimiser sans coder sur <https://api.aliyun.com/mcp>.

Par exemple, vous pouvez ajouter les interfaces de requête de prix du service ECS DescribePrice et CreateInstance, DescribeImages à un service MCP personnalisé. Cela vous permet d'obtenir une adresse MCP distante sans aucune configuration de code. En utilisant AgentScope, vous pouvez interroger les prix et passer des commandes depuis l'agent. En plus de supporter les OpenAPI atomiques, il supporte également l'encapsulation de Terraform HCL en tant qu'outil distant pour réaliser une orchestration déterministe.

Après avoir ajouté l'exemple MCP, vous pouvez utiliser des requêtes similaires aux suivantes :
1. Trouver l'instance ECS la moins chère dans la région de Hangzhou ;
2. Créer une instance avec le prix le plus bas et les spécifications minimales à Hangzhou.


## Prérequis

- Python 3.10 ou supérieur
- Packages Python asyncio, webbrowser
- Node.js et npm (pour le serveur MCP)
- Adresse de connexion au serveur MCP API AlibabaCloud [Console du serveur MCP API Alibaba Cloud](https://api.aliyun.com/mcp)

## Comment exécuter cet exemple

**Modifier main.py**

```python
# openai base
# read from .env
load_dotenv()

server_url = "https://openapi-mcp.cn-hangzhou.aliyuncs.com/accounts/14******/custom/****/id/KXy******/mcp"
```


Vous devez créer votre propre MCP SERVER depuis https://api.aliyun.com/mcp et remplacer le lien ici. Veuillez choisir une adresse qui utilise le protocole streamable HTTP.


**Exécuter le script** :
```bash
python main.py
```

## Exemple vidéo

<https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250911/otcfsk/AgentScope+%E9%9B%86%E6%88%90+OpenAPI+MCP+Server%28%E8%87%AA%E7%84%B6%E8%AF%AD%E8%A8%80%E5%88%9B%E5%BB%BA+ECS%29.mp4>

Cette vidéo démontre comment compléter la configuration dans AgentScope en utilisant le service MCP SERVER d'Alibaba Cloud API. Après s'être connecté via OAuth, les utilisateurs peuvent créer une instance ECS en utilisant le langage naturel.
