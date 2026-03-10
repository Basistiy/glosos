#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${1:-$PWD}"
IMAGE="${GLOSOS_IMAGE:-ghcr.io/basistiy/glosos:latest}"
CONFIG_DIR="$WORK_DIR/config"
USER_DIR="$WORK_DIR/user"
ENV_FILE="$CONFIG_DIR/.env"
DEFAULTS_FILE="$CONFIG_DIR/defaults.toml"
GOOGLE_CREDS_FILE="$CONFIG_DIR/google-service-account.json"
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

read -r -p "LIVEKIT_API_KEY: " livekit_api_key
read -r -s -p "LIVEKIT_API_SECRET: " livekit_api_secret
echo

if [[ -z "${livekit_api_key:-}" || -z "${livekit_api_secret:-}" ]]; then
  echo "LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required."
  exit 1
fi

cat > "$DEFAULTS_FILE" <<EOF
[agent]
LIVEKIT_URL = "$livekit_url"
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
LIVEKIT_API_KEY=$livekit_api_key
LIVEKIT_API_SECRET=$livekit_api_secret
LIVEKIT_ROOM=default-room
LIVEKIT_IDENTITY=token-agent
LIVEKIT_CLIENT_IDENTITY=human-test
LIVEKIT_TOKEN_TTL_SECONDS=3600
EOF

chmod 600 "$ENV_FILE"

echo
echo "Provide path to Google service account JSON file."
echo "Tip: drag and drop the JSON file into this terminal to auto-fill its path."
read -r -p "Path to Google service account JSON file: " source_google_creds_file
if [[ -z "${source_google_creds_file:-}" ]]; then
  echo "Credentials file path is required."
  exit 1
fi
if [[ ! -f "$source_google_creds_file" ]]; then
  echo "File not found: $source_google_creds_file"
  exit 1
fi
cp "$source_google_creds_file" "$GOOGLE_CREDS_FILE"

if command -v python3 >/dev/null 2>&1; then
  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1], "r", encoding="utf-8"))' "$GOOGLE_CREDS_FILE"; then
    echo "Invalid JSON. Please run setup again."
    exit 1
  fi
else
  echo "Warning: python3 not found, JSON validation skipped."
fi

chmod 600 "$GOOGLE_CREDS_FILE"

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
    command: ["python", "secret_agent.py", "start"]
EOF

echo
echo "Pulling image: $IMAGE"
docker pull "$IMAGE"

echo
echo "Setup complete."
echo "Created: $DEFAULTS_FILE"
echo "Created: $ENV_FILE"
echo "Created: $GOOGLE_CREDS_FILE"
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
