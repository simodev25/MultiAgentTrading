# A2UI Generator

Ceci est une UI pour générer et visualiser les réponses A2UI.

## Prérequis

1. [nodejs](https://nodejs.org/en)

## Exécution

Cet exemple dépend du renderer Lit. Avant d'exécuter cet exemple, vous devez construire le renderer.

1. **Construire le renderer :**
   ```bash
   cd ../../../renderers/lit
   npm install
   npm run build
   ```

2. **Exécuter cet exemple :**
   ```bash
   cd - # back to the sample directory
   npm install
   ```

3. **Démarrer les serveurs :**
   - Exécutez le [serveur A2A](../../../general_agent/)
   - Démarrez le serveur de développement : `npm run dev`

Après avoir démarré le serveur de développement, vous pouvez ouvrir http://localhost:5173/ pour voir l'exemple.

Important : Le code d'exemple fourni est à des fins de démonstration et illustre les mécaniques d'A2UI et du protocole Agent-to-Agent (A2A). Lors de la construction d'applications en production, il est essentiel de traiter tout agent opérant en dehors de votre contrôle direct comme une entité potentiellement non fiable.

Toutes les données opérationnelles reçues d'un agent externe — y compris son AgentCard, ses messages, ses artefacts et ses statuts de tâche — doivent être traitées comme des entrées non fiables. Par exemple, un agent malveillant pourrait fournir des données conçues dans ses champs (par ex., name, skills.description) qui, si elles sont utilisées sans assainissement pour construire des prompts pour un Large Language Model (LLM), pourraient exposer votre application à des attaques par injection de prompt.

De même, toute définition d'UI ou flux de données reçu doit être traité comme non fiable. Des agents malveillants pourraient tenter d'usurper des interfaces légitimes pour tromper les utilisateurs (phishing), injecter des scripts malveillants via les valeurs de propriétés (XSS), ou générer une complexité de mise en page excessive pour dégrader les performances du client (DoS). Si votre application supporte du contenu embarqué optionnel (comme des iframes ou des vues web), des précautions supplémentaires doivent être prises pour prévenir l'exposition à des sites externes malveillants.

Responsabilité du développeur : Le fait de ne pas valider correctement les données et de ne pas isoler strictement le contenu rendu peut introduire des vulnérabilités graves. Les développeurs sont responsables de la mise en œuvre de mesures de sécurité appropriées — telles que l'assainissement des entrées, les Content Security Policies (CSP), l'isolation stricte pour le contenu embarqué optionnel et la gestion sécurisée des identifiants — pour protéger leurs systèmes et leurs utilisateurs.
