# Glosos Voice Agent

Minimal LiveKit-based voice assistant built with `livekit-agents`.

## What This Project Does

This project runs a realtime voice agent that:
- joins a LiveKit room,
- listens to user speech,
- generates responses with an LLM,
- speaks replies back with TTS.

The agent is defined in [`agent.py`](agent.py) and registered as `my-agent`.

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

- [`agent.py`](agent.py): agent implementation and runtime entrypoint
- [`config/defaults.toml`](config/defaults.toml): committed non-secret runtime defaults
- [`pyproject.toml`](pyproject.toml): project metadata and dependencies
- [`uv.lock`](uv.lock): locked dependency graph

## Configuration

This project uses a split configuration model:
- committed non-secret defaults in `config/defaults.toml`,
- local secrets in `.env` (gitignored).

Runtime settings such as `STT_MODEL`, `LLM_MODEL`, `TTS_MODEL`, `TTS_VOICE_NAME`,
`STT_LANGUAGE`, `STT_USE_STREAMING`, endpointing delays, and `GOOGLE_LLM_LOCATION` are read from
`config/defaults.toml` and can be overridden via environment variables.
For Gemini 3 Flash on Vertex AI, use `GOOGLE_LLM_LOCATION=global`.

Create a local `.env` file with secrets:
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- optional fallback: `LIVEKIT_TOKEN` (used only when `LIVEKIT_API_SECRET` is unset; must be the project secret, not a JWT)
- `LIVEKIT_URL`
- `GOOGLE_CREDENTIALS_FILE` (or legacy alias `GOOGLE_STT_CREDENTIALS_FILE`)

For Vertex AI LLM, optional:
- `GOOGLE_CLOUD_PROJECT` (if omitted, inferred from the service account)

Startup fails fast if required secrets/settings are missing or invalid.

Keep `.env` private and never commit real secrets.
You can bootstrap from the template:
```bash
cp .env.example .env
```

## Run Locally

1. Install dependencies:
```bash
uv sync
```

2. Set environment variables in `.env` (see `.env.example`), then start the agent:
```bash
uv run python agent.py
```

Token-only participant mode (no worker dispatch):
```bash
uv run python token_agent.py
```
Requires `LIVEKIT_URL` and a valid room `LIVEKIT_TOKEN`.

Token-only participant mode with auto-generated JWT from API key/secret:
```bash
uv run python run_token_agent.py
```
Requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
Optional token-generation env vars:
- `LIVEKIT_ROOM` (default: `default-room`)
- `LIVEKIT_IDENTITY` (default: `token-agent`)
- `LIVEKIT_CLIENT_IDENTITY` (default: `human-test`)
- `LIVEKIT_TOKEN_TTL_SECONDS` (default: `3600`)

If you need available CLI options from LiveKit Agents:
```bash
uv run python agent.py --help
```

## Run In Container (Project Read-Only, `user/` Writable)

This setup runs the full agent inside Docker while keeping container root filesystem read-only.
Only `./user` from the host is mounted as writable at `/app/user`.

1. Prepare environment:
```bash
cp .env.example .env
```

2. Ensure user directory exists:
```bash
mkdir -p user
```

3. Build and run:
```bash
docker compose up --build
```

4. Stop:
```bash
docker compose down
```

Notes:
- Source code edits from inside the agent cannot persist on host because project files are not mounted writable.
- User data persists in host `user/`.
- Container defaults to `python agent.py start` (not `console`).
- `console` mode requires PortAudio and host audio device access, which is typically not available in Docker Desktop.

## Notes

- The project is intentionally small and currently has no tests.
- Use this repository as a base for extending tools, prompts, and workflow logic in the agent.
