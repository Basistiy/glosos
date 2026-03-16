import asyncio
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents.voice import room_io

from agent import (
    Assistant,
    LIVEKIT_URL,
    _build_project_context,
    _print_project_inspection,
    build_agent_session,
    register_incoming_file_handler,
)
from sounds import emit_ready_sound


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


_builtin_print = print


def print(*args, **kwargs):  # type: ignore[no-redef]
    _builtin_print(f"[{_now_hms()}]", *args, **kwargs)


load_dotenv("config/.env")
ATTRIBUTE_AGENT_STATE = "lk.agent.state"


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


async def run_token_agent() -> None:
    livekit_token = _required_env("LIVEKIT_TOKEN")
    linked_identity = _optional_env("LIVEKIT_CLIENT_IDENTITY")

    project_context = _build_project_context()

    room = rtc.Room()
    disconnected = asyncio.Event()
    register_incoming_file_handler(room)

    @room.on("disconnected")
    def _on_disconnected(reason: object) -> None:
        print(f"[token-agent] disconnected: {reason}")
        disconnected.set()

    session = build_agent_session()
    state_publish_tasks: set[asyncio.Task[None]] = set()

    async def _publish_agent_state(state: str) -> None:
        for attempt in range(1, 4):
            try:
                await room.local_participant.set_attributes({ATTRIBUTE_AGENT_STATE: state})
                return
            except Exception as exc:
                if attempt == 3:
                    print(f"[token-agent] failed to publish {ATTRIBUTE_AGENT_STATE}={state}: {exc}")
                    return
                await asyncio.sleep(0.2 * attempt)

    def _schedule_state_publish(state: str) -> None:
        task = asyncio.create_task(_publish_agent_state(state))
        state_publish_tasks.add(task)
        task.add_done_callback(lambda t: state_publish_tasks.discard(t))

    @session.on("agent_state_changed")
    def _on_agent_state_changed(event: object) -> None:
        new_state = getattr(event, "new_state", "")
        old_state = getattr(event, "old_state", "")
        print(f"[token-agent] state: {old_state} -> {new_state}")
        if isinstance(new_state, str) and new_state:
            _schedule_state_publish(new_state)
    
    async def _send_file(path: Path, topic: str, destination_identities: list[str]) -> str:
        info = await room.local_participant.send_file(
            str(path),
            topic=topic,
            destination_identities=destination_identities,
        )
        return info.stream_id

    await room.connect(LIVEKIT_URL, livekit_token)
    print(f"[token-agent] connected to room: {room.name}")
    if linked_identity:
        print(f"[token-agent] room input participant_identity={linked_identity}")
    _schedule_state_publish("initializing")

    room_options = room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(pre_connect_audio=False),
    )
    if linked_identity:
        room_options.participant_identity = linked_identity

    await session.start(
        room=room,
        agent=Assistant(project_context=project_context, send_file_fn=_send_file, room=room),
        room_options=room_options,
    )
    _schedule_state_publish("listening")
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
