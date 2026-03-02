import os
import platform
import subprocess
import sys
import asyncio
from pathlib import Path

import tomllib
from dotenv import load_dotenv
from google.genai import types as genai_types
from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession, function_tool, room_io
from livekit.plugins import google, noise_cancellation, silero

load_dotenv(".env")

STT_MODEL = "cartesia/ink-whisper"
LLM_MODEL = "gemini-3-flash-preview"
TTS_MODEL = "inworld/inworld-tts-1.5-max"
MAX_TOOL_OUTPUT_CHARS = 4000
PYTHON_TOOL_TIMEOUT_SECONDS = 10
USER_SYSTEM_INSTRUCTIONS_PATH = Path("user/system/instructions.md")
DEFAULT_USER_SYSTEM_INSTRUCTIONS = """This file is loaded into the agent system instructions at startup.
Keep the text concise and task-focused.
"""


def _read_pyproject(pyproject_path: Path) -> tuple[str, str, str, int]:
    if not pyproject_path.exists():
        return ("unknown", "unknown", "unknown", 0)

    with pyproject_path.open("rb") as f:
        pyproject = tomllib.load(f)

    project = pyproject.get("project", {})
    name = project.get("name", "unknown")
    version = project.get("version", "unknown")
    requires_python = project.get("requires-python", "unknown")
    dependencies = project.get("dependencies", [])
    return (name, version, requires_python, len(dependencies))


def _build_project_context() -> str:
    root = Path(__file__).resolve().parent
    key_files = ("README.md", "agent.py", "pyproject.toml", "uv.lock", ".env.example")
    name, version, requires_python, dependency_count = _read_pyproject(root / "pyproject.toml")
    existing_files = [file_name for file_name in key_files if (root / file_name).exists()]
    missing_files = [file_name for file_name in key_files if not (root / file_name).exists()]

    context_lines = [
        "Project context for your own source code:",
        f"- root: {root}",
        f"- project: {name} {version}",
        f"- requires-python: {requires_python}",
        f"- dependencies-in-pyproject: {dependency_count}",
        f"- models: stt={STT_MODEL}, llm={LLM_MODEL}, tts={TTS_MODEL}",
        f"- key-files-present: {', '.join(existing_files) if existing_files else 'none'}",
        f"- key-files-missing: {', '.join(missing_files) if missing_files else 'none'}",
    ]
    return "\n".join(context_lines)


def _read_user_system_instructions(root: Path) -> str:
    path = root / USER_SYSTEM_INSTRUCTIONS_PATH
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_USER_SYSTEM_INSTRUCTIONS, encoding="utf-8")
            content = DEFAULT_USER_SYSTEM_INSTRUCTIONS.strip()
        except OSError as exc:
            return f"[failed to create {USER_SYSTEM_INSTRUCTIONS_PATH.as_posix()}: {exc.__class__.__name__}: {exc}]"
    except OSError as exc:
        return f"[failed to read {USER_SYSTEM_INSTRUCTIONS_PATH.as_posix()}: {exc.__class__.__name__}: {exc}]"

    return content or "[file is empty]"


def _print_project_inspection() -> None:
    root = Path(__file__).resolve().parent
    project_context = _build_project_context()

    print("\n[startup] project inspection")
    print(f"[startup] root: {root}")
    print(f"[startup] python: {sys.version.split()[0]} ({platform.python_implementation()})")
    for line in project_context.splitlines()[1:]:
        print(f"[startup] {line[2:] if line.startswith('- ') else line}")


class Assistant(Agent):
    def __init__(self, project_context: str) -> None:
        root = Path(__file__).resolve().parent
        user_system_instructions = _read_user_system_instructions(root)
        super().__init__(
            instructions="""You are a helpful voice AI assistant.
            You eagerly assist users with their questions by providing information from your extensive knowledge.
            Your responses are concise, to the point, and without any complex formatting or punctuation including emojis, asterisks, or other symbols.
            The repository described below is your own source code.
            Treat this project context as authoritative for how you are implemented and configured.
            When users ask about your behavior, capabilities, dependencies, setup, or files, ground your answers in this context.
            You can execute Python in your own project using the execute_python tool when computation or code validation is needed.
            If you need to preserve important information across restarts, update /user/system/instructions.md with concise, durable notes only.
            You also have the following runtime project context:


            """
            + project_context
            + """

            Also include and follow the editable user system instructions from /user/system/instructions.md:

            """
            + user_system_instructions,
        )

    @function_tool
    async def execute_python(self, code: str) -> str:
        """Execute Python code in the project environment and return stdout/stderr and exit code."""
        project_root = Path(__file__).resolve().parent

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-c", code],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=PYTHON_TOOL_TIMEOUT_SECONDS,
                check=False,
            )

        try:
            result = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return f"timed out after {PYTHON_TOOL_TIMEOUT_SECONDS}s"
        except Exception as exc:
            return f"execution error: {exc.__class__.__name__}: {exc}"

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        combined = f"exit_code={result.returncode}\nstdout:\n{stdout or '<empty>'}\nstderr:\n{stderr or '<empty>'}"
        if len(combined) > MAX_TOOL_OUTPUT_CHARS:
            combined = combined[:MAX_TOOL_OUTPUT_CHARS] + "\n...<truncated>"
        return combined

server = AgentServer()

@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    if not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY is required for the Google Gemini LLM.")

    project_context = _build_project_context()
    session = AgentSession(
        stt=STT_MODEL,
        llm=google.LLM(
            model=LLM_MODEL,
            temperature=0.8,
            thinking_config=genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel.MEDIUM,
                include_thoughts=True,
            ),
        ),
        tts=TTS_MODEL,
        vad=silero.VAD.load(),
        turn_detection="vad",
        max_tool_steps=10,
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(project_context=project_context),
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
    _print_project_inspection()
    agents.cli.run_app(server)

# trigger restart test
