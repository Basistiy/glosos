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
- LLM: `gemini-3.1-pro-preview`
- TTS: `inworld/inworld-tts-1.5-max`
- VAD: Silero
- Turn detection: multilingual model

## Repository Layout

- [`agent.py`](agent.py): agent implementation and runtime entrypoint
- [`pyproject.toml`](pyproject.toml): project metadata and dependencies
- [`uv.lock`](uv.lock): locked dependency graph

## Environment Variables

Create a local `.env` file with required credentials:
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_URL`
- `GOOGLE_API_KEY`

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

If you need available CLI options from LiveKit Agents:
```bash
uv run python agent.py --help
```

## Run In Container (Project Read-Only, `users/` Writable)

This setup runs the full agent inside Docker while keeping container root filesystem read-only.
Only `./users` from the host is mounted as writable at `/app/users`.

1. Prepare environment:
```bash
cp .env.example .env
```

2. Ensure users directory exists:
```bash
mkdir -p users
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
- User data persists in host `users/`.
- Container defaults to `python agent.py start` (not `console`).
- `console` mode requires PortAudio and host audio device access, which is typically not available in Docker Desktop.

## Notes

- The project is intentionally small and currently has no tests.
- Use this repository as a base for extending tools, prompts, and workflow logic in the agent.

## Recovery Automation

If the agent modifies source files and leaves backup copies, you can automate restore:

1. Preview restore actions (safe dry-run):
```bash
python scripts/recover_from_backups.py
```

2. Apply restore:
```bash
python scripts/recover_from_backups.py --apply
```

3. Apply restore and remove backup files:
```bash
python scripts/recover_from_backups.py --apply --cleanup-backups
```
