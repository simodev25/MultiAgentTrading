# Guide de theming et de configuration A2UI

Ce guide explique comment l'Universal App Shell gère le theming et comment ajouter de nouvelles applications exemples de manière transparente.

## Vue d'ensemble de l'architecture

Le système de styles est construit sur deux couches distinctes :

### 1. **Couche de base (`default-theme.ts`)**

- **Rôle** : Styles structurels et fonctionnels.
- **Ce qu'il fait** : Mappe les composants A2UI (comme `Text`, `Card`, `Row`) à des classes CSS utilitaires fonctionnelles (par ex., `layout-w-100`, `typography-f-sf`).
- **Quand le modifier** : Rarement. Seulement si vous devez changer le comportement de mise en page fondamental d'un composant dans toutes les applications shell.

### 2. **Couche de configuration (`configs/*.ts`)**

- **Rôle** : Identité de l'application et remplacements de marque.
- **Ce qu'il fait** : Permet des remplacements de thème au niveau de l'application.
- **Mécanisme clé** : L'interface `AppConfig` vous permet de fournir un nouveau thème en définissant des éléments dans la propriété `theme`.
- **Quand le modifier** : Chaque fois que vous ajoutez une nouvelle application et souhaitez changer le thème d'une application par rapport au thème par défaut fourni avec le shell.

---

## Comment ajouter une nouvelle application exemple

Suivez ces étapes pour ajouter une nouvelle application (par ex., « Flight Booker ») avec son propre thème unique.

### Étape 1 : Créer la configuration

Créez un nouveau fichier `configs/flights.ts` :

```typescript
import { AppConfig } from "./types.js";
import { cloneDefaultTheme } from "../theme/clone-default-theme.js";

const theme = cloneDefaultTheme();
// Set your variables, e.g., theme.components.Card = { 'color-bgc-n100': true }

export const config: AppConfig = {
  key: "flights",
  title: "Flight Booker",
  heroImage: "/hero-flights.png",
  heroImageDark: "/hero-flights-dark.png", // Optional
  placeholder: "Where do you want to go?",
  loadingText: ["Checking availability...", "Finding best rates..."],
  serverUrl: "http://localhost:10004", // Your agent's URL
  theme, // Apply the theme.
};
```

### Étape 2 : Enregistrer la configuration

Mettez à jour `app.ts` pour inclure votre nouvelle configuration :

```typescript
import { config as flightsConfig } from "./configs/flights.js";

const configs: Record<string, AppConfig> = {
  restaurant: restaurantConfig,
  contacts: contactsConfig,
  flights: flightsConfig, // Add this line
};
```

### Étape 3 : Exécuter

Accédez à votre nouvelle application en ajoutant le paramètre de requête `app` :
`http://localhost:5173/?app=flights`

L'App Shell va automatiquement :

1.  Charger votre configuration `flights`.
2.  Appliquer votre thème au contexte de thème de la racine A2UI.
3.  Se connecter à votre `serverUrl` spécifié.

---

## Référence : Leviers de style

Cette section liste les « leviers » de style disponibles (classes utilitaires) que vous pouvez utiliser dans votre fichier `theme.ts` ou directement dans vos composants. Ceux-ci sont définis dans la bibliothèque core (`renderers/lit/src/0.8/styles`).

### 1. Layout (`layout-`)

**Source :** `styles/layout.ts`

| Catégorie       | Préfixe       | Échelle/Valeurs                             | Exemples                                                    |
| :-------------- | :------------ | :------------------------------------------ | :---------------------------------------------------------- |
| **Padding**     | `layout-p-`   | 0-24 (1 = 4px)                              | `layout-p-4` (16px), `layout-pt-2` (Top 8px), `layout-px-4` |
| **Margin**      | `layout-m-`   | 0-24 (1 = 4px)                              | `layout-m-0`, `layout-mb-4` (Bottom 16px), `layout-mx-auto` |
| **Gap**         | `layout-g-`   | 0-24 (1 = 4px)                              | `layout-g-2` (8px), `layout-g-4` (16px)                     |
| **Width**       | `layout-w-`   | 10-100 (Pourcentage)                        | `layout-w-100` (100%), `layout-w-50` (50%)                  |
| **Width (Px)**  | `layout-wp-`  | 0-15 (1 = 4px)                              | `layout-wp-10` (40px)                                       |
| **Height**      | `layout-h-`   | 10-100 (Pourcentage)                        | `layout-h-100` (100%)                                       |
| **Height (Px)** | `layout-hp-`  | 0-15 (1 = 4px)                              | `layout-hp-10` (40px)                                       |
| **Display**     | `layout-dsp-` | `none`, `block`, `grid`, `flex`, `iflex`    | `layout-dsp-flexhor` (Row), `layout-dsp-flexvert` (Col)     |
| **Alignment**   | `layout-al-`  | `fs` (Start), `fe` (End), `c` (Center)      | `layout-al-c` (Align Items Center)                          |
| **Justify**     | `layout-sp-`  | `c` (Center), `bt` (Between), `ev` (Evenly) | `layout-sp-bt` (Justify Content Space Between)              |
| **Flex**        | `layout-flx-` | `0` (None), `1` (Grow)                      | `layout-flx-1` (Flex Grow 1)                                |
| **Position**    | `layout-pos-` | `a` (Absolute), `rel` (Relative)            | `layout-pos-rel`                                            |

