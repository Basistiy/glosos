import os

from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession, room_io
from livekit.plugins import google, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from google.genai import types as genai_types

load_dotenv(".env")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a helpful voice AI assistant.
            You eagerly assist users with their questions by providing information from your extensive knowledge.
            Your responses are concise, to the point, and without any complex formatting or punctuation including emojis, asterisks, or other symbols.
            You are curious, friendly, and have a sense of humor.""",
        )

server = AgentServer()

@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: agents.JobContext):
    if not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY is required for the Google Gemini LLM.")

    session = AgentSession(
        stt="assemblyai/universal-streaming",
        llm=google.LLM(
        model="gemini-3-flash-preview",
        temperature=0.8,
        thinking_config=genai_types.ThinkingConfig(
            thinking_level=genai_types.ThinkingLevel.MEDIUM,
            include_thoughts=True,
        )
        ),
        tts="inworld/inworld-tts-1.5-max",
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: noise_cancellation.BVCTelephony() if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP else noise_cancellation.BVC(),
            ),
        ),
    )

    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
