# Exemple de Realtime Voice Agent

Cet exemple montre comment construire un **agent de conversation vocale en temps réel** en utilisant le RealtimeAgent d'AgentScope. L'agent prend en charge le streaming vocal bidirectionnel, permettant des conversations vocales naturelles avec une faible latence et une transcription audio en temps réel.

## Prérequis

- Python 3.10 ou supérieur
- Votre clé API DashScope dans une variable d'environnement `DASHSCOPE_API_KEY`

Installez les packages requis :

```bash
uv pip install agentscope fastapi uvicorn websockets
# or
# pip install agentscope
```

## Utilisation

### 1. Démarrer le serveur

Lancez le serveur FastAPI :

```bash
cd examples/agent/realtime_voice_agent
python run_server.py
```

Le serveur démarrera sur `http://localhost:8000` par défaut.

### 2. Ouvrir l'interface web

Ouvrez votre navigateur web et accédez à :

```
http://localhost:8000
```

Vous verrez une interface web comprenant :
- Un panneau de configuration (instructions et nom d'utilisateur)
- Des boutons de contrôle vocal (Start Recording, Stop Recording, Disconnect)
- Un bouton d'enregistrement vidéo (Start Video Recording)
- Un champ de saisie de texte
- Une zone d'affichage des messages
- Une zone de prévisualisation vidéo (lorsque l'enregistrement vidéo est actif)

### 3. Démarrer la conversation

1. **Configurer l'agent** (optionnel) :
   - Modifiez les "Instructions" pour personnaliser le comportement de l'agent
   - Entrez votre nom dans le champ "User Name"

2. **Démarrer l'enregistrement vocal** :
   - Cliquez sur le bouton "🎤 Start Recording"
   - Autorisez l'accès au microphone lorsque votre navigateur vous le demande
   - Parlez naturellement à l'agent
   - L'agent répondra par la voix et par du texte

3. **Arrêter l'enregistrement** :
   - Cliquez sur "⏹️ Stop Recording" pour mettre en pause l'entrée vocale

4. **Enregistrement vidéo** (optionnel) :
   - Cliquez sur le bouton "📹 Start Video Recording" pour démarrer l'enregistrement vidéo
   - Autorisez l'accès à la caméra lorsque votre navigateur vous le demande
   - Le système capturera et enverra automatiquement les images vidéo au serveur à raison de 1 image par seconde (1 fps)
   - Un aperçu vidéo sera affiché pendant l'enregistrement
   - Cliquez sur "🔴 Stop Video Recording" pour arrêter l'enregistrement
   - **Note** : L'enregistrement vidéo nécessite une session de chat vocal active. Veuillez d'abord démarrer le chat vocal avant de lancer l'enregistrement vidéo.

## Changement de modèles

AgentScope prend en charge plusieurs modèles vocaux en temps réel. Par défaut, cet exemple utilise le modèle `qwen3-omni-flash-realtime` de DashScope, mais vous pouvez facilement passer à d'autres fournisseurs.

### Modèles pris en charge

- **GeminiRealtimeModel**
- **OpenAIRealtimeModel**

### Comment changer de modèle

Éditez `run_server.py` et remplacez le code d'initialisation du modèle :

**Pour OpenAI :**

```python
from agentscope.realtime import OpenAIRealtimeModel

agent = RealtimeAgent(
    name="Friday",
    sys_prompt=sys_prompt,
    model=OpenAIRealtimeModel(
        model_name="gpt-4o-realtime-preview",
        api_key=os.getenv("OPENAI_API_KEY"),
        voice="alloy",  # Options: "alloy", "echo", "marin", "cedar"
    ),
)
```

**Pour Gemini :**

```python
from agentscope.realtime import GeminiRealtimeModel

agent = RealtimeAgent(
    name="Friday",
    sys_prompt=sys_prompt,
    model=GeminiRealtimeModel(
        model_name="gemini-2.5-flash-native-audio-preview-09-2025",
        api_key=os.getenv("GEMINI_API_KEY"),
        voice="Puck",  # Options: "Puck", "Charon", "Kore", "Fenrir"
    ),
)
```

N'oubliez pas de définir la variable d'environnement de la clé API correspondante avant de démarrer le serveur !
