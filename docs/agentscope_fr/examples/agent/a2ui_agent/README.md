# A2UI dans AgentScope

[A2UI (Agent-to-Agent UI)](https://github.com/google/A2UI) est un protocole permettant aux agents d'envoyer
des interfaces utilisateur interactives en streaming aux clients. Il permet aux LLM de générer des définitions
d'UI déclaratives et indépendantes de la plateforme que les clients peuvent rendre progressivement en utilisant
des ensembles de widgets natifs.

Dans cet exemple, nous démontrons comment intégrer A2UI dans un agent ReAct dans AgentScope. Cette
implémentation est basée sur les exemples d'agents A2UI officiels, adaptés pour utiliser le framework
d'agents d'AgentScope.

Plus précisément, nous avons :

1. **Réimplémenté l'agent avec AgentScope** : La partie agent des exemples A2UI officiels a
   été réimplémentée en utilisant le `ReActAgent` d'AgentScope, offrant une expérience de
   développement plus familière et intégrée pour les utilisateurs d'AgentScope.

2. **Exposition progressive du schéma et des templates via les skills** : Pour aider l'agent à apprendre et générer
   des interfaces conformes à A2UI, nous utilisons le système de skills d'AgentScope pour exposer progressivement le
   schéma A2UI et les templates d'UI. L'agent peut charger dynamiquement ces ressources via le
   skill `A2UI_response_generator`, lui permettant de comprendre les définitions de composants et d'apprendre à partir
   de structures d'UI exemples.

## Note sur les dépendances externes

Les répertoires suivants dans cet exemple contiennent du contenu provenant du [dépôt Google A2UI](https://github.com/google/A2UI) :

- **`samples/client/`** : Applications clientes exemples A2UI

**Statut des packages NPM** : À ce jour, les bibliothèques clientes A2UI (`@a2ui/lit` et `@a2ui/angular`) ne sont **pas encore publiées sur NPM**. Selon le [guide officiel de configuration du client A2UI](https://a2ui.org/guides/client-setup/#renderers) : « La bibliothèque cliente Lit n'est pas encore publiée sur NPM. Revenez dans les prochains jours. »

Par conséquent, ces dépendances sont actuellement incluses dans ce dépôt d'exemple en utilisant des chemins de fichiers locaux (par ex., `"@a2ui/lit": "file:../../../../renderers/lit"` dans les fichiers `package.json`). Cela reflète l'approche utilisée dans le [dépôt officiel A2UI](https://github.com/google/A2UI), où les renderers et les exemples utilisent également des chemins de fichiers locaux pour se référencer mutuellement. De plus, la tâche `copy-spec` dans `renderers/lit/package.json` copie les fichiers depuis le répertoire local `specification/` pendant le processus de build.

**Plans futurs** : Une fois ces bibliothèques publiées sur NPM, nous prévoyons de migrer progressivement vers l'utilisation des packages NPM officiels et de supprimer ces répertoires inclus localement.

## Démarrage rapide

Téléchargez les packages a2ui et agentscope dans le même répertoire

```bash
git clone https://github.com/google/A2UI.git
git clone -b main https://github.com/agentscope-ai/agentscope.git
# copy the renders and specification directory to AgentScope/examples/agent/a2ui_agent
cp -r A2UI/renderers AgentScope/examples/agent/a2ui_agent
cp -r A2UI/specification AgentScope/examples/agent/a2ui_agent
```


Ensuite, naviguez vers le répertoire client et lancez la démo du restaurant finder :

```bash
cd AgentScope/examples/agent/a2ui_agent/samples/client/lit
npm run demo:restaurant
```

Cette commande va :
- Installer les dépendances et construire le renderer A2UI
- Démarrer le serveur A2A (agent AgentScope) pour le restaurant finder
- Lancer l'application cliente dans votre navigateur

> Note :
> - L'exemple est construit avec le modèle de chat DashScope. Assurez-vous de définir votre variable d'environnement
>   `DASHSCOPE_API_KEY` avant de lancer la démo.
> - Si vous utilisez des modèles de la série Qwen, nous recommandons d'utiliser `qwen3-max` pour de meilleures performances
>   dans la génération de réponses JSON conformes à A2UI.
> - La génération de réponses JSON d'UI peut prendre un certain temps, généralement 1 à 2 minutes, car l'agent doit
>   traiter le schéma, les exemples et générer des structures d'UI complexes.
> - La démo utilise le catalogue A2UI standard. Le support des catalogues personnalisés et inline est en cours
>   de développement.

## Feuille de route

Le focus principal d'AgentScope à l'avenir sera d'améliorer **comment les agents fonctionnent** avec A2UI. Le
workflow vers lequel nous travaillons est :

```
User Input → Agent Logic → LLM → A2UI JSON
```

Nos efforts d'optimisation se concentreront sur :

- **Agent Logic** : Améliorer la façon dont les agents traitent les entrées et orchestrent la génération de messages
  JSON A2UI


- **Gérer les interactions utilisateur depuis le client** : Permettre aux agents de traiter et répondre correctement aux
  interactions utilisateur depuis le client (comme les clics de bouton, les soumissions de formulaire), en les traitant comme de nouvelles
  entrées utilisateur pour créer une boucle interactive continue

**Approche actuelle** : La méthode basée sur les skills que nous avons implémentée dans cet exemple est notre première étape
vers cet objectif. En utilisant le système de skills d'AgentScope pour exposer progressivement le schéma A2UI et
les templates, les agents peuvent apprendre à générer des structures d'UI conformes. Les améliorations futures se concentreront sur
la rationalisation de ce processus et le rendre plus intuitif pour les développeurs de construire des agents compatibles A2UI.

**Prochaines étapes pour l'amélioration de l'Agent Logic**

- **Améliorations des skills d'agent** :
  - Support de l'ajout flexible de schémas : Permettre aux développeurs d'ajouter et personnaliser facilement des schémas sans
    modifier le code core du skill
  - Séparer les schémas et exemples dans des dossiers dédiés : Organiser les définitions de schémas et les templates
    d'exemples dans des répertoires distincts pour une meilleure maintenabilité et une structure plus claire

- **Gestion du contexte en Memory pour le long contexte A2UI** :
  - Actuellement, les messages A2UI sont extrêmement longs, ce qui rend les interactions multi-tours inefficaces
    et dégrade la qualité des réponses de l'agent. Nous prévoyons d'implémenter de meilleures stratégies de gestion
    du contexte pour gérer ces messages longs et améliorer la qualité des conversations multi-tours.

- **Suivre les mises à jour du protocole A2UI** :
  - Nous suivrons les mises à jour du protocole A2UI et ferons les ajustements correspondants. Par exemple, nous prévoyons de
    supporter le streaming de JSON d'UI introduit dans A2UI v0.9.
