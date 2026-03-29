# Nine-Player Werewolves Game

Ceci est un exemple de jeu de Loups-Garous à neuf joueurs construit avec AgentScope, mettant en avant les **interactions multi-agents**,
le **gameplay basé sur les rôles** et la **gestion des sorties structurées**.
Plus précisément, ce jeu est composé de

- trois villageois,
- trois loups-garous,
- une voyante,
- une sorcière et
- un chasseur.

## Changelog

- 2025-10 : Nous mettons à jour l'exemple pour prendre en charge davantage de fonctionnalités :
    - Permettre aux joueurs morts de laisser des messages.
    - Prise en charge du chinois.
    - Prise en charge du **jeu continu** en chargeant et sauvegardant les états de session, afin que les mêmes agents puissent jouer plusieurs parties et continuer à apprendre et optimiser leurs stratégies.


## Démarrage rapide

Exécutez la commande suivante pour lancer le jeu, en vous assurant d'avoir défini votre clé API DashScope comme variable d'environnement.

```bash
python main.py
```

> Note :
> - Vous pouvez ajuster la langue, le modèle et d'autres paramètres dans `main.py`.
> - Différents modèles peuvent produire des expériences de jeu différentes.

L'exécution de l'exemple avec AgentScope Studio offre une expérience plus interactive.

- Vidéo de démonstration en chinois (cliquez pour lire) :

[![Werewolf Game in Chinese](https://img.alicdn.com/imgextra/i3/6000000007235/O1CN011pK6Be23JgcdLWmLX_!!6000000007235-0-tbvideo.jpg)](https://cloud.video.taobao.com/vod/KxyR66_CWaWwu76OPTvOV2Ye1Gas3i5p4molJtzhn_s.mp4)

- Vidéo de démonstration en anglais (cliquez pour lire) :

[![Werewolf Game in English](https://img.alicdn.com/imgextra/i3/6000000007389/O1CN011alyGK24SDcFBzHea_!!6000000007389-0-tbvideo.jpg)](https://cloud.video.taobao.com/vod/bMiRTfxPg2vm76wEoaIP2eJfkCi8CUExHRas-1LyK1I.mp4)

## Détails

Le jeu est construit avec le ``ReActAgent`` d'AgentScope, en utilisant sa capacité à générer des sorties structurées pour
contrôler le déroulement du jeu et les interactions.
Nous utilisons également le ``MsgHub`` et les pipelines d'AgentScope pour gérer les interactions complexes comme les discussions et les votes.
Il est très intéressant de voir comment les agents jouent au jeu du Loup-Garou avec différents rôles et objectifs.

# Utilisation avancée

## Changer la langue

Le jeu se joue en anglais par défaut. Il suffit de décommenter la ligne suivante dans `game.py` pour passer en chinois.

```python
# from prompt import ChinesePrompts as Prompts
```

## Jouer avec les agents

Vous pouvez remplacer l'un des agents par un `UserAgent` pour jouer avec les agents IA.

## Changer de modèles

Il suffit de modifier le paramètre `model` dans `main.py` pour essayer différents modèles. Notez que vous devez changer le formatter en même temps pour correspondre au format de sortie du modèle.

## Activer le Text-to-Speech (TTS)

Le jeu prend en charge la fonctionnalité Text-to-Speech. Pour activer le TTS :

1. **Dans `main.py`** :
   - Décommentez l'instruction d'import :
     ```python
     import random
     from agentscope.tts import DashScopeTTSModel
     ```
   - Décommentez le paramètre `tts_model` dans la fonction `get_official_agents` :
     ```python
     tts_model=DashScopeTTSModel(
         api_key=os.environ.get("DASHSCOPE_API_KEY"),
         model_name="qwen3-tts-flash",
         voice=random.choice(["Cherry", "Serena", "Ethan", "Chelsie"]),
         stream=True,
     ),
     ```

2. **Dans `game.py`** (optionnel, pour le TTS du modérateur) :
   - Décommentez l'instruction d'import :
     ```python
     import random
     from agentscope.tts import DashScopeTTSModel
     ```
   - Décommentez le paramètre `tts_model` dans l'initialisation du `moderator` :
     ```python
     tts_model=DashScopeTTSModel(
         api_key=os.environ.get("DASHSCOPE_API_KEY"),
         model_name="qwen3-tts-flash",
         voice=random.choice(["Cherry", "Serena", "Ethan", "Chelsie"]),
         stream=True,
     ),
     ```

3. **Configurez votre clé API** :
   - Assurez-vous d'avoir défini la variable d'environnement `DASHSCOPE_API_KEY`.

Après avoir activé le TTS, le jeu synthétisera la parole pour les messages des joueurs et les annonces du modérateur, offrant une expérience audio plus immersive.

## Pour aller plus loin

- [Structured Output](https://doc.agentscope.io/tutorial/task_agent.html#structured-output)
- [MsgHub and Pipelines](https://doc.agentscope.io/tutorial/task_pipeline.html)
- [Prompt Formatter](https://doc.agentscope.io/tutorial/task_prompt.html)
- [AgentScope Studio](https://doc.agentscope.io/tutorial/task_studio.html)
- [TTS](https://doc.agentscope.io/tutorial/task_tts.html)
