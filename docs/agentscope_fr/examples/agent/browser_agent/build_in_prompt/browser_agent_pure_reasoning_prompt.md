Sous-tâche actuelle à compléter : {current_subtask}

Veuillez évaluer soigneusement si vous devez utiliser un outil pour atteindre votre objectif actuel, ou si vous pouvez l'accomplir par le raisonnement seul.

**Si vous avez seulement besoin de raisonnement :**
- Analysez les informations actuellement disponibles
- Fournissez votre réponse de raisonnement basée sur l'analyse
- Portez une attention particulière à savoir si cette sous-tâche est complétée après votre réponse
- Si vous pensez que la sous-tâche est complète, résumez les résultats et appelez `browser_subtask_manager` pour passer à la sous-tâche suivante

**Si vous devez utiliser un outil :**
- Analysez l'historique de conversation précédent - si les appels d'outils précédents ont échoué, essayez un outil ou une approche différente
- Retournez l'appel d'outil approprié avec votre réponse de raisonnement
- Par exemple, utilisez des outils pour naviguer, cliquer, sélectionner ou taper du contenu sur la page web

N'oubliez pas d'être stratégique dans votre approche et d'apprendre de toute tentative échouée précédente.

Si vous pensez que la sous-tâche actuelle est complète, fournissez les résultats et appelez `browser_subtask_manager` pour passer à la sous-tâche suivante.

Si la réponse finale à la requête de l'utilisateur, c'est-à-dire {init_query}, a été trouvée, appelez directement `browser_generate_final_response` pour terminer le processus. N'appelez PAS `browser_subtask_manager` dans ce cas.
