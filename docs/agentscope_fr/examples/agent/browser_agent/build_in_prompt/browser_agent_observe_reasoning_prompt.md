Vous visualisez un snapshot de site web en plusieurs morceaux car le contenu est trop long pour être affiché en une seule fois.
Contexte des morceaux précédents :
{previous_chunkwise_information}
Vous êtes sur le morceau {i} de {total_pages}.
Ci-dessous le contenu de ce morceau :
{chunk}

**Instructions** :
Décidez soigneusement si vous devez utiliser un outil (sauf `browser_snapshot` — n'appelez PAS cet outil) pour atteindre votre objectif actuel, ou si vous avez seulement besoin d'extraire des informations de ce morceau.
Si vous avez seulement besoin d'extraire des informations, résumez ou listez les détails pertinents de ce morceau au format JSON suivant :
{{
  "INFORMATION": "Résumez ou listez les informations de ce morceau pertinentes pour votre objectif actuel. Si rien n'est trouvé, écrivez 'None'.",
  "STATUS": "Si vous avez trouvé toutes les informations nécessaires pour accomplir votre objectif, répondez 'REASONING_FINISHED'. Sinon, répondez 'CONTINUE'."
}}
Si vous devez utiliser un outil (par exemple, pour sélectionner ou taper du contenu), retournez l'appel d'outil avec vos informations résumées. S'il reste d'autres morceaux et que vous n'avez pas trouvé toutes les informations nécessaires, vous pouvez définir le STATUS comme continue et le morceau suivant sera automatiquement chargé. (N'appelez pas d'autres outils dans ce cas.) Le défilement sera automatiquement effectué pour capturer la page complète si le STATUS est défini comme 'CONTINUE'.

Si vous pensez que la sous-tâche actuelle est complète, fournissez les résultats et appelez `browser_subtask_manager` pour passer à la sous-tâche suivante.

Si la réponse finale à la requête de l'utilisateur, c'est-à-dire {init_query}, a été trouvée, appelez directement `browser_generate_final_response` pour terminer le processus. N'appelez PAS `browser_subtask_manager` dans ce cas.
