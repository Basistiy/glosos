import os
import platform
import subprocess
import sys
import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import tomllib
from livekit import rtc
from google.genai import types as genai_types
from livekit.agents import Agent, AgentSession, function_tool, get_job_context
from livekit.agents.llm import ImageContent
from livekit.plugins import google, silero

ROOT = Path(__file__).resolve().parent
DEFAULTS_PATH = ROOT / "config" / "defaults.toml"


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


def _setting_source_value(name: str) -> object | None:
    return AGENT_DEFAULTS.get(name)


def _required_str_setting(name: str) -> str:
    value = _setting_source_value(name)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    elif isinstance(value, (int, float)):
        return str(value)

    raise RuntimeError(
        f"Missing runtime setting: {name}. "
        f"Set [agent].{name} in {DEFAULTS_PATH}."
    )


def _required_float_setting(name: str) -> float:
    raw = _required_str_setting(name)
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float runtime setting for {name}: {raw!r}") from exc


def _bool_setting(name: str) -> bool:
    value = _setting_source_value(name)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized:
            raise RuntimeError(f"Invalid boolean runtime setting for {name}: {value!r}.")
    return False


STT_MODEL = _required_str_setting("STT_MODEL")
LLM_MODEL = _required_str_setting("LLM_MODEL")
TTS_MODEL = _required_str_setting("TTS_MODEL")
TTS_VOICE_NAME = _required_str_setting("TTS_VOICE_NAME")
LIVEKIT_URL = _required_str_setting("LIVEKIT_URL")
GOOGLE_CREDENTIALS_FILE = _required_str_setting("GOOGLE_CREDENTIALS_FILE")
GOOGLE_STT_LOCATION = _required_str_setting("GOOGLE_STT_LOCATION")
GOOGLE_LLM_LOCATION = _required_str_setting("GOOGLE_LLM_LOCATION")
STT_LANGUAGE = _required_str_setting("STT_LANGUAGE")
STT_USE_STREAMING = _bool_setting("STT_USE_STREAMING")
MIN_ENDPOINTING_DELAY = _required_float_setting("MIN_ENDPOINTING_DELAY")
MAX_ENDPOINTING_DELAY = _required_float_setting("MAX_ENDPOINTING_DELAY")
MAX_TOOL_OUTPUT_CHARS = 4000
PYTHON_TOOL_TIMEOUT_SECONDS = 10
MAX_SEND_FILE_BYTES = 25 * 1024 * 1024
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


def _list_user_files(user_root: Path) -> list[str]:
    if not user_root.exists() or not user_root.is_dir():
        return []

    files: list[str] = []
    for path in sorted(user_root.rglob("*")):
        if not path.is_file():
            continue
        rel_from_root = path.relative_to(ROOT).as_posix()
        files.append(rel_from_root)
    return files


