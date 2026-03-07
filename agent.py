import os
import platform
import subprocess
import sys
import asyncio
from pathlib import Path

import tomllib
from dotenv import load_dotenv
from google.genai import types as genai_types
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, function_tool
from livekit.plugins import google, silero

load_dotenv(".env")

ROOT = Path(__file__).resolve().parent
DEFAULTS_PATH = ROOT / "config" / "defaults.toml"


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


def _load_agent_defaults() -> dict[str, object]:
    try:
        with DEFAULTS_PATH.open("rb") as f:
            payload = tomllib.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Missing defaults file: {DEFAULTS_PATH}. "
            "Commit config/defaults.toml with non-secret runtime settings."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to read defaults file {DEFAULTS_PATH}: {exc}") from exc

    defaults = payload.get("agent")
    if not isinstance(defaults, dict):
        raise RuntimeError(
            f"Invalid defaults format in {DEFAULTS_PATH}: expected [agent] table."
        )
    return defaults


AGENT_DEFAULTS = _load_agent_defaults()


def _get_setting(name: str) -> str:
    env_value = (os.getenv(name) or "").strip()
    if env_value:
        return env_value

    default_value = AGENT_DEFAULTS.get(name)
    if isinstance(default_value, str):
        value = default_value.strip()
        if value:
            return value
    elif isinstance(default_value, (int, float)):
        return str(default_value)

    raise RuntimeError(
        f"Missing runtime setting: {name}. "
        f"Set env var {name} or [agent].{name} in {DEFAULTS_PATH}."
    )


def _get_optional_setting(name: str) -> str:
    env_value = (os.getenv(name) or "").strip()
    if env_value:
        return env_value

    default_value = AGENT_DEFAULTS.get(name)
    if isinstance(default_value, str):
        return default_value.strip()
    if isinstance(default_value, (int, float)):
        return str(default_value)
    return ""


def _get_float_setting(name: str) -> float:
    raw = _get_setting(name)
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid float runtime setting for {name}: {raw!r}"
        ) from exc


def _get_bool_setting(name: str) -> bool:
    env_value = (os.getenv(name) or "").strip()
    if env_value:
        normalized = env_value.lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise RuntimeError(
            f"Invalid boolean runtime setting for {name}: {env_value!r}."
        )

    default_value = AGENT_DEFAULTS.get(name)
    if isinstance(default_value, bool):
        return default_value
    if isinstance(default_value, str):
        normalized = default_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized:
            raise RuntimeError(
                f"Invalid boolean default setting for {name}: {default_value!r} in {DEFAULTS_PATH}."
            )
    if isinstance(default_value, (int, float)):
        return bool(default_value)
    return False


STT_MODEL = _get_setting("STT_MODEL")
LLM_MODEL = _get_setting("LLM_MODEL")
TTS_MODEL = _get_setting("TTS_MODEL")
TTS_VOICE_NAME = _get_setting("TTS_VOICE_NAME")
GOOGLE_STT_LOCATION = _get_setting("GOOGLE_STT_LOCATION")
GOOGLE_CLOUD_PROJECT = _get_optional_setting("GOOGLE_CLOUD_PROJECT")
GOOGLE_LLM_LOCATION = _get_optional_setting("GOOGLE_LLM_LOCATION") or "us-central1"
STT_LANGUAGE = _get_setting("STT_LANGUAGE")
STT_USE_STREAMING = _get_bool_setting("STT_USE_STREAMING")
MIN_ENDPOINTING_DELAY = _get_float_setting("MIN_ENDPOINTING_DELAY")
MAX_ENDPOINTING_DELAY = _get_float_setting("MAX_ENDPOINTING_DELAY")
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
    key_files = (
        "README.md",
        "agent.py",
        "pyproject.toml",
        "uv.lock",
        ".env.example",
        "config/defaults.toml",
    )
    name, version, requires_python, dependency_count = _read_pyproject(ROOT / "pyproject.toml")
    existing_files = [file_name for file_name in key_files if (ROOT / file_name).exists()]
    missing_files = [file_name for file_name in key_files if not (ROOT / file_name).exists()]

    context_lines = [
        "Project context for your own source code:",
        f"- root: {ROOT}",
        f"- project: {name} {version}",
        f"- requires-python: {requires_python}",
        f"- dependencies-in-pyproject: {dependency_count}",
        f"- models: stt={STT_MODEL}, llm={LLM_MODEL}, tts={TTS_MODEL}",
        f"- stt-streaming: {STT_USE_STREAMING}",
        "- llm-provider: vertex-ai-service-account",
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
    project_context = _build_project_context()

    print("\n[startup] project inspection")
    print(f"[startup] root: {ROOT}")
    print(f"[startup] python: {sys.version.split()[0]} ({platform.python_implementation()})")
    for line in project_context.splitlines()[1:]:
        print(f"[startup] {line[2:] if line.startswith('- ') else line}")


def _google_credentials_file() -> str:
    configured_env = "GOOGLE_CREDENTIALS_FILE"
    legacy_env = "GOOGLE_STT_CREDENTIALS_FILE"
    credentials_file = (
        (os.getenv(configured_env) or "").strip()
        or (os.getenv(legacy_env) or "").strip()
    )
    if not credentials_file:
        raise RuntimeError(
            "Missing required environment variable: GOOGLE_CREDENTIALS_FILE "
            "(or legacy GOOGLE_STT_CREDENTIALS_FILE)."
        )
    configured = Path(credentials_file).expanduser()
    if not configured.is_absolute():
        configured = ROOT / configured
    path = configured
    if not path.exists():
        raise RuntimeError(
            f"Google credentials path does not exist: {path}. "
            f"Check {configured_env}."
        )
    if path.is_dir():
        raise RuntimeError(
            f"Google credentials path is a directory, expected a JSON file: {path}. "
            "If this path is a mounted secret directory, point GOOGLE_CREDENTIALS_FILE "
            "to the JSON file inside it."
        )
    return str(path)


def _build_google_llm() -> google.LLM:
    resolved_llm_model = {
        "gemini-3-flash": "gemini-3-flash-preview",
    }.get(LLM_MODEL, LLM_MODEL)
    if resolved_llm_model == "gemini-3-flash-preview" and GOOGLE_LLM_LOCATION != "global":
        raise RuntimeError(
            "Gemini 3 Flash on Vertex AI currently requires GOOGLE_LLM_LOCATION=global."
        )

    thinking_config = genai_types.ThinkingConfig(
        thinking_level=genai_types.ThinkingLevel.LOW,
        include_thoughts=False,
    )
    llm_kwargs = {
        "model": resolved_llm_model,
        "vertexai": True,
        "location": GOOGLE_LLM_LOCATION,
        "temperature": 0.4,
        "thinking_config": thinking_config,
    }
    if GOOGLE_CLOUD_PROJECT:
        llm_kwargs["project"] = GOOGLE_CLOUD_PROJECT

    return google.LLM(
        **llm_kwargs,
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
    google_credentials_file = _google_credentials_file()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = google_credentials_file
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

    await session.start(
        room=ctx.room,
        agent=Assistant(project_context=project_context),
    )

    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )


if __name__ == "__main__":
    _configure_livekit_auth()
    _print_project_inspection()
    agents.cli.run_app(server)

# trigger restart test
