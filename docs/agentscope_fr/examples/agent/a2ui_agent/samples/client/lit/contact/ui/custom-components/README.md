# Guide d'intégration de composants personnalisés A2UI

Ce guide détaille comment créer, enregistrer et utiliser un composant personnalisé dans le client A2UI.

## Créer le composant

Créez un nouveau fichier de composant Lit dans `lib/src/0.8/ui/custom-components/`.
Exemple : `my-component.ts`

```typescript
import { html, css } from "lit";
import { property } from "lit/decorators.js";

import { Root } from "../root.js";

export class MyComponent extends Root {
  @property() accessor myProp: string = "Default";

  static styles = [
    ...Root.styles, // Inherit base styles
    css`
      :host {
        display: block;
        padding: 16px;
        border: 1px solid #ccc;
      }
    `,
  ];

  render() {
    return html`
      <div>
        <h2>My Custom Component</h2>
        <p>Prop value: ${this.myProp}</p>
      </div>
    `;
  }
}
```

## Enregistrer le composant

Mettez à jour `lib/src/0.8/ui/custom-components/index.ts` pour enregistrer votre nouveau composant.
Vous devez passer le nom de balise souhaité comme troisième argument.

```typescript
import { componentRegistry } from "../component-registry.js";
import { MyComponent } from "./my-component.js"; // Import your component

export function registerCustomComponents() {
  // Register with explicit tag name
  componentRegistry.register("MyComponent", MyComponent, "my-component");
}

export { MyComponent }; // Export for type usage if needed
```

## Définir le schéma (côté serveur)

Créez un schéma JSON pour les propriétés de votre composant. Celui-ci sera utilisé par le serveur pour valider les messages.
Exemple : `lib/my_component_schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "type": { "const": "object" },
    "properties": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "myProp": {
          "type": "string",
          "description": "A sample property."
        }
      },
      "required": ["myProp"]
    }
  },
  "required": ["type", "properties"]
}
```

## Utilisation dans l'application cliente

Dans votre application cliente (par ex., l'exemple `contact`), assurez-vous d'importer et d'appeler la fonction d'enregistrement.

```typescript
import { registerCustomComponents } from "@a2ui/lit/ui";

// Call this once at startup
registerCustomComponents();
```

## Remplacement de composants standard

Vous pouvez remplacer les composants A2UI standard (comme `TextField`, `Video`, `Button`) par vos propres implémentations personnalisées.

### Étapes pour le remplacement

1.  **Créez votre composant** en étendant `Root` (comme un composant personnalisé).

2.  **Assurez-vous qu'il accepte les propriétés standard** pour ce type de composant (par ex., `label` et `text` pour `TextField`).

3.  **Enregistrez-le** en utilisant le **nom de type standard** (par ex., `"TextField"`).

    ```typescript
    // 1. Define your override
    class MyPremiumTextField extends Root {
      @property() accessor label = "";
      @property() accessor text = "";

      static styles = [
        ...Root.styles,
        css`
          /* your premium styles */
        `,
      ];

      render() {
        return html`
          <div class="premium-field">
            <label>${this.label}</label>
            <input .value="${this.text}" />
          </div>
        `;
      }
    }

    // 2. Register with the STANDARD type name
    import { componentRegistry } from "@a2ui/lit/ui";
    componentRegistry.register(
      "TextField",
      MyPremiumTextField,
      "my-premium-textfield"
    );
    ```

**Résultat :**
Quand le serveur envoie un composant `TextField`, le client affichera désormais `<my-premium-textfield>` au lieu du `<a2ui-textfield>` par défaut.

## Vérification

Vous pouvez vérifier le composant en créant un simple fichier HTML de test ou en envoyant un message serveur avec le nouveau type de composant.

**Exemple de message serveur :**

```json
{
  "surfaceId": "main",
  "component": {
    "type": "MyComponent",
    "id": "comp-1",
    "properties": {
      "myProp": "Hello World"
    }
  }
}
```

## Dépannage

- **`NotSupportedError`** : Si vous voyez « constructor has already been used », assurez-vous d'avoir **supprimé** le décorateur `@customElement` de votre classe de composant.
- **Le composant ne s'affiche pas** : Vérifiez que `registerCustomComponents()` est bien appelé. Vérifiez que le nom de balise dans le DOM correspond à ce que vous avez enregistré (par ex., `<my-component>` vs `<a2ui-custom-mycomponent>`).
- **Styles manquants** : Assurez-vous que `static styles` inclut `...Root.styles`.
