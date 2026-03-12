import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentServer
from script_scheduler import start_script_scheduler

from agent import (
    Assistant,
    LIVEKIT_URL,
    _build_project_context,
    _print_project_inspection,
    build_agent_session,
    register_incoming_file_handler,
)

load_dotenv("config/.env")


def _configure_livekit_auth() -> None:
    livekit_secret = (os.getenv("LIVEKIT_API_SECRET") or "").strip()
    if not livekit_secret:
        raise RuntimeError(
            "Missing required secret: LIVEKIT_API_SECRET. "
            "Worker mode requires project API credentials."
        )


server = AgentServer(ws_url=LIVEKIT_URL)


@server.rtc_session()
async def my_agent(ctx: agents.JobContext) -> None:
    project_context = _build_project_context()
    session = build_agent_session()
    register_incoming_file_handler(ctx.room)
    
    async def _send_file(path: Path, topic: str, destination_identities: list[str]) -> str:
        info = await ctx.room.local_participant.send_file(
            str(path),
            topic=topic,
            destination_identities=destination_identities,
        )
        return info.stream_id

    await session.start(
        room=ctx.room,
        agent=Assistant(project_context=project_context, send_file_fn=_send_file),
    )

    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )


def main() -> None:
    _configure_livekit_auth()
    _print_project_inspection()
    start_script_scheduler()
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
