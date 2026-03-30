---
name: A2UI_response_generator
description: A skill that can retrieve A2UI UI JSON schematics and UI templates that best show the response. This skill is essential and must be used before generating A2UI (Agent to UI) JSON responses.
---

# Skill de génération de réponses A2UI

## Vue d'ensemble

Ce skill est **essentiel et doit être utilisé avant de générer des réponses JSON A2UI (Agent to UI)**. Il permet aux agents de récupérer les schémas JSON d'UI A2UI et les templates d'UI qui présentent au mieux la réponse, permettant la génération de réponses d'UI riches et interactives en utilisant le protocole A2UI.

Au lieu de charger l'intégralité du schéma A2UI et de tous les exemples d'un coup, ce skill permet aux agents de **récupérer** uniquement les templates d'UI et les schémas pertinents en fonction du contenu de la réponse. Le protocole A2UI définit un format basé sur JSON pour construire et mettre à jour dynamiquement les interfaces utilisateur. En décomposant les exemples en templates modulaires, les agents peuvent :

1. Récupérer les schémas JSON d'UI A2UI appropriés pour la validation et la référence structurelle
2. Sélectionner les templates d'UI qui correspondent et affichent le mieux le contenu de la réponse
3. Réduire l'utilisation de tokens de prompt en ne chargeant que les templates nécessaires
4. S'étendre facilement avec de nouveaux templates d'UI pour différents domaines

### Structure des fichiers

```
A2UI_response_generator/
├── SKILL.md                          # This file - main skill documentation
├── view_a2ui_schema.py               # Tool to view the complete A2UI schema (schema included in file)
├── view_a2ui_examples.py             # Tool to view UI template examples (templates included in file)
├── __init__.py                       # Package initialization
├── schema/                           # A2UI schema definitions
│   ├── __init__.py
│   └── base_schema.py                # Base A2UI schema
└── UI_templete_examples/             # UI template examples
    ├── __init__.py
    ├── booking_form.py               # Booking form template
    ├── contact_form.py               # Contact form template
    ├── email_compose_form.py         # Email compose form template
    ├── error_message.py              # Error message template
    ├── info_message.py               # Info message template
    ├── item_detail_card_with_image.py # Item detail card with image template
    ├── profile_view.py               # Profile view template
    ├── search_filter_form.py         # Search filter form template
    ├── simple_column_list_without_image.py # Simple list template
    ├── single_column_list.py         # Single column list template
    ├── success_confirmation_with_image.py # Success confirmation template
    └── two_column_list.py            # Two column list template
```

## Démarrage rapide

Lorsqu'il est nécessaire de générer du JSON d'UI, suivez ces étapes :

Important : Veuillez utiliser l'outil `execute_shell_command` pour exécuter les commandes Python.

### Étape 1 : Charger le schéma A2UI

Exécutez le script suivant pour charger le schéma A2UI complet.

Actuellement, la `schema_category` disponible est `BASE_SCHEMA`.

**Utilisez l'outil `execute_shell_command` pour exécuter (assurez-vous d'être dans le répertoire du skill) :**
```bash
python -m view_a2ui_schema --schema_category BASE_SCHEMA
```

**Utilisation** : `python -m view_a2ui_schema --schema_category [schema_category]` - Charge la définition du schéma A2UI pour valider la structure des réponses JSON A2UI. Actuellement, seul `BASE_SCHEMA` est disponible.

Pour l'utilisation détaillée, veuillez vous référer au script `./view_a2ui_schema.py` (situé dans le même dossier que ce fichier SKILL.md).

### Étape 2 : Sélectionner les exemples de templates d'UI

Sélectionnez les exemples de templates d'UI appropriés en fonction du contenu de votre réponse.

**IMPORTANT** : Vous DEVEZ utiliser les **noms de templates exacts** listés dans le tableau « Available UI template examples » ci-dessous. N'utilisez PAS de noms de catégories génériques comme 'list', 'form', 'confirmation' ou 'detail'. Vous DEVEZ utiliser le nom de template spécifique (par ex., `SINGLE_COLUMN_LIST_WITH_IMAGE`, `BOOKING_FORM_WITH_IMAGE`, etc.).

**Utilisez l'outil `execute_shell_command` pour exécuter (assurez-vous d'être dans le répertoire du skill) :**
```bash
python -m view_a2ui_examples --template_name SINGLE_COLUMN_LIST_WITH_IMAGE
```

**Utilisation** : `python -m view_a2ui_examples --template_name [template_name]` - Charge un exemple de template d'UI pour référence lors de la génération de réponses A2UI. Accepte un seul nom de template depuis le tableau de templates disponibles ci-dessous.

**Exemples de templates d'UI disponibles** (quand `schema_category` est `BASE_SCHEME`, vous DEVEZ utiliser ces noms exacts, sensibles à la casse) :