### 2. Colors (`color-`)

**Source :** `styles/colors.ts`

| Catégorie        | Préfixe      | Échelle/Valeurs     | Exemples                                                              |
| :--------------- | :----------- | :------------------ | :-------------------------------------------------------------------- |
| **Text Color**   | `color-c-`   | Palette Key + Shade | `color-c-p50` (Primary), `color-c-n10` (Black), `color-c-e40` (Error) |
| **Background**   | `color-bgc-` | Palette Key + Shade | `color-bgc-p100` (White/Lightest), `color-bgc-s30` (Secondary Dark)   |
| **Border Color** | `color-bc-`  | Palette Key + Shade | `color-bc-p60` (Primary Border)                                       |

**Clés de palette :**

- `p` = Primary (Marque)
- `s` = Secondary
- `t` = Tertiary
- `n` = Neutral (Gris)
- `nv` = Neutral Variant
- `e` = Error

**Nuances :** 0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 90, 95, 98, 99, 100

### 3. Typography (`typography-`)

**Source :** `styles/type.ts`

| Catégorie           | Préfixe          | Échelle/Valeurs                           | Exemples                                                                             |
| :------------------ | :--------------- | :---------------------------------------- | :----------------------------------------------------------------------------------- |
| **Font Family**     | `typography-f-`  | `sf` (Sans/Flex), `s` (Serif), `c` (Code) | `typography-f-sf` (System UI / Outfit)                                               |
| **Weight**          | `typography-w-`  | 100-900                                   | `typography-w-400` (Regular), `typography-w-500` (Medium), `typography-w-700` (Bold) |
| **Size (Body)**     | `typography-sz-` | `bs`, `bm`, `bl`                          | `typography-sz-bm` (Body Medium - 14px)                                              |
| **Size (Title)**    | `typography-sz-` | `ts`, `tm`, `tl`                          | `typography-sz-tl` (Title Large - 22px)                                              |
| **Size (Headline)** | `typography-sz-` | `hs`, `hm`, `hl`                          | `typography-sz-hl` (Headline Large - 32px)                                           |
| **Size (Display)**  | `typography-sz-` | `ds`, `dm`, `dl`                          | `typography-sz-dl` (Display Large - 57px)                                            |
| **Align**           | `typography-ta-` | `s` (Start), `c` (Center)                 | `typography-ta-c`                                                                    |

### 4. Borders (`border-`)

**Source :** `styles/border.ts`

| Catégorie  | Préfixe      | Échelle/Valeurs | Exemples                                              |
| :--------- | :----------- | :-------------- | :---------------------------------------------------- |
| **Radius** | `border-br-` | 0-24 (1 = 4px)  | `border-br-4` (16px), `border-br-50pc` (50% / Circle) |
| **Width**  | `border-bw-` | 0-24 (Pixels)   | `border-bw-1` (1px), `border-bw-2` (2px)              |
| **Style**  | `border-bs-` | `s` (Solid)     | `border-bs-s`                                         |

### 5. Comportement et opacité

**Source :** `styles/behavior.ts`, `styles/opacity.ts`

| Catégorie         | Préfixe        | Échelle/Valeurs                        | Exemples                                |
| :---------------- | :------------- | :------------------------------------- | :-------------------------------------- |
| **Hover Opacity** | `behavior-ho-` | 0-100 (Step 5)                         | `behavior-ho-80` (Opacity 0.8 on hover) |
| **Opacity**       | `opacity-el-`  | 0-100 (Step 5)                         | `opacity-el-50` (Opacity 0.5)           |
| **Overflow**      | `behavior-o-`  | `s` (Scroll), `a` (Auto), `h` (Hidden) | `behavior-o-h`                          |
| **Scrollbar**     | `behavior-sw-` | `n` (None)                             | `behavior-sw-n`                         |

### 6. Icons

**Source :** `styles/icons.ts`

- Classe : `.g-icon`
- Variantes : `.filled`, `.filled-heavy`
- Utilisation : `<span class="g-icon">icon_name</span>`
