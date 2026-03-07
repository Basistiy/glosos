import asyncio
import os

from dotenv import load_dotenv
from livekit import rtc

from agent import (
    Assistant,
    _build_project_context,
    _print_project_inspection,
    build_agent_session,
)

load_dotenv(".env")


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def run_token_agent() -> None:
    livekit_url = _required_env("LIVEKIT_URL")
    livekit_token = _required_env("LIVEKIT_TOKEN")

    project_context = _build_project_context()

    room = rtc.Room()
    disconnected = asyncio.Event()

    @room.on("disconnected")
    def _on_disconnected(reason: object) -> None:
        print(f"[token-agent] disconnected: {reason}")
        disconnected.set()

    session = build_agent_session()

    await room.connect(livekit_url, livekit_token)
    print(f"[token-agent] connected to room: {room.name}")

    await session.start(
        room=room,
        agent=Assistant(project_context=project_context),
    )

    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )

    await disconnected.wait()


def main() -> None:
    _print_project_inspection()
    try:
        asyncio.run(run_token_agent())
    except KeyboardInterrupt:
        print("[token-agent] stopped by user")


if __name__ == "__main__":
    main()