| Nom du template | Cas d'utilisation | Guide de sélection | Support image |
| --- | --- | --- | --- |
| `SINGLE_COLUMN_LIST_WITH_IMAGE` | Liste verticale avec cartes détaillées (pour ≤5 éléments) | Utilisez pour **l'affichage de liste** avec ≤5 éléments | ✅ Avec image |
| `TWO_COLUMN_LIST_WITH_IMAGE` | Mise en page en grille avec cartes (pour >5 éléments) | Utilisez pour **l'affichage de liste** avec >5 éléments | ✅ Avec image |
| `SIMPLE_LIST` | Liste compacte sans images | Utilisez pour les **listes compactes** sans images | ❌ Sans image |
| `SELECTION_CARD` | Questions à choix multiples | Utilisez pour les **questions à choix multiples** | ❌ Sans image |
| `MULTIPLE_SELECTION_CARDS` | Cartes de sélection multiples dans une liste | Utilisez pour les **cartes de sélection multiples** affichées ensemble | ❌ Sans image |
| `BOOKING_FORM_WITH_IMAGE` | Réservation, booking, inscription | Utilisez pour les **formulaires de réservation/booking** | ✅ Avec image |
| `SEARCH_FILTER_FORM_WITH_IMAGE` | Formulaires de recherche avec filtres | Utilisez pour les **formulaires de recherche avec filtres** | ❌ Sans image |
| `CONTACT_FORM_WITH_IMAGE` | Formulaires de contact ou de feedback | Utilisez pour les **formulaires de contact/feedback** | ❌ Sans image |
| `EMAIL_COMPOSE_FORM_WITH_IMAGE` | Formulaires de composition d'email | Utilisez pour les **formulaires de composition d'email** | ❌ Sans image |
| `SUCCESS_CONFIRMATION_WITH_IMAGE` | Message de succès après une action | Utilisez pour les **confirmations de succès** | ✅ Avec image |
| `ERROR_MESSAGE` | Affichage d'erreurs ou d'avertissements | Utilisez pour les **messages d'erreur** | ❌ Sans image |
| `INFO_MESSAGE` | Messages informationnels | Utilisez pour les **messages d'information** | ❌ Sans image |
| `ITEM_DETAIL_CARD` | Vue détaillée d'un seul élément | Utilisez pour les **vues détaillées d'éléments** | ✅ Avec image |
| `ITEM_DETAIL_CARD_WITH_IMAGE` | Vue détaillée d'un seul élément avec image | Utilisez pour les **vues détaillées d'éléments** avec images | ✅ Avec image |
| `PROFILE_VIEW` | Affichage de profil utilisateur ou d'entité | Utilisez pour les **vues de profil** | ✅ Avec image |

**Rappel** : Utilisez toujours les noms de templates exacts du tableau ci-dessus. N'utilisez jamais de termes génériques comme 'list' ou 'form' — ce ne sont PAS des noms de templates valides.

Pour l'utilisation détaillée, veuillez vous référer au script `./view_a2ui_examples.py` (situé dans le même dossier que ce fichier SKILL.md).

### Étape 3 : Générer la réponse A2UI

Après avoir chargé le schéma et les exemples, produisez votre réponse A2UI directement en texte. La sortie texte doit contenir deux parties séparées par le délimiteur `---a2ui_JSON---` :

Première partie : **Réponse textuelle conversationnelle** : Votre réponse en langage naturel à l'utilisateur
Deuxième partie : **Messages JSON A2UI** : Un tableau JSON brut d'objets de message A2UI qui DOIT être validé contre le schéma A2UI

**Format :**
```
[Your conversational response here]

---a2ui_JSON---
[
  { "beginRendering": { ... } },
  { "surfaceUpdate": { ... } },
  { "dataModelUpdate": { ... } }
]
```

**Important** : La partie JSON doit être du JSON valide et conforme au schéma A2UI chargé à l'étape 1.



## Extensions spécifiques au domaine

Pour ajouter le support d'un nouveau domaine (par ex., réservation de vols, e-commerce), ajoutez de nouveaux templates dans `view_a2ui_examples.py` :

1. Définissez une nouvelle constante de template dans `view_a2ui_examples.py` (par ex., `FLIGHT_BOOKING_FORM_EXAMPLE`)
2. Ajoutez le template au dictionnaire `TEMPLATE_MAP` dans `view_a2ui_examples.py`
3. Mettez à jour ce SKILL.md pour inclure les nouveaux templates dans la liste des templates disponibles


## Dépannage

Si vous rencontrez des problèmes lors de l'exécution des scripts, assurez-vous que :
vous utilisez l'outil `execute_shell_command` pour exécuter la commande Python.

1. Vous êtes dans le bon répertoire du skill (vérifiez la description du skill pour le chemin réel)
2. Les fichiers de script (`view_a2ui_schema.py` et `view_a2ui_examples.py`) existent dans le répertoire du skill
3. Vous avez les dépendances Python requises installées

Pour l'utilisation détaillée de chaque script, veuillez vous référer à :
- `./view_a2ui_schema.py` - Voir le schéma A2UI
- `./view_a2ui_examples.py` - Voir les exemples de templates A2UI
