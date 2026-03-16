import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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

try:
    from sounds import emit_ready_sound
except ModuleNotFoundError:
    async def emit_ready_sound(_room: rtc.Room) -> None:
        return None


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


def _apply_agent_env(agent_env: dict[str, Any] | None) -> None:
    for key in ("AGENT_GENDER", "AGENT_LANGUAGE", "AGENT_NAME"):
        if agent_env and isinstance(agent_env.get(key), str):
            value = agent_env[key].strip()
            if value:
                os.environ[key] = value
                continue
        os.environ.pop(key, None)


async def _run_token_session(
    *,
    livekit_token: str,
    linked_identity: str,
    agent_env: dict[str, Any] | None,
    stop_event: asyncio.Event | None,
) -> None:
    _apply_agent_env(agent_env)
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

    try:
        if stop_event is not None and stop_event.is_set():
            print("[token-agent] start cancelled before room.connect (stop already requested)")
            return

        await room.connect(LIVEKIT_URL, livekit_token)
        print(f"[token-agent] connected to room: {room.name}")
        if linked_identity:
            print(f"[token-agent] room input participant_identity={linked_identity}")
        _schedule_state_publish("initializing")

        if stop_event is not None and stop_event.is_set():
            print("[token-agent] stop requested right after connect, disconnecting room")
            await room.disconnect()
            await disconnected.wait()
            return

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

        if stop_event is None:
            await disconnected.wait()
        else:
            stop_wait = asyncio.create_task(stop_event.wait())
            disconnect_wait = asyncio.create_task(disconnected.wait())
            done, pending = await asyncio.wait(
                {stop_wait, disconnect_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if stop_wait in done and not disconnected.is_set():
                print("[token-agent] stop requested, disconnecting room")
                try:
                    await room.disconnect()
                except Exception as exc:
                    print(f"[token-agent] room.disconnect failed: {exc}")
                await disconnected.wait()
    finally:
        for task in list(state_publish_tasks):
            task.cancel()
        close_session = getattr(session, "aclose", None)
        if callable(close_session):
            try:
                await close_session()
            except Exception as exc:
                print(f"[token-agent] session close failed: {exc}")
        if not disconnected.is_set():
            try:
                await room.disconnect()
            except Exception:
                pass


class TokenAgentDaemon:
    def __init__(self) -> None:
        self._active_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def _running(self) -> bool:
        return self._active_task is not None and not self._active_task.done()

    async def start(self, token: str, linked_identity: str, agent_env: dict[str, Any] | None) -> None:
        if self._running():
            print("[token-agent] start ignored: session already running")
            return

        self._stop_event = asyncio.Event()

        async def _runner() -> None:
            unexpected_end = False
            try:
                await _run_token_session(
                    livekit_token=token,
                    linked_identity=linked_identity,
                    agent_env=agent_env,
                    stop_event=self._stop_event,
                )
            except Exception as exc:
                print(f"[token-agent] session failed: {exc}")
                unexpected_end = True
            finally:
                print("[token-agent] session ended")
                stop_requested = self._stop_event is not None and self._stop_event.is_set()
                if not stop_requested:
                    unexpected_end = True
                if unexpected_end:
                    print(
                        "[token-agent] session ended unexpectedly; exiting daemon for supervisor restart"
                    )
                    os._exit(70)

        self._active_task = asyncio.create_task(_runner())

    async def stop(self, reason: str = "requested") -> None:
        if not self._running():
            print("[token-agent] stop ignored: no active session")
            return

        print(f"[token-agent] stop requested: {reason}")
        assert self._stop_event is not None
        self._stop_event.set()

        assert self._active_task is not None
        try:
            await asyncio.wait_for(self._active_task, timeout=15)
        except asyncio.TimeoutError:
            if not self._active_task.done():
                print("[token-agent] stop timeout; cancelling active session")
                self._active_task.cancel()
                await asyncio.gather(self._active_task, return_exceptions=True)

        self._active_task = None
        self._stop_event = None


async def run_daemon() -> None:
    daemon = TokenAgentDaemon()
    print("[token-agent] daemon ready")

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if line == "":
            await daemon.stop("stdin closed")
            print("[token-agent] stdin closed; daemon exiting")
            return

        payload_raw = line.strip()
        if not payload_raw:
            continue

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as exc:
            print(f"[token-agent] invalid command JSON: {exc}")
            continue

        cmd = str(payload.get("cmd", "")).strip().lower()
        if cmd == "start":
            token = str(payload.get("token", "")).strip()
            if not token:
                print("[token-agent] start ignored: missing token")
                continue
            linked_identity = str(payload.get("linked_identity", "")).strip()
            agent_env_raw = payload.get("agent_env")
            agent_env = agent_env_raw if isinstance(agent_env_raw, dict) else None
            await daemon.start(token, linked_identity, agent_env)
        elif cmd == "stop":
            reason = str(payload.get("reason", "requested")).strip() or "requested"
            await daemon.stop(reason)
        elif cmd == "shutdown":
            await daemon.stop("shutdown")
            print("[token-agent] shutdown command received")
            return
        else:
            print(f"[token-agent] unknown command: {cmd}")


async def run_once_mode() -> None:
    livekit_token = _required_env("LIVEKIT_TOKEN")
    linked_identity = _optional_env("LIVEKIT_CLIENT_IDENTITY")
    await _run_token_session(
        livekit_token=livekit_token,
        linked_identity=linked_identity,
        agent_env=None,
        stop_event=None,
    )


def main() -> None:
    _print_project_inspection()
    try:
        if "--daemon" in sys.argv:
            asyncio.run(run_daemon())
        else:
            asyncio.run(run_once_mode())
    except KeyboardInterrupt:
        print("[token-agent] stopped by user")


if __name__ == "__main__":
    main()
