import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc

from agent import (
    Assistant,
    LIVEKIT_URL,
    _build_project_context,
    _print_project_inspection,
    build_agent_session,
    register_incoming_file_handler,
)
from sounds import emit_ready_sound

load_dotenv("config/.env")


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def run_token_agent() -> None:
    livekit_token = _required_env("LIVEKIT_TOKEN")

    project_context = _build_project_context()

    room = rtc.Room()
    disconnected = asyncio.Event()
    register_incoming_file_handler(room)

    @room.on("disconnected")
    def _on_disconnected(reason: object) -> None:
        print(f"[token-agent] disconnected: {reason}")
        disconnected.set()

    session = build_agent_session()
    
    async def _send_file(path: Path, topic: str, destination_identities: list[str]) -> str:
        info = await room.local_participant.send_file(
            str(path),
            topic=topic,
            destination_identities=destination_identities,
        )
        return info.stream_id

    await room.connect(LIVEKIT_URL, livekit_token)
    print(f"[token-agent] connected to room: {room.name}")

    await session.start(
        room=room,
        agent=Assistant(project_context=project_context, send_file_fn=_send_file, room=room),
    )
    await emit_ready_sound(room)

    await disconnected.wait()


def main() -> None:
    _print_project_inspection()
    try:
        asyncio.run(run_token_agent())
    except KeyboardInterrupt:
        print("[token-agent] stopped by user")


if __name__ == "__main__":
    main()
