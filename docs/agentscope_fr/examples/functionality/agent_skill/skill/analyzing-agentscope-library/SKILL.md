---
name: Analyzing AgentScope Library
description: This skill provides a way to retrieve information from the AgentScope library for analysis and decision-making.
---

# Analyser la bibliothèque AgentScope

## Vue d'ensemble

Ce guide couvre les opérations essentielles pour récupérer et répondre aux questions sur la bibliothèque AgentScope.
Si vous devez répondre à des questions concernant la bibliothèque AgentScope, ou rechercher des informations spécifiques, des fonctions/classes,
des exemples ou des conseils, ce skill vous aidera à y parvenir.

## Démarrage rapide

Le skill fournit les scripts clés suivants :

- Rechercher des conseils dans le tutoriel AgentScope.
- Rechercher des exemples officiels et des implémentations recommandées fournies par AgentScope.
- Une interface rapide pour visualiser la bibliothèque Python d'AgentScope en donnant un nom de module (par ex. agentscope), et retourner les sous-modules, classes et fonctions du module.

Lorsqu'on vous pose une question liée à AgentScope, vous pouvez suivre les étapes ci-dessous pour trouver les informations pertinentes :

Décidez d'abord lequel des trois scripts utiliser en fonction de la question de l'utilisateur.
- Si l'utilisateur pose des questions de type « comment utiliser », utilisez le script « Search for Guidance » pour trouver le tutoriel pertinent
- Si l'utilisateur pose des questions de type « comment implémenter/construire », recherchez d'abord les exemples pertinents. Si non trouvé, alors
  considérez quelles fonctions sont nécessaires et recherchez dans le guide/tutoriel
- Si l'utilisateur pose des questions de type « comment initialiser », recherchez d'abord les tutoriels pertinents. Si non trouvé, alors
  envisagez de rechercher les modules, classes ou fonctions correspondants dans la bibliothèque.


### Rechercher des exemples

Demandez d'abord la permission de l'utilisateur pour cloner le dépôt GitHub d'agentscope si vous ne l'avez pas encore fait :

```bash
git clone -b main https://github.com/agentscope-ai/agentscope
```

Dans ce dépôt, le dossier `examples` contient divers exemples démontrant comment utiliser les différentes fonctionnalités de la
bibliothèque AgentScope.
Ils sont organisés dans une structure arborescente par différentes fonctionnalités. Vous devez utiliser des commandes shell comme `ls` ou `cat` pour
naviguer et visualiser les exemples. Évitez d'utiliser la commande `find` pour rechercher des exemples, car le nom des fichiers
d'exemple peut ne pas être directement lié à la fonctionnalité recherchée.

### Rechercher des conseils

De même, assurez-vous d'abord d'avoir cloné le dépôt GitHub d'agentscope.

Le tutoriel source d'agentscope se trouve dans le dossier `docs/tutorials` du dépôt GitHub d'agentscope. Il est
organisé par les différentes sections. Pour rechercher des conseils, allez dans le dossier `docs/tutorials` et visualisez les fichiers de tutoriel
avec des commandes shell comme `ls` ou `cat`.


### Rechercher des modules ciblés

Assurez-vous d'abord d'avoir installé la bibliothèque agentscope dans votre environnement :

```bash
pip list | grep agentscope
```

Si non installé, demandez la permission de l'utilisateur pour l'installer avec la commande :

```bash
pip install agentscope
```

Puis, exécutez le script suivant pour rechercher des modules, classes ou fonctions spécifiques. Il est suggéré de commencer avec
`agentscope` comme nom de module racine, puis de spécifier le nom du sous-module que vous souhaitez rechercher.

```bash
python view_agentscope_module.py --module agentscope
```

Pour l'utilisation détaillée, veuillez vous référer au script `./view_agentscope_module.py` (situé dans le même dossier que ce
fichier SKILL.md).
