#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${1:-$PWD}"
CONFIG_DIR="$WORK_DIR/config"
USER_DIR="$WORK_DIR/user"
ENV_FILE="$CONFIG_DIR/.env"
DEFAULTS_FILE="$CONFIG_DIR/defaults.toml"
GOOGLE_CREDS_FILE="$CONFIG_DIR/google-service-account.json"
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
echo "Provide path to Google service account JSON file."
read -r -p "Path: " source_google_creds_file

if [[ -z "${firebase_user_id:-}" || -z "${firebase_password:-}" || -z "${firebase_web_api_key:-}" ]]; then
  echo "user_id, password, and firebase key are required."
  exit 1
fi

if [[ -z "${source_google_creds_file:-}" ]]; then
  echo "Google JSON path is required."
  exit 1
fi

if [[ ! -f "$source_google_creds_file" ]]; then
  echo "File not found: $source_google_creds_file"
  exit 1
fi

cat > "$DEFAULTS_FILE" <<EOF
[agent]
LIVEKIT_URL = "$DEFAULT_LIVEKIT_URL"
GOOGLE_CREDENTIALS_FILE = "config/google-service-account.json"
STT_MODEL = "latest_long"
LLM_MODEL = "gemini-3-flash"
TTS_MODEL = "chirp_3"
TTS_VOICE_NAME = "en-US-Chirp3-HD-Charon"
GOOGLE_STT_LOCATION = "eu"
GOOGLE_LLM_LOCATION = "global"
STT_LANGUAGE = "en-US"
STT_USE_STREAMING = true
MIN_ENDPOINTING_DELAY = 0.1
MAX_ENDPOINTING_DELAY = 0.6
EOF

cat > "$ENV_FILE" <<EOF
FIREBASE_WEB_API_KEY=$firebase_web_api_key
FIREBASE_AUTH_USERNAME=$firebase_user_id
FIREBASE_AUTH_PASSWORD=$firebase_password
EOF

cp "$source_google_creds_file" "$GOOGLE_CREDS_FILE"

if command -v python3 >/dev/null 2>&1; then
  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1], "r", encoding="utf-8"))' "$GOOGLE_CREDS_FILE"; then
    echo "Invalid JSON file: $GOOGLE_CREDS_FILE"
    exit 1
  fi
fi

chmod 600 "$ENV_FILE" "$GOOGLE_CREDS_FILE"

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
echo "Created: $GOOGLE_CREDS_FILE"
echo "Created: $COMPOSE_FILE"
echo "Prepared: $USER_DIR/system/scripts"
echo
echo "Next:"
echo "  cd \"$WORK_DIR\""
echo "  docker compose -f docker-compose.glosos.yml up -d"
