import os
from datetime import timedelta

from dotenv import load_dotenv
from livekit import api

import token_agent

load_dotenv(".env")


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer for {name}: {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be > 0")
    return value


def _build_livekit_token(identity_env: str, default_identity: str) -> tuple[str, str, str, int]:
    api_key = _required_env("LIVEKIT_API_KEY")
    api_secret = _required_env("LIVEKIT_API_SECRET")
    room = (os.getenv("LIVEKIT_ROOM") or "").strip() or "default-room"
    identity = (os.getenv(identity_env) or "").strip() or default_identity
    ttl_seconds = _optional_int_env("LIVEKIT_TOKEN_TTL_SECONDS", 3600)

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(seconds=ttl_seconds))
        .to_jwt()
    )
    return token, room, identity, ttl_seconds


def main() -> None:
    livekit_url = _required_env("LIVEKIT_URL")
    agent_token, room, agent_identity, ttl_seconds = _build_livekit_token(
        "LIVEKIT_IDENTITY", "token-agent"
    )
    os.environ["LIVEKIT_TOKEN"] = agent_token
    client_token, _, client_identity, _ = _build_livekit_token(
        "LIVEKIT_CLIENT_IDENTITY", "human-test"
    )

    print(
        f"[run-token-agent] generated agent token for room={room!r} "
        f"ttl={ttl_seconds}s identity={agent_identity!r}"
    )
    print(f"[run-token-agent] livekit url: {livekit_url}")
    print(
        f"[run-token-agent] client join token for room={room!r} "
        f"identity={client_identity!r}:"
    )
    print(client_token)

    token_agent.main()


if __name__ == "__main__":
    main()
