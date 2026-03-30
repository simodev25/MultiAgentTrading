# Voice Agent

> Il s'agit d'une fonctionnalité expérimentale dans AgentScope.

Cet exemple montre comment créer un agent vocal en utilisant AgentScope avec le modèle Qwen-Omni, offrant des capacités de sortie à la fois textuelles et audio.

> **Note** :
>  - Qwen-Omni peut ne pas générer d'appels d'outils lorsque la sortie audio est activée.
>  - Cet exemple prend en charge les modèles DashScope `Qwen-Omni` et OpenAI `GPT-4o Audio`. Vous pouvez changer de modèle en modifiant le paramètre `model` dans `main.py`.
>  - Nous n'avons pas encore testé vLLM. Les contributions sont les bienvenues !

## Démarrage rapide

Assurez-vous d'avoir installé agentscope et défini ``DASHSCOPE_API_KEY`` dans vos variables d'environnement.

Exécutez les commandes suivantes pour configurer et lancer l'exemple :

```bash
python main.py
```
