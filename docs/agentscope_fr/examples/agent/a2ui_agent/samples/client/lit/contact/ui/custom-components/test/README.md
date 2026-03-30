# Tests de vérification de l'exemple contact

Ce répertoire contient des tests pour vérifier l'intégration des composants personnalisés spécifiquement dans l'environnement de l'application exemple `contact`.

## Comment exécuter

Ces tests s'exécutent via le serveur de développement Vite utilisé par l'exemple contact.

### 1. Démarrer le serveur de développement
Depuis le répertoire `web/lit/samples/contact`, exécutez :

```bash
npm run dev
```

### 2. Accéder aux tests
Ouvrez votre navigateur et naviguez vers le serveur local (généralement le port 5173) :

-   **Test de remplacement de composant** :
    [http://localhost:5173/ui/custom-components/test/override-test.html](http://localhost:5173/ui/custom-components/test/override-test.html)
    *Vérifie qu'un composant standard (TextField) peut être remplacé par une implémentation personnalisée.*

-   **Test d'intégration du graphe hiérarchique** :
    [http://localhost:5173/ui/custom-components/test/hierarchy-test.html](http://localhost:5173/ui/custom-components/test/hierarchy-test.html)
    *Vérifie que le composant HierarchyGraph s'affiche correctement dans l'environnement de build de l'application contact.*

## Fichiers

-   `override-test.html` & `override-test.ts` : Implémente et teste un remplacement personnalisé de `TextField`.
-   `hierarchy-test.html` : Teste le composant `HierarchyGraph`.
