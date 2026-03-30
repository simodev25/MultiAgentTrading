# Exemple de sortie structurée

## Ce que cet exemple démontre

Cet exemple présente la **génération de sorties structurées** en utilisant AgentScope avec des modèles Pydantic. Il montre comment contraindre les sorties de modèles IA à suivre des structures et formats de données spécifiques, garantissant des réponses cohérentes et analysables.

### Fonctionnalités clés :
- **Génération de données structurées** : Force les réponses de l'agent à se conformer à
  des schémas prédéfinis
- **Intégration Pydantic** : Utilise des modèles Pydantic pour définir la structure de sortie avec validation
- **Sûreté de type** : Garantit que les types de données en sortie correspondent aux formats attendus
- **Validation des champs** : Inclut des contraintes comme les limites d'âge (0-120) et les choix d'énumérations
- **Sortie JSON** : Génère des réponses JSON propres et structurées

### Modèles d'exemple :

1. **TableModel** : Informations structurées sur une personne
   - `name` : Nom de la personne (string)
   - `age` : Âge de la personne (integer, 0-120)
   - `intro` : Introduction en une phrase (string)
   - `honors` : Liste de distinctions/réalisations (tableau de strings)

2. **ChoiceModel** : Sélection de choix contrainte
   - `choice` : Doit être l'un parmi "apple", "banana" ou "orange"

### Cas d'utilisation :
- **Extraction de données** : Extraire des informations structurées à partir de texte non structuré
- **Génération de formulaires** : Générer des données cohérentes pour des bases de données ou des API
- **Réponses à des enquêtes** : Garantir que les réponses correspondent à des catégories prédéfinies
- **Classification de contenu** : Catégoriser le contenu en types spécifiques

## Comment exécuter cet exemple
1. **Définir la variable d'environnement :**
   ```bash
   export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   ```
2. **Exécuter le script :**
    ```bash
   python main.py
   ```
3. **Sortie attendue :**
Le programme générera deux réponses structurées comme ci-dessous :
```
Structured Output 1:
{
    "name": "Albert Einstein",
    "age": 76,
    "intro": 1,
    "honors": [
        "Nobel Prize in Physics (1921)",
        "Copley Medal (1925)"
    ]
}
Structured Output 2:
{
    "choice": "apple"
}
```

>**Note :** Le contenu spécifique variera à chaque exécution car l'agent génère des réponses différentes, mais la structure JSON sera toujours conforme aux modèles Pydantic prédéfinis (`TableModel` et `ChoiceModel`).

## Fonctionnement :
1. L'agent reçoit une requête accompagnée d'un paramètre structured_model
2. L'agent génère une réponse conforme au schéma du modèle Pydantic
3. Les données structurées sont renvoyées dans res.metadata sous forme d'objet JSON validé
4. Pydantic garantit que tous les types de champs et contraintes sont satisfaits

## Modèles Pydantic personnalisés
Créez vos propres modèles de sortie structurée pour des cas d'utilisation spécifiques, par exemple :

```
from typing import Optional
from pydantic import BaseModel, Field, EmailStr

class BusinessModel(BaseModel):
    """Business information extraction model."""

    company_name: str = Field(description="Name of the company")
    industry: str = Field(description="Industry sector")
    founded_year: int = Field(description="Year founded", ge=1800, le=2024)
    headquarters: str = Field(description="Location of headquarters")
    employee_count: Optional[int] = Field(description="Number of employees", ge=1)
    email: Optional[EmailStr] = Field(description="Contact email address")
    website: Optional[str] = Field(description="Company website URL")

# Usage
query = Msg("user", "Tell me about Tesla Inc.", "user")
res = await agent(query, structured_model=BusinessModel)
```

## Bonnes pratiques pour la sortie structurée

1. **Utilisez des noms de champs descriptifs :** Rendez l'objectif des champs clair
2. **Ajoutez des descriptions de champs :** Aidez l'agent à comprendre quelles données générer
3. **Définissez des contraintes de validation :** Utilisez les validateurs Pydantic pour l'intégrité des données
4. **Choisissez des types appropriés :** Utilisez des types spécifiques comme EmailStr, datetime, etc.
