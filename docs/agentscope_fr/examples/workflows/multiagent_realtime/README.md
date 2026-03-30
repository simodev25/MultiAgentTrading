# Exemple d'interaction vocale multi-agents en temps réel

Cet exemple montre comment utiliser la classe `ChatRoom` d'AgentScope pour créer un système d'interaction vocale multi-agents en temps réel où deux agents IA peuvent avoir des conversations autonomes sans intervention de l'utilisateur.

## Fonctionnalités

- 🗣️ **Interaction vocale en temps réel** : Deux agents communiquent par la voix en temps réel
- 🤖 **Conversation autonome** : Les agents conversent entre eux sans intervention de l'utilisateur
- ⚙️ **Configuration personnalisable** : Configurez les noms et les instructions des agents via l'interface web
- 🎨 **Interface moderne** : Interface épurée, inspirée de shadcn, pour une interaction facile
- 📊 **Transcription en direct** : Visualisez les transcriptions de la conversation en temps réel

## Architecture

L'exemple utilise :
- **Backend** : Serveur FastAPI avec support WebSocket
- **Frontend** : HTML5 avec Web Audio API pour la lecture audio
- **Composants AgentScope** :
  - `ChatRoom` : Gère plusieurs instances de `RealtimeAgent`
  - `RealtimeAgent` : Gère l'interaction vocale en temps réel avec les modèles IA
  - `DashScopeRealtimeModel` : Modèle temps réel Qwen3-Omni de DashScope

## Prérequis

1. **Dépendances Python** :
   ```bash
   pip install agentscope[dashscope]
   pip install fastapi uvicorn
   ```

2. **Clé API DashScope** :
   - Définissez votre clé API DashScope comme variable d'environnement :
     ```bash
     export DASHSCOPE_API_KEY="your-api-key-here"
     ```

## Utilisation

1. **Démarrer le serveur** :
   ```bash
   python run_server.py
   ```

2. **Ouvrir l'interface web** :
   - Accédez à `http://localhost:8000` dans votre navigateur web

3. **Configurer les agents** :
   - Définissez les noms et les instructions pour Agent 1 et Agent 2
   - Exemples de configurations :
     - **Agent 1 (Alice)** : "You are Alice, a cheerful and optimistic person who loves to share stories and ask questions. Keep your responses brief and conversational."
     - **Agent 2 (Bob)** : "You are Bob, a thoughtful and analytical person who enjoys deep conversations. Keep your responses brief and conversational."

4. **Démarrer la conversation** :
   - Cliquez sur le bouton "▶️ Start Conversation"
   - Les agents commenceront à converser de manière autonome
   - Vous verrez les transcriptions et les messages système dans le panneau de messages
   - La lecture audio sera diffusée en temps réel

5. **Arrêter la conversation** :
   - Cliquez sur le bouton "⏹️ Stop Conversation" lorsque vous souhaitez mettre fin à la session

## Fonctionnement

### Flux backend

1. **Connexion WebSocket** : Le client se connecte via WebSocket à `/ws/{user_id}/{session_id}`
2. **Création de session** :
   - Le client envoie un événement `client_session_create` avec les configurations des agents
   - Le serveur crée deux instances de `RealtimeAgent` avec les noms et instructions spécifiés
   - Le serveur crée un `ChatRoom` avec les deux agents
   - Le serveur démarre le salon de discussion et retourne un événement `session_created`
3. **Diffusion des messages** :
   - `ChatRoom` diffuse automatiquement les messages entre les agents
   - Tous les événements (audio, transcriptions, etc.) sont transmis au frontend
4. **Fin de session** : Le client envoie un événement `client_session_end` pour arrêter la conversation

### Flux frontend

1. **Configuration WebSocket** : Établit la connexion et attend les événements du serveur
2. **Gestion de session** : Envoie la configuration et gère l'état de la conversation
3. **Lecture audio** :
   - Reçoit des morceaux audio PCM16 encodés en base64
   - Décode et met en file d'attente les données audio
   - Utilise le `ScriptProcessorNode` de Web Audio API pour la lecture en streaming à 24 kHz
4. **Affichage des transcriptions** : Affiche les transcriptions en temps réel des deux agents

## Composants clés

### ChatRoom

La classe `ChatRoom` gère plusieurs instances de `RealtimeAgent` :
- Établit les connexions pour tous les agents
- Diffuse automatiquement les messages entre les agents
- Transmet les événements au frontend
- Gère le cycle de vie (démarrage/arrêt)

### RealtimeAgent

Chaque `RealtimeAgent` :
- Se connecte à l'API temps réel DashScope
- Traite l'entrée audio des autres agents
- Génère des réponses vocales
- Émet des événements pour les transcriptions, l'audio et les mises à jour de statut

## Personnalisation

### Changer de modèle

Pour utiliser un modèle différent, modifiez la configuration de `DashScopeRealtimeModel` dans `run_server.py` :

```python
model=DashScopeRealtimeModel(
    model_name="your-model-name",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
)
```

### Ajouter plus d'agents

Pour ajouter plus d'agents, modifiez la section de création des agents dans `run_server.py` :

```python
agent3 = RealtimeAgent(
    name=agent3_name,
    sys_prompt=agent3_instructions,
    model=DashScopeRealtimeModel(
        model_name="qwen3-omni-flash-realtime",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    ),
)

chat_room = ChatRoom(agents=[agent1, agent2, agent3])
```

Et mettez à jour le frontend pour inclure des champs de configuration pour les agents supplémentaires.

## Dépannage

### Pas de lecture audio
- Assurez-vous que votre navigateur prend en charge Web Audio API
- Vérifiez la console du navigateur pour les erreurs liées à l'audio
- Vérifiez que le format audio correspond au PCM16 attendu à 24 kHz

### Problèmes de connexion
- Vérifiez que votre clé API DashScope est correctement configurée
- Vérifiez que le port 8000 n'est pas bloqué par un pare-feu
- Consultez les journaux du serveur pour les messages d'erreur

### Les agents ne répondent pas
- Assurez-vous que les deux configurations d'agents ont des instructions valides
- Vérifiez que les instructions encouragent un comportement conversationnel
- Consultez les journaux de la console pour les erreurs API

## Références

- [AgentScope Documentation](https://modelscope.github.io/agentscope/)
- [DashScope API Documentation](https://help.aliyun.com/zh/model-studio/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)
