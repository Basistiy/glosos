import os
import platform
import subprocess
import sys
import asyncio
import json
from pathlib import Path

import tomllib
from dotenv import load_dotenv
from google.genai import types as genai_types
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, function_tool
from livekit.plugins import google, silero

load_dotenv(".env")

STT_MODEL = (os.getenv("STT_MODEL") or "latest_long").strip()
LLM_MODEL = (os.getenv("LLM_MODEL") or "gemini-3-flash-preview").strip()
TTS_MODEL = "chirp_3"
TTS_VOICE_NAME = "en-US-Chirp3-HD-Charon"
GOOGLE_STT_LOCATION = (os.getenv("GOOGLE_STT_LOCATION") or "eu").strip()
GOOGLE_LLM_LOCATION = (os.getenv("GOOGLE_LLM_LOCATION") or "global").strip()
GOOGLE_API_KEY = ((os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "")).strip()
STT_LANGUAGE = (os.getenv("STT_LANGUAGE") or "en-US").strip()
MIN_ENDPOINTING_DELAY = float((os.getenv("MIN_ENDPOINTING_DELAY") or "0.25").strip())
MAX_ENDPOINTING_DELAY = float((os.getenv("MAX_ENDPOINTING_DELAY") or "1.2").strip())
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
        f"- llm-provider: {'gemini-api-key' if GOOGLE_API_KEY else 'vertex-ai'}",
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


def _google_stt_credentials_file() -> str:
    root = Path(__file__).resolve().parent
    configured_env = "GOOGLE_STT_CREDENTIALS_FILE"
    credentials_file = (os.getenv(configured_env) or "").strip()
    if credentials_file:
        configured = Path(credentials_file).expanduser()
        if not configured.is_absolute():
            configured = root / configured
        path = configured
    else:
        fallback = (
            root / ".config" / "keys" / "google-stt-service-account.json"
        )
        if fallback.exists():
            path = fallback
        else:
            path = None
    if path is None:
        raise RuntimeError(
            "Google STT requires credentials. Set GOOGLE_STT_CREDENTIALS_FILE "
            "to a service-account JSON key path."
        )
    if not path.exists():
        raise RuntimeError(
            f"Google STT credentials path does not exist: {path}. "
            f"Check {configured_env}."
        )
    if path.is_dir():
        raise RuntimeError(
            f"Google STT credentials path is a directory, expected a JSON file: {path}. "
            "If this path is a mounted secret directory, point GOOGLE_STT_CREDENTIALS_FILE "
            "to the JSON file inside it."
        )
    return str(path)


def _google_cloud_project_from_credentials(credentials_file: str) -> str | None:
    try:
        with Path(credentials_file).open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    project_id = payload.get("project_id")
    return project_id.strip() if isinstance(project_id, str) and project_id.strip() else None


def _build_google_llm() -> google.LLM:
    thinking_config = genai_types.ThinkingConfig(
        thinking_level=genai_types.ThinkingLevel.LOW,
        include_thoughts=False,
    )
    if GOOGLE_API_KEY:
        return google.LLM(
            model=LLM_MODEL,
            vertexai=False,
            api_key=GOOGLE_API_KEY,
            temperature=0.4,
            thinking_config=thinking_config,
        )
    return google.LLM(
        model=LLM_MODEL,
        vertexai=True,
        location=GOOGLE_LLM_LOCATION,
        temperature=0.4,
        thinking_config=thinking_config,
    )


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
    project_context = _build_project_context()
    stt_credentials_file = _google_stt_credentials_file()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = stt_credentials_file
    if not (os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip():
        inferred_project = _google_cloud_project_from_credentials(stt_credentials_file)
        if inferred_project:
            os.environ["GOOGLE_CLOUD_PROJECT"] = inferred_project
    session = AgentSession(
        stt=google.STT(
            model=STT_MODEL,
            location=GOOGLE_STT_LOCATION,
            languages=STT_LANGUAGE,
            detect_language=False,
            spoken_punctuation=False,
            credentials_file=stt_credentials_file,
        ),
        llm=_build_google_llm(),
        tts=google.TTS(
            model_name=TTS_MODEL,
            voice_name=TTS_VOICE_NAME,
            use_streaming=True,
            credentials_file=stt_credentials_file,
        ),
        vad=silero.VAD.load(),
        turn_detection="vad",
        min_endpointing_delay=MIN_ENDPOINTING_DELAY,
        max_endpointing_delay=MAX_ENDPOINTING_DELAY,
        max_tool_steps=10,
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(project_context=project_context),
    )

    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )


if __name__ == "__main__":
    _print_project_inspection()
    agents.cli.run_app(server)

# trigger restart test
