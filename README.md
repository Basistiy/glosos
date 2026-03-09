# Glosos Voice Agent

Minimal LiveKit-based voice assistant built with `livekit-agents`.

## What This Project Does

This project runs a realtime voice agent that:
- joins a LiveKit room,
- listens to user speech,
- generates responses with an LLM,
- speaks replies back with TTS.

Shared agent logic is in [`agent.py`](agent.py), and worker startup/registration is in [`secret_agent.py`](secret_agent.py) as `my-agent`.

## Tech Stack

- Python
- `livekit-agents`
- `livekit-plugins-noise-cancellation`
- `python-dotenv`

Configured pipeline in `agent.py`:
- STT: `assemblyai/universal-streaming`
- LLM: `gemini-3-flash` (resolved to Vertex model id `gemini-3-flash-preview`)
- TTS: `inworld/inworld-tts-1.5-max`
- VAD: Silero
- Turn detection: multilingual model

## Repository Layout

- [`agent.py`](agent.py): shared agent implementation and runtime helpers
- [`secret_agent.py`](secret_agent.py): LiveKit worker startup (API key/secret mode)
- [`config/defaults.toml`](config/defaults.toml): committed non-secret runtime defaults
- [`docker-compose.yml`](docker-compose.yml): runtime setup for the published container image
- [`pyproject.toml`](pyproject.toml): project metadata and dependencies
- [`uv.lock`](uv.lock): locked dependency graph

## Configuration

This project uses a split configuration model:
- committed non-secret defaults in `config/defaults.toml`,
- local secrets in `config/.env` and `config/google-service-account.json` (gitignored).

Runtime settings such as `STT_MODEL`, `LLM_MODEL`, `TTS_MODEL`, `TTS_VOICE_NAME`,
`STT_LANGUAGE`, `STT_USE_STREAMING`, endpointing delays, and `GOOGLE_LLM_LOCATION` are read from
`config/defaults.toml` (no env overrides for settings).
`LIVEKIT_URL` and `GOOGLE_CREDENTIALS_FILE` are also read only from `config/defaults.toml`.
`GOOGLE_LLM_LOCATION` is required.
For Gemini 3 Flash on Vertex AI, set `GOOGLE_LLM_LOCATION=global`.

Create a local `config/.env` file with secrets:
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`

Place your Google service account JSON at `config/google-service-account.json`.

Startup fails fast if required secrets/settings are missing or invalid.

Keep `config/.env` and `config/google-service-account.json` private and never commit real secrets.
The app reads secrets only from `config/`.
You can bootstrap from the template:
```bash
cp .env.example config/.env
```

## Run Locally

1. Install dependencies:
```bash
uv sync
```

2. Set secret environment variables in `config/.env` (see `.env.example`), then start the agent:
```bash
uv run python secret_agent.py
```

Token-only participant mode (no worker dispatch):
```bash
uv run python token_agent.py
```
Requires a valid `LIVEKIT_URL` in `config/defaults.toml` and `LIVEKIT_TOKEN`.

Token-only participant mode with auto-generated JWT from API key/secret:
```bash
uv run python run_token_agent.py
```
Requires `LIVEKIT_URL` in `config/defaults.toml`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
Optional token-generation env vars:
- `LIVEKIT_ROOM` (default: `default-room`)
- `LIVEKIT_IDENTITY` (default: `token-agent`)
- `LIVEKIT_CLIENT_IDENTITY` (default: `human-test`)
- `LIVEKIT_TOKEN_TTL_SECONDS` (default: `3600`)

If you need available CLI options from LiveKit Agents:
```bash
uv run python secret_agent.py --help
```

## Run In Container (Published Image, `user/` Writable, `config/` Mounted Read-Only)

This setup runs the published Docker image while keeping the container root filesystem read-only.
The host `./config` directory is mounted read-only at `/app/config`, so changes to
`config/defaults.toml` are picked up on container restart without rebuilding the image.
Only `./user` from the host is mounted as writable at `/app/user`.

1. Prepare environment:
```bash
cp .env.example config/.env
```

2. Place your Google service account file at `config/google-service-account.json`.

3. Ensure user directory exists:
```bash
mkdir -p user
```

4. Start the published image:
```bash
docker compose up
```

5. After changing files under `config/`, restart the container without rebuilding:
```bash
docker compose restart
```

6. Stop:
```bash
docker compose down
```

Notes:
- The default image is `ghcr.io/basistiy/glosos:latest`. Override it with `GLOSOS_IMAGE=...` if needed.
- Source code edits from inside the container cannot persist on host because project files are not mounted writable.
- Runtime config edits in `config/` persist on host and are loaded on the next container start.
- User data persists in host `user/`.
- Python scripts placed in `user/system/scripts/` are discovered every 60 seconds and executed one by one in filename order.
- Files starting with `.` or `_` are ignored.
- Each script is run with the app's Python interpreter and has a 300 second timeout.
- Container defaults to `python secret_agent.py start` (not `console`).
- `console` mode requires PortAudio and host audio device access, which is typically not available in Docker Desktop.

## Scheduled User Scripts

The worker includes a minute-based runner for user scripts stored in `user/system/scripts/`.
This is intended for lightweight project-local automation that persists through the writable `user/` mount.

Behavior:
- Scans `user/system/scripts/` every 60 seconds.
- Runs top-level `*.py` files only.
- Executes files one by one in alphabetical order.
- Ignores files starting with `.` or `_`.
- Uses a 300 second timeout for each script.
- Logs stdout and stderr to the container logs.

Example:
```bash
mkdir -p user/system/scripts
cat > user/system/scripts/01_hello.py <<'PY'
print("hello from scheduled script")
PY
```

## Publish Image

Publish `ghcr.io/basistiy/glosos` manually with standard Docker commands, for example:

```bash
docker build -t ghcr.io/basistiy/glosos:latest .
docker push ghcr.io/basistiy/glosos:latest
```

## Notes

- The project is intentionally small and currently has no tests.
- Use this repository as a base for extending tools, prompts, and workflow logic in the agent.
