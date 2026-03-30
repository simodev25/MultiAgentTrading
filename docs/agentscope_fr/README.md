<p align="center">
  <img
    src="https://img.alicdn.com/imgextra/i1/O1CN01nTg6w21NqT5qFKH1u_!!6000000001621-55-tps-550-550.svg"
    alt="AgentScope Logo"
    width="200"
  />
</p>

<span align="center">

[**中文主页**](https://github.com/agentscope-ai/agentscope/blob/main/README_zh.md) | [**Tutorial**](https://doc.agentscope.io/) | [**Roadmap (Jan 2026 -)**](https://github.com/agentscope-ai/agentscope/blob/main/docs/roadmap.md) | [**FAQ**](https://doc.agentscope.io/tutorial/faq.html)

</span>

<p align="center">
    <a href="https://arxiv.org/abs/2402.14034">
        <img
            src="https://img.shields.io/badge/cs.MA-2402.14034-B31C1C?logo=arxiv&logoColor=B31C1C"
            alt="arxiv"
        />
    </a>
    <a href="https://pypi.org/project/agentscope/">
        <img
            src="https://img.shields.io/badge/python-3.10+-blue?logo=python"
            alt="pypi"
        />
    </a>
    <a href="https://pypi.org/project/agentscope/">
        <img
            src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fagentscope%2Fjson&query=%24.info.version&prefix=v&logo=pypi&label=version"
            alt="pypi"
        />
    </a>
    <a href="https://discord.gg/eYMpfnkG8h">
        <img
            src="https://img.shields.io/discord/1194846673529213039?label=Discord&logo=discord"
            alt="discord"
        />
    </a>
    <a href="https://doc.agentscope.io/">
        <img
            src="https://img.shields.io/badge/Docs-English%7C%E4%B8%AD%E6%96%87-blue?logo=markdown"
            alt="docs"
        />
    </a>
    <a href="./LICENSE">
        <img
            src="https://img.shields.io/badge/license-Apache--2.0-black"
            alt="license"
        />
    </a>
</p>

<p align="center">
<img src="https://trendshift.io/api/badge/repositories/20310" alt="agentscope-ai%2Fagentscope | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
</p>

## Qu'est-ce qu'AgentScope ?

AgentScope est un framework d'agents prêt pour la production, facile à utiliser, avec des abstractions essentielles qui s'adaptent aux capacités croissantes des modèles et un support intégré pour le finetuning.

Nous concevons pour des LLM de plus en plus agentiques.
Notre approche exploite les capacités de raisonnement et d'utilisation d'outils des modèles
plutôt que de les contraindre avec des prompts stricts et des orchestrations rigides.

## Pourquoi utiliser AgentScope ?

- **Simple** : commencez à construire vos agents en 5 minutes avec un agent ReAct intégré, des outils, des skills, un pilotage humain dans la boucle, de la mémoire, de la planification, de la voix en temps réel, de l'évaluation et du finetuning de modèles
- **Extensible** : grand nombre d'intégrations écosystème pour les outils, la mémoire et l'observabilité ; support intégré pour MCP et A2A ; message hub pour une orchestration multi-agents flexible et des workflows
- **Prêt pour la production** : déployez et servez vos agents localement, en serverless dans le cloud, ou sur votre cluster K8s avec un support OTel intégré


<p align="center">
<img src="./assets/images/agentscope.png" width="90%" />
<br/>
L'écosystème AgentScope
</p>


## Actualités
<!-- BEGIN NEWS -->
- **[2026-03] `RELS` :** Nous avons récemment développé et publié en open source un assistant IA nommé [CoPaw](https://github.com/agentscope-ai/CoPaw) (Co Personal Agent Workstation), construit sur [AgentScope](https://github.com/agentscope-ai/agentscope), [AgentScope-Runtime](https://github.com/agentscope-ai/agentscope-runtime) et [Reme](https://github.com/agentscope-ai/ReMe).
- **[2026-02] `FEAT` :** Support de Realtime Voice Agent. [Exemple](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/realtime_voice_agent) | [Exemple Multi-Agent Realtime](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_realtime) | [Tutoriel](https://doc.agentscope.io/tutorial/task_realtime.html)
- **[2026-01] `COMM` :** Lancement des réunions bimensuelles pour partager les mises à jour de l'écosystème et les plans de développement - rejoignez-nous ! [Détails & Calendrier](https://github.com/agentscope-ai/agentscope/discussions/1126)
- **[2026-01] `FEAT` :** Support des bases de données et compression de la mémoire dans le module memory. [Exemple](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/short_term_memory/memory_compression) | [Tutoriel](https://doc.agentscope.io/tutorial/task_memory.html)
- **[2025-12] `INTG` :** Support du protocole A2A (Agent-to-Agent). [Exemple](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/a2a_agent) | [Tutoriel](https://doc.agentscope.io/tutorial/task_a2a.html)
- **[2025-12] `FEAT` :** Support TTS (Text-to-Speech). [Exemple](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/tts) | [Tutoriel](https://doc.agentscope.io/tutorial/task_tts.html)
- **[2025-11] `INTG` :** Support Anthropic Agent Skill. [Exemple](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/agent_skill) | [Tutoriel](https://doc.agentscope.io/tutorial/task_agent_skill.html)
- **[2025-11] `RELS` :** Alias-Agent pour diverses tâches réelles et Data-Juicer Agent pour le traitement de données publiés en open source. [Alias-Agent](https://github.com/agentscope-ai/agentscope-samples/tree/main/alias) | [Data-Juicer Agent](https://github.com/agentscope-ai/agentscope-samples/tree/main/data_juicer_agent)
- **[2025-11] `INTG` :** RL agentique via la bibliothèque Trinity-RFT. [Exemple](https://github.com/agentscope-ai/agentscope/tree/main/examples/tuner/model_tuning) | [Trinity-RFT](https://github.com/agentscope-ai/Trinity-RFT)
- **[2025-11] `INTG` :** ReMe pour une mémoire long terme améliorée. [Exemple](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/long_term_memory/reme)
- **[2025-11] `RELS` :** Lancement du dépôt agentscope-samples et mise à niveau d'agentscope-runtime avec déploiement Docker/K8s et sandboxes GUI alimentées par VNC. [Samples](https://github.com/agentscope-ai/agentscope-samples) | [Runtime](https://github.com/agentscope-ai/agentscope-runtime)
<!-- END NEWS -->

[Plus d'actualités →](./docs/NEWS.md)

## Communauté

Bienvenue dans notre communauté sur

| [Discord](https://discord.gg/eYMpfnkG8h)                                                                                         | DingTalk                                                                  |
|----------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| <img src="https://gw.alicdn.com/imgextra/i1/O1CN01hhD1mu1Dd3BWVUvxN_!!6000000000238-2-tps-400-400.png" width="100" height="100"> | <img src="./assets/images/dingtalk_qr_code.png" width="100" height="100"> |

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
## 📑 Table des matières

- [Démarrage rapide](#démarrage-rapide)
  - [Installation](#installation)
    - [Depuis PyPI](#depuis-pypi)
    - [Depuis les sources](#depuis-les-sources)
- [Exemple](#exemple)
  - [Hello AgentScope!](#hello-agentscope)
  - [Voice Agent](#voice-agent)
  - [Realtime Voice Agent](#realtime-voice-agent)
  - [Human-in-the-loop](#human-in-the-loop)
  - [Utilisation flexible de MCP](#utilisation-flexible-de-mcp)
  - [RL agentique](#rl-agentique)
  - [Workflows multi-agents](#workflows-multi-agents)
- [Documentation](#documentation)
- [Plus d'exemples & samples](#plus-dexemples--samples)
  - [Fonctionnalités](#fonctionnalités)
  - [Agent](#agent)
  - [Jeu](#jeu)
  - [Workflow](#workflow)
  - [Évaluation](#évaluation)
  - [Tuner](#tuner)
- [Contribution](#contribution)
- [Licence](#licence)
- [Publications](#publications)
- [Contributeurs](#contributeurs)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Démarrage rapide

### Installation

> AgentScope nécessite **Python 3.10** ou supérieur.

#### Depuis PyPI

```bash
pip install agentscope
```

Ou avec uv :

```bash
uv pip install agentscope
```

#### Depuis les sources

```bash
# Pull the source code from GitHub
git clone -b main https://github.com/agentscope-ai/agentscope.git

# Install the package in editable mode
cd agentscope

pip install -e .
# or with uv:
# uv pip install -e .
```


## Exemple

### Hello AgentScope!

Commencez avec une conversation entre l'utilisateur et un agent ReAct 🤖 nommé "Friday" !

```python
from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, execute_python_code, execute_shell_command
import os, asyncio


async def main():
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(execute_shell_command)

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You're a helpful assistant named Friday.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=True,
        ),
        memory=InMemoryMemory(),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    user = UserAgent(name="user")

    msg = None
    while True:
        msg = await agent(msg)
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break

asyncio.run(main())
```

### Voice Agent

Créez un agent ReAct avec support vocal capable de comprendre et de répondre par la parole, y compris pour jouer à un jeu de loup-garou multi-agents avec des interactions vocales.


https://github.com/user-attachments/assets/c5f05254-aff6-4375-90df-85e8da95d5da


### Realtime Voice Agent

Construisez un agent vocal en temps réel avec une interface web capable d'interagir avec les utilisateurs via l'entrée et la sortie vocales.

[Chatbot en temps réel](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/realtime_voice_agent) | [Exemple Multi-Agent Realtime](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_realtime)

https://github.com/user-attachments/assets/1b7b114b-e995-4586-9b3f-d3bb9fcd2558



### Human-in-the-loop

Support de l'interruption en temps réel dans ReActAgent : la conversation peut être interrompue via une annulation en temps réel et reprise
de manière transparente grâce à une préservation robuste de la mémoire.

<img src="./assets/images/realtime_steering_en.gif" alt="Realtime Steering" width="60%"/>

### Utilisation flexible de MCP

Utilisez des outils MCP individuels comme **fonctions appelables localement** pour composer des toolkits ou les encapsuler dans un outil plus complexe.

```python
from agentscope.mcp import HttpStatelessClient
from agentscope.tool import Toolkit
import os

async def fine_grained_mcp_control():
    # Initialize the MCP client
    client = HttpStatelessClient(
        name="gaode_mcp",
        transport="streamable_http",
        url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
    )

    # Obtain the MCP tool as a **local callable function**, and use it anywhere
    func = await client.get_callable_function(func_name="maps_geo")

    # Option 1: Call directly
    await func(address="Tiananmen Square", city="Beijing")

    # Option 2: Pass to agent as a tool
    toolkit = Toolkit()
    toolkit.register_tool_function(func)
    # ...

    # Option 3: Wrap into a more complex tool
    # ...
```

### RL agentique

Entraînez votre application agentique de manière transparente avec l'intégration du Reinforcement Learning. Nous préparons également plusieurs projets d'exemple couvrant divers scénarios :

| Exemple                                                                                          | Description                                                 | Modèle                 | Résultat d'entraînement     |
|--------------------------------------------------------------------------------------------------|-------------------------------------------------------------|------------------------|-----------------------------|
| [Math Agent](https://github.com/agentscope-ai/agentscope-samples/tree/main/tuner/math_agent)     | Affiner un agent de résolution mathématique avec raisonnement multi-étapes. | Qwen3-0.6B             | Accuracy: 75% → 85%         |
| [Frozen Lake](https://github.com/agentscope-ai/agentscope-samples/tree/main/tuner/frozen_lake)   | Entraîner un agent à naviguer dans l'environnement Frozen Lake. | Qwen2.5-3B-Instruct    | Success rate: 15% → 86%     |
| [Learn to Ask](https://github.com/agentscope-ai/agentscope-samples/tree/main/tuner/learn_to_ask) | Affiner des agents en utilisant LLM-as-a-judge pour un feedback automatisé. | Qwen2.5-7B-Instruct    | Accuracy: 47% → 92%         |
| [Email Search](https://github.com/agentscope-ai/agentscope-samples/tree/main/tuner/email_search) | Améliorer les capacités d'utilisation d'outils sans vérité terrain étiquetée. | Qwen3-4B-Instruct-2507 | Accuracy: 60%               |
| [Werewolf Game](https://github.com/agentscope-ai/agentscope-samples/tree/main/tuner/werewolves)  | Entraîner des agents pour des interactions stratégiques dans un jeu multi-agents. | Qwen2.5-7B-Instruct    | Werewolf win rate: 50% → 80% |
| [Data Augment](https://github.com/agentscope-ai/agentscope-samples/tree/main/tuner/data_augment) | Générer des données d'entraînement synthétiques pour améliorer les résultats de tuning. | Qwen3-0.6B             | AIME-24 accuracy: 20% → 60% |

### Workflows multi-agents

AgentScope fournit ``MsgHub`` et des pipelines pour simplifier les conversations multi-agents, offrant un routage de messages efficace et un partage d'informations transparent

```python
from agentscope.pipeline import MsgHub, sequential_pipeline
from agentscope.message import Msg
import asyncio

async def multi_agent_conversation():
    # Create agents
    agent1 = ...
    agent2 = ...
    agent3 = ...
    agent4 = ...

    # Create a message hub to manage multi-agent conversation
    async with MsgHub(
        participants=[agent1, agent2, agent3],
        announcement=Msg("Host", "Introduce yourselves.", "assistant")
    ) as hub:
        # Speak in a sequential manner
        await sequential_pipeline([agent1, agent2, agent3])
        # Dynamic manage the participants
        hub.add(agent4)
        hub.delete(agent3)
        await hub.broadcast(Msg("Host", "Goodbye!", "assistant"))

asyncio.run(multi_agent_conversation())
```


## Documentation

- [Tutoriel](https://doc.agentscope.io/tutorial/)
- [FAQ](https://doc.agentscope.io/tutorial/faq.html)
- [Documentation API](https://doc.agentscope.io/api/agentscope.html)

## Plus d'exemples & samples

### Fonctionnalités

- [MCP](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/mcp)
- [Anthropic Agent Skill](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/agent_skill)
- [Plan](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/plan)
- [Structured Output](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/structured_output)
- [RAG](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/rag)
- [Long-Term Memory](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/long_term_memory)
- [Session with SQLite](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/session_with_sqlite)
- [Stream Printing Messages](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/stream_printing_messages)
- [TTS](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/tts)
- [Déploiement code-first](https://github.com/agentscope-ai/agentscope/tree/main/examples/deployment/planning_agent)
- [Memory Compression](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/short_term_memory/memory_compression)

### Agent

- [ReAct Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/react_agent)
- [Voice Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/voice_agent)
- [Deep Research Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/deep_research_agent)
- [Browser-use Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/browser_agent)
- [Meta Planner Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/meta_planner_agent)
- [A2A Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/a2a_agent)
- [Realtime Voice Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/realtime_voice_agent)

### Jeu

- [Loup-garou à neuf joueurs](https://github.com/agentscope-ai/agentscope/tree/main/examples/game/werewolves)

### Workflow

- [Débat multi-agents](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_debate)
- [Conversation multi-agents](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_conversation)
- [Multi-agents concurrent](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_concurrent)
- [Conversation multi-agents en temps réel](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_realtime)

### Évaluation

- [ACEBench](https://github.com/agentscope-ai/agentscope/tree/main/examples/evaluation/ace_bench)

### Tuner

- [Affiner un ReAct Agent](https://github.com/agentscope-ai/agentscope/tree/main/examples/tuner/model_tuning)


## Contribution

Nous accueillons les contributions de la communauté ! Veuillez consulter notre [CONTRIBUTING.md](./CONTRIBUTING.md) pour les directives
sur la façon de contribuer.

## Licence

AgentScope est publié sous la licence Apache License 2.0.

## Publications

Si vous trouvez notre travail utile pour votre recherche ou votre application, veuillez citer nos articles.

- [AgentScope 1.0: A Developer-Centric Framework for Building Agentic Applications](https://arxiv.org/abs/2508.16279)

- [AgentScope: A Flexible yet Robust Multi-Agent Platform](https://arxiv.org/abs/2402.14034)

```
@article{agentscope_v1,
    author  = {Dawei Gao, Zitao Li, Yuexiang Xie, Weirui Kuang, Liuyi Yao, Bingchen Qian, Zhijian Ma, Yue Cui, Haohao Luo, Shen Li, Lu Yi, Yi Yu, Shiqi He, Zhiling Luo, Wenmeng Zhou, Zhicheng Zhang, Xuguang He, Ziqian Chen, Weikai Liao, Farruh Isakulovich Kushnazarov, Yaliang Li, Bolin Ding, Jingren Zhou}
    title   = {AgentScope 1.0: A Developer-Centric Framework for Building Agentic Applications},
    journal = {CoRR},
    volume  = {abs/2508.16279},
    year    = {2025},
}

@article{agentscope,
    author  = {Dawei Gao, Zitao Li, Xuchen Pan, Weirui Kuang, Zhijian Ma, Bingchen Qian, Fei Wei, Wenhao Zhang, Yuexiang Xie, Daoyuan Chen, Liuyi Yao, Hongyi Peng, Zeyu Zhang, Lin Zhu, Chen Cheng, Hongzhu Shi, Yaliang Li, Bolin Ding, Jingren Zhou}
    title   = {AgentScope: A Flexible yet Robust Multi-Agent Platform},
    journal = {CoRR},
    volume  = {abs/2402.14034},
    year    = {2024},
}
```

## Contributeurs

Merci à tous nos contributeurs :

<a href="https://github.com/agentscope-ai/agentscope/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=agentscope-ai/agentscope&max=999&columns=12&anon=1" />
</a>
