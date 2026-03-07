import asyncio
import os

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentSession
from livekit.plugins import google, silero

from agent import (
    Assistant,
    GOOGLE_STT_LOCATION,
    MAX_ENDPOINTING_DELAY,
    MIN_ENDPOINTING_DELAY,
    STT_LANGUAGE,
    STT_MODEL,
    STT_USE_STREAMING,
    TTS_MODEL,
    TTS_VOICE_NAME,
    _build_google_llm,
    _build_project_context,
    _google_credentials_file,
    _print_project_inspection,
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
    google_credentials_file = _google_credentials_file()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = google_credentials_file

    room = rtc.Room()
    disconnected = asyncio.Event()

    @room.on("disconnected")
    def _on_disconnected(reason: object) -> None:
        print(f"[token-agent] disconnected: {reason}")
        disconnected.set()

    session = AgentSession(
        stt=google.STT(
            model=STT_MODEL,
            location=GOOGLE_STT_LOCATION,
            languages=STT_LANGUAGE,
            detect_language=False,
            spoken_punctuation=False,
            use_streaming=STT_USE_STREAMING,
            credentials_file=google_credentials_file,
        ),
        llm=_build_google_llm(),
        tts=google.TTS(
            model_name=TTS_MODEL,
            voice_name=TTS_VOICE_NAME,
            use_streaming=True,
            credentials_file=google_credentials_file,
        ),
        vad=silero.VAD.load(),
        turn_detection="vad",
        min_endpointing_delay=MIN_ENDPOINTING_DELAY,
        max_endpointing_delay=MAX_ENDPOINTING_DELAY,
        max_tool_steps=10,
    )

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
