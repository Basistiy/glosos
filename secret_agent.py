import os

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentServer

from agent import (
    Assistant,
    LIVEKIT_URL,
    _build_project_context,
    _print_project_inspection,
    build_agent_session,
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

    await session.start(
        room=ctx.room,
        agent=Assistant(project_context=project_context),
    )

    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )


def main() -> None:
    _configure_livekit_auth()
    _print_project_inspection()
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