def _build_project_context() -> str:
    key_files = (
        "README.md",
        "agent.py",
        "pyproject.toml",
        "uv.lock",
        "config/.env.example",
        "config/defaults.toml",
    )
    name, version, requires_python, dependency_count = _read_pyproject(ROOT / "pyproject.toml")
    existing_files = [file_name for file_name in key_files if (ROOT / file_name).exists()]
    missing_files = [file_name for file_name in key_files if not (ROOT / file_name).exists()]
    user_root = ROOT / "user"
    user_files = _list_user_files(user_root)

    context_lines = [
        "Project context for your own source code:",
        f"- root: {ROOT}",
        f"- project: {name} {version}",
        f"- requires-python: {requires_python}",
        f"- dependencies-in-pyproject: {dependency_count}",
        f"- models: stt={STT_MODEL}, llm={LLM_MODEL}, tts={TTS_MODEL}",
        f"- livekit-url: {LIVEKIT_URL}",
        f"- stt-streaming: {STT_USE_STREAMING}",
        "- llm-provider: vertex-ai-service-account",
        f"- key-files-present: {', '.join(existing_files) if existing_files else 'none'}",
        f"- key-files-missing: {', '.join(missing_files) if missing_files else 'none'}",
        f"- user-root: {user_root if user_root.exists() else f'{user_root} (missing)'}",
        f"- user-files-count: {len(user_files)}",
    ]
    if user_files:
        context_lines.append("- user-files:")
        context_lines.extend(f"  - {path}" for path in user_files)
    else:
        context_lines.append("- user-files: none")

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
    configured = Path(GOOGLE_CREDENTIALS_FILE).expanduser()
    if not configured.is_absolute():
        configured = ROOT / configured
    path = configured
    if not path.exists():
        raise RuntimeError(
            f"Google credentials path does not exist: {path}. "
            f"Check [agent].GOOGLE_CREDENTIALS_FILE in {DEFAULTS_PATH}."
        )
    if path.is_dir():
        raise RuntimeError(
            f"Google credentials path is a directory, expected a JSON file: {path}. "
            "If this path is a mounted secret directory, point [agent].GOOGLE_CREDENTIALS_FILE "
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

    return google.LLM(
        **llm_kwargs,
    )


def build_agent_session() -> AgentSession:
    google_credentials_file = _google_credentials_file()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = google_credentials_file
    return AgentSession(
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


FileSendFn = Callable[[Path, str, list[str]], Awaitable[str]]
DEFAULT_INCOMING_FILES_TOPIC = "lk.chat"
DEFAULT_INCOMING_FILES_DIR = ROOT / "user" / "incoming"


def _stream_info_value(info: object, key: str) -> str:
    if hasattr(info, key):
        value = getattr(info, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(info, dict):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _unique_file_path(base_dir: Path, file_name: str) -> Path:
    candidate = base_dir / file_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem or "file"
    suffix = candidate.suffix
    for index in range(1, 1000):
        next_path = base_dir / f"{stem}-{index}{suffix}"
        if not next_path.exists():
            return next_path
    raise RuntimeError(f"too many files with name prefix: {stem}")


def register_incoming_file_handler(
    room: rtc.Room,
    *,
    topic: str = DEFAULT_INCOMING_FILES_TOPIC,
    save_dir: Path = DEFAULT_INCOMING_FILES_DIR,
) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    active_tasks: set[asyncio.Task[None]] = set()

    async def _persist_file(reader: rtc.ByteStreamReader, participant_identity: str) -> None:
        info = reader.info
        stream_id = _stream_info_value(info, "id") or "stream"
        incoming_name = _stream_info_value(info, "name")
        safe_name = Path(incoming_name).name if incoming_name else ""
        if not safe_name:
            safe_name = f"{stream_id}.bin"

        target = _unique_file_path(save_dir, safe_name)
        with target.open("wb") as f:
            async for chunk in reader:
                f.write(chunk)
        print(
            f"[files] received {target.name} from {participant_identity} "
            f"on topic={topic} stream_id={stream_id} saved_to={target}"
        )

    def _handle_stream(reader: rtc.ByteStreamReader, participant_identity: str) -> None:
        task = asyncio.create_task(_persist_file(reader, participant_identity))
        active_tasks.add(task)
        task.add_done_callback(lambda t: active_tasks.discard(t))

    room.register_byte_stream_handler(topic, _handle_stream)


class Assistant(Agent):
    def __init__(
        self,
        project_context: str,
        send_file_fn: FileSendFn | None = None,
        room: rtc.Room | None = None,
    ) -> None:
        root = Path(__file__).resolve().parent
        self._root = root
        self._send_file_fn = send_file_fn
        self._room = room
        self._latest_frame: object | None = None
        self._video_stream: rtc.VideoStream | None = None
        self._video_tasks: set[asyncio.Task[None]] = set()
        user_system_instructions = _read_user_system_instructions(root)
        super().__init__(
            instructions="""You are a helpful voice AI assistant.
            You eagerly assist users with their questions by providing information from your extensive knowledge.
            Your responses are concise, to the point, and without any complex formatting or punctuation including emojis, asterisks, or other symbols.
            You can execute Python in your own project using the execute_python tool. Use python to read files, inspect the environment, perform calculations, making network requests.
            The environment includes file/data libraries: pypdf, pdfplumber, pandas, matplotlib, openpyxl, xlsxwriter, orjson, ruamel.yaml, Pillow, python-docx, python-pptx, reportlab.
            For web browsing and extraction, the environment includes: httpx, beautifulsoup4, lxml, trafilatura, feedparser, duckduckgo-search.
            Prefer these libraries for file creation/manipulation and web research tasks before suggesting extra installs.
            You can create and manage recurring background tasks by writing Python scripts to app/user/system/scripts which are executed every 60 seconds by script_scheduler.py.
            You can send files to the user using the send_file_to_user tool.
            All text files created should be in .md format unless another format is specified.
            If you need to log some data like meal calories or weight tracking, create or update .json files in the /app/user directory.
            Api keys are stored in user/system/keys.md.
            Store all user data files and tracking files in the /app/user directory. You have read/write access to this directory and its subdirectories.
            When a user sends a file, it is stored in /app/user/incoming.
            When users ask about your behavior, capabilities, dependencies, setup, or files, ground your answers in this context:


            """
            + project_context
            + """

            If you need to preserve important information across restarts, update app/user/system/instructions.md with concise, durable notes only.

            """
            + user_system_instructions,
        )

    async def on_enter(self) -> None:
        room = self._room
        if room is None:
            room = get_job_context().room

        def _attach_track(track: rtc.Track) -> None:
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            self._create_video_stream(track)

        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track is not None:
                    _attach_track(publication.track)

        @room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            del publication, participant
            _attach_track(track)

    async def on_user_turn_completed(self, turn_ctx: object, new_message: object) -> None:
        del turn_ctx
        if self._latest_frame is None:
            return
        if not hasattr(new_message, "content"):
            return
        content = getattr(new_message, "content")
        if not isinstance(content, list):
            return
        content.append(ImageContent(image=self._latest_frame))
        self._latest_frame = None

    def _create_video_stream(self, track: rtc.Track) -> None:
        if self._video_stream is not None:
            self._video_stream.close()

        self._video_stream = rtc.VideoStream(track)
        stream = self._video_stream

        async def _read_stream() -> None:
            async for event in stream:
                self._latest_frame = event.frame

        task = asyncio.create_task(_read_stream())
        self._video_tasks.add(task)
        task.add_done_callback(lambda t: self._video_tasks.discard(t))

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

    @function_tool
    async def send_file_to_user(
        self,
        file_path: str,
        topic: str = DEFAULT_INCOMING_FILES_TOPIC,
        destination_identity: str = "",
    ) -> str:
        """Send a local file to a participant over LiveKit byte streams."""
        if self._send_file_fn is None:
            return "file sending is not configured for this agent session"

        requested = Path(file_path.strip()).expanduser()
        user_root = (self._root / "user").resolve()
        if requested.is_absolute():
            resolved = requested.resolve()
        else:
            resolved = (user_root / requested).resolve()

        try:
            resolved.relative_to(user_root)
        except ValueError:
            return (
                f"invalid path: {resolved}. "
                f"Only files under {user_root} can be sent."
            )

        if not resolved.exists():
            return f"file not found: {resolved}"
        if not resolved.is_file():
            return f"path is not a file: {resolved}"
        file_size = resolved.stat().st_size
        if file_size > MAX_SEND_FILE_BYTES:
            return (
                f"file is too large ({file_size} bytes). "
                f"Maximum allowed size is {MAX_SEND_FILE_BYTES} bytes."
            )

        destinations = [destination_identity.strip()] if destination_identity.strip() else []
        stream_id = await self._send_file_fn(
            resolved,
            topic.strip() or DEFAULT_INCOMING_FILES_TOPIC,
            destinations,
        )
        return (
            f"sent file {resolved.name} ({file_size} bytes) on topic "
            f"{topic.strip() or DEFAULT_INCOMING_FILES_TOPIC} with stream_id={stream_id}"
        )
