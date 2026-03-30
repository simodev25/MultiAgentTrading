Votre travail consiste à réfléchir sur votre échec en vous basant sur votre historique de travail et à générer la sous-tâche de suivi. Vous avez déjà constaté qu'une des sous-tâches du Working Plan ne peut pas être complétée avec succès selon votre historique de travail.

## Instructions
1. Examinez le Work History pour identifier précisément la sous-tâche échouée dans le Working Plan.
2. Examinez le Current Subtask et le Task Final Objective fournis dans le Work History, analysez attentivement si cette sous-tâche a été conçue incorrectement en raison d'une mauvaise compréhension de la tâche. Si c'est le cas,
    * définissez `need_rephrase` dans `rephrase_subtask` à true
    * Remplacez uniquement la sous-tâche inappropriée par une sous-tâche modifiée, tout en conservant le reste du Working Plan inchangé. Vous devez produire le Working Plan mis à jour dans `rephrased_plan`.
    * Si la sous-tâche n'a pas été mal conçue, passez à l'étape 3.
3. Récupérez attentivement l'objectif de la sous-tâche précédente dans le Work History pour vérifier tout signe de blocage dans des **schémas répétitifs** générant des sous-tâches similaires.
    * Si c'est le cas, évitez une décomposition inutile en définissant `need_decompose` dans `decompose_subtask` à false.
    * Sinon, définissez `need_decompose` à true et produisez uniquement la sous-tâche échouée sans raisonnement supplémentaire dans `failed_subtask`.

## Notes importantes
1. `need_decompose` et `need_rephrase` ne peuvent PAS être tous les deux à true en même temps.
2. Définissez `need_decompose` et `need_rephrase` à false simultanément lorsque vous constatez que vous êtes bloqué dans un schéma d'échec répétitif.

## Exemple
Work History:
1. Reflect the failure of this subtask and identify the failed subtask "Convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes by mapping tools or geo-mapping APIs"
2. Decompose subtask "Convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes by mapping tools or geo-mapping APIs" and generate a plan.
Working Plan:
1. Extract detailed geographic data  focusing on Fred Howard Park and associated HUC code.
2. Use mapping tools or geo-mapping APIs (e.g., 'maps_regeocode') to convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes.
3. Verify the accuracy of the generated zip codes by cross-referencing them with external databases or additional resources to ensure inclusion of all Clownfish occurrence locations.
4. Compile the verified zip codes into a formatted list as required by the user, ensuring clarity and adherence to specifications.
Failed Subtask: "Use mapping tools or geo-mapping APIs (e.g., 'maps_regeocode') to convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes."
Output:
```json
{
    "rephrase_subtask":{
        "need_rephrase": false,
        "rephrased_plan": ""
    },
    "decompose_subtask":{
        "need_decompose": false,
        "failed_subtask": ""
    }
}
```
Explication : La sous-tâche actuellement échouée "Use mapping tools or geo-mapping APIs (e.g., 'maps_regeocode') to convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes" est similaire à la sous-tâche précédemment échouée "Convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes by mapping tools or geo-mapping APIs", qui a déjà été identifiée et décomposée dans l'historique de travail. Par conséquent, nous n'avons pas besoin de procéder à une décomposition de manière répétitive.

### Exigences de format de sortie
* Assurez un formatage JSON correct avec les caractères spéciaux échappés si nécessaire.
* Les sauts de ligne dans les champs textuels doivent être représentés par `\n` dans la sortie JSON.
* Il n'y a pas de limite spécifique sur la longueur des champs, mais visez des descriptions concises.
* Toutes les valeurs de champs doivent être des chaînes de caractères.
* Pour chaque document JSON, incluez uniquement les champs suivants :
