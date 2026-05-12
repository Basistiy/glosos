#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${1:-$PWD}"
CONFIG_DIR="$WORK_DIR/config"
USER_DIR="$WORK_DIR/user"
ENV_FILE="$CONFIG_DIR/.env"
DEFAULTS_FILE="$CONFIG_DIR/defaults.toml"
COMPOSE_FILE="$WORK_DIR/docker-compose.glosos.yml"
IMAGE="${GLOSOS_IMAGE:-ghcr.io/basistiy/glosos:latest}"
DEFAULT_LIVEKIT_URL="wss://glosos-uti53aki.livekit.cloud"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
}

require_cmd docker

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required (docker compose)."
  exit 1
fi

mkdir -p "$CONFIG_DIR" "$USER_DIR" "$USER_DIR/system/scripts"

echo "Glosos minimal install"
echo "Target directory: $WORK_DIR"
echo

read -r -p "Firebase user_id (email): " firebase_user_id
read -r -s -p "Firebase password: " firebase_password
echo
read -r -p "Firebase web API key: " firebase_web_api_key
echo
read -r -s -p "Google API key (Gemini): " google_api_key
echo
read -r -s -p "Cartesia API key: " cartesia_api_key
echo

if [[ -z "${firebase_user_id:-}" || -z "${firebase_password:-}" || -z "${firebase_web_api_key:-}" || -z "${google_api_key:-}" || -z "${cartesia_api_key:-}" ]]; then
  echo "user_id, password, firebase key, Google API key, and Cartesia API key are required."
  exit 1
fi

cat > "$DEFAULTS_FILE" <<EOF
[agent]
LIVEKIT_URL = "$DEFAULT_LIVEKIT_URL"
GOOGLE_CREDENTIALS_FILE = ""
STT_PROVIDER = "cartesia"
STT_MODEL = "ink-whisper"
LLM_PROVIDER = "google_api"
LLM_MODEL = "gemini-3-flash"
TTS_PROVIDER = "cartesia"
TTS_MODEL = "sonic-3"
TTS_VOICE_NAME = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
GOOGLE_STT_LOCATION = "eu"
GOOGLE_LLM_LOCATION = "global"
STT_LANGUAGE = "en"
STT_USE_STREAMING = true
MIN_ENDPOINTING_DELAY = 0.1
MAX_ENDPOINTING_DELAY = 0.6
EOF

cat > "$ENV_FILE" <<EOF
FIREBASE_WEB_API_KEY=$firebase_web_api_key
FIREBASE_AUTH_USERNAME=$firebase_user_id
FIREBASE_AUTH_PASSWORD=$firebase_password
GOOGLE_API_KEY=$google_api_key
CARTESIA_API_KEY=$cartesia_api_key
EOF

chmod 600 "$ENV_FILE"

cat > "$COMPOSE_FILE" <<EOF
services:
  agent:
    image: $IMAGE
    pull_policy: always
    read_only: true
    tmpfs:
      - /tmp
    working_dir: /app
    user: "10001:10001"
    volumes:
      - ./config:/app/config:ro
      - ./user:/app/user:rw
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    restart: unless-stopped
    command: ["node", "run_token_agent.js"]
EOF

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo
  echo "Image not found locally. Pulling latest: $IMAGE"
  docker pull "$IMAGE"
else
  echo
  echo "Image already exists locally: $IMAGE"
fi

echo
echo "Install complete."
echo "Created: $DEFAULTS_FILE"
echo "Created: $ENV_FILE"
echo "Created: $COMPOSE_FILE"
echo "Prepared: $USER_DIR/system/scripts"
echo
echo "Next:"
echo "  cd \"$WORK_DIR\""
echo "  docker compose -f docker-compose.glosos.yml up -d"
