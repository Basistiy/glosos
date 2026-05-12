#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${1:-$PWD}"
IMAGE="${GLOSOS_IMAGE:-ghcr.io/basistiy/glosos:latest}"
CONFIG_DIR="$WORK_DIR/config"
USER_DIR="$WORK_DIR/user"
ENV_FILE="$CONFIG_DIR/.env"
DEFAULTS_FILE="$CONFIG_DIR/defaults.toml"
COMPOSE_FILE="$WORK_DIR/docker-compose.glosos.yml"

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

mkdir -p "$CONFIG_DIR" "$USER_DIR"

echo "Glosos standalone setup"
echo "Install directory: $WORK_DIR"
echo "Docker image: $IMAGE"
echo

read -r -p "LIVEKIT_URL [wss://glosos-uti53aki.livekit.cloud]: " livekit_url
livekit_url="${livekit_url:-wss://glosos-uti53aki.livekit.cloud}"
if [[ -z "${livekit_url:-}" ]]; then
  echo "LIVEKIT_URL cannot be empty."
  exit 1
fi

read -r -p "FIREBASE_WEB_API_KEY: " firebase_web_api_key
read -r -p "FIREBASE_AUTH_USERNAME (email): " firebase_auth_username
read -r -s -p "FIREBASE_AUTH_PASSWORD: " firebase_auth_password
echo
read -r -s -p "GOOGLE_API_KEY (Gemini API): " google_api_key
echo
read -r -s -p "CARTESIA_API_KEY: " cartesia_api_key
echo

if [[ -z "${firebase_web_api_key:-}" || -z "${firebase_auth_username:-}" || -z "${firebase_auth_password:-}" || -z "${google_api_key:-}" || -z "${cartesia_api_key:-}" ]]; then
  echo "FIREBASE_WEB_API_KEY, FIREBASE_AUTH_USERNAME, FIREBASE_AUTH_PASSWORD, GOOGLE_API_KEY, and CARTESIA_API_KEY are required."
  exit 1
fi

cat > "$DEFAULTS_FILE" <<EOF
[agent]
LIVEKIT_URL = "$livekit_url"
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
FIREBASE_AUTH_USERNAME=$firebase_auth_username
FIREBASE_AUTH_PASSWORD=$firebase_auth_password
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

echo
echo "Pulling image: $IMAGE"
docker pull "$IMAGE"

echo
echo "Setup complete."
echo "Created: $DEFAULTS_FILE"
echo "Created: $ENV_FILE"
echo "Created: $COMPOSE_FILE"
echo "Prepared: $USER_DIR"
echo
read -r -p "Start now with docker compose -f docker-compose.glosos.yml up -d? [Y/n]: " start_now
start_now="${start_now:-Y}"
if [[ "$start_now" =~ ^[Yy]$ ]]; then
  (cd "$WORK_DIR" && docker compose -f docker-compose.glosos.yml up -d)
  echo "Started. Check logs with: docker compose -f docker-compose.glosos.yml logs -f"
else
  echo "Start with: cd \"$WORK_DIR\" && docker compose -f docker-compose.glosos.yml up -d"
fi
