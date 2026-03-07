import os

from livekit import agents
from livekit.agents import AgentServer

from agent import Assistant, _build_project_context, _print_project_inspection, build_agent_session


def _configure_livekit_auth() -> None:
    livekit_secret = (os.getenv("LIVEKIT_API_SECRET") or "").strip()
    if livekit_secret:
        return

    livekit_token = (os.getenv("LIVEKIT_TOKEN") or "").strip()
    if not livekit_token:
        return

    # JWT access tokens cannot replace LIVEKIT_API_SECRET for agent worker auth.
    if livekit_token.count(".") == 2:
        raise RuntimeError(
            "LIVEKIT_API_SECRET is missing and LIVEKIT_TOKEN looks like a JWT. "
            "The agent worker requires LIVEKIT_API_SECRET (project secret), not a room token."
        )

    os.environ["LIVEKIT_API_SECRET"] = livekit_token


server = AgentServer()


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
