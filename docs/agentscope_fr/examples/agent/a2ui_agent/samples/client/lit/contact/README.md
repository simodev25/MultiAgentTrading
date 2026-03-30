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
   - Exécutez le [serveur A2A](../../../agent/adk/contact_lookup/)
   - Démarrez le serveur de développement : `npm run dev`

Après avoir démarré le serveur de développement, vous pouvez ouvrir http://localhost:5173/ pour voir l'exemple.
