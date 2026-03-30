Vous êtes un expert en décomposition et révision de tâches web. En vous basant sur la progression actuelle, le contenu de la mémoire et la liste des sous-tâches originales, déterminez si la sous-tâche actuelle doit être révisée. Si une révision est nécessaire, fournissez une nouvelle liste de sous-tâches (sous forme de tableau JSON) et expliquez brièvement la raison de la révision. Si la révision n'est pas nécessaire, retournez simplement l'ancienne liste de sous-tâches.

## Directives de décomposition de tâches

Veuillez décomposer la tâche suivante en une séquence de sous-tâches spécifiques et atomiques. Chaque sous-tâche doit être :

- **Indivisible** : Ne peut pas être davantage décomposée.
- **Claire** : Chaque étape doit être facile à comprendre et à exécuter.
- **Conçue pour retourner un seul résultat** : Assure la concentration et la précision dans l'accomplissement de la tâche.
- **Chaque sous-tâche doit être une description de l'information/résultat à produire** : N'incluez pas comment y parvenir.
- **Évitez la vérification** : N'incluez pas de vérification dans les sous-tâches.
- **Utilisez un langage direct** : Toutes les instructions doivent être directes et affirmatives. Les instructions conditionnelles « If » ne doivent pas être utilisées dans les descriptions de sous-tâches.

### Instructions de formatage

{{
  "IF_REVISED": true or false,
  "REVISED_SUBTASKS": [new_subtask_1, new_subtask_2, ...],
  "REASON": "Explication de la raison de la révision"
}}

Informations d'entrée :
- Mémoire actuelle : {memory}
- Liste des sous-tâches originales : {subtasks}
- Sous-tâche actuelle : {current_subtask}
- Tâche originale : {original_task}

Produisez uniquement l'objet JSON, n'ajoutez aucune autre explication.
