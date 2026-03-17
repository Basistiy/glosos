import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "user" / "system" / "scripts"
LOGS_DIR = SCRIPTS_DIR / "logs"
SCRIPT_TIMEOUT_SECONDS = 300
SCAN_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class ScriptResult:
    path: Path
    exit_code: int
    timed_out: bool
    stdout: str
    stderr: str
    duration_seconds: float


def _discover_scripts() -> list[Path]:
    if not SCRIPTS_DIR.exists():
        return []
    if not SCRIPTS_DIR.is_dir():
        print(f"[scheduler] scripts path is not a directory: {SCRIPTS_DIR}")
        return []

    scripts: list[Path] = []
    for path in sorted(SCRIPTS_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix != ".py":
            continue
        if path.name.startswith(".") or path.name.startswith("_"):
            continue
        scripts.append(path)
    return scripts


def _summarize_output(label: str, output: str) -> None:
    normalized = output.strip()
    if not normalized:
        return
    for line in normalized.splitlines():
        print(f"[scheduler] {label}: {line}")


def _append_script_log(path: Path, lines: list[str]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{path.stem}.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _persist_result(result: ScriptResult) -> None:
    lines = [
        f"[{_timestamp()}] script={result.path.name}",
        f"exit_code={result.exit_code}",
        f"timed_out={str(result.timed_out).lower()}",
        f"duration_seconds={result.duration_seconds:.1f}",
        "stdout:",
        result.stdout or "<empty>",
        "stderr:",
        result.stderr or "<empty>",
        "-" * 40,
    ]
    _append_script_log(result.path, lines)


def _run_script(path: Path) -> ScriptResult:
    started_at = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_seconds = time.monotonic() - started_at
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        return ScriptResult(
            path=path,
            exit_code=-1,
            timed_out=True,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
        )

    duration_seconds = time.monotonic() - started_at
    return ScriptResult(
        path=path,
        exit_code=completed.returncode,
        timed_out=False,
        stdout=(completed.stdout or "").strip(),
        stderr=(completed.stderr or "").strip(),
        duration_seconds=duration_seconds,
    )


def _run_batch() -> None:
    print(f"[scheduler] batch started at {_timestamp()}")
    scripts = _discover_scripts()
    if not scripts:
        print(f"[scheduler] no runnable scripts found in {SCRIPTS_DIR}")
        return

    print(f"[scheduler] running {len(scripts)} script(s) from {SCRIPTS_DIR}")
    for path in scripts:
        print(f"[scheduler] starting: {path.name}")
        result = _run_script(path)
        if result.timed_out:
            print(
                f"[scheduler] timed out after {SCRIPT_TIMEOUT_SECONDS}s: "
                f"{path.name} ({result.duration_seconds:.1f}s)"
            )
        else:
            print(
                f"[scheduler] finished: {path.name} "
                f"exit_code={result.exit_code} duration={result.duration_seconds:.1f}s"
            )
        _summarize_output(f"{path.name} stdout", result.stdout)
        _summarize_output(f"{path.name} stderr", result.stderr)
        _persist_result(result)


def _scheduler_loop() -> None:
    while True:
        started_at = time.monotonic()
        try:
            _run_batch()
        except Exception as exc:
            print(f"[scheduler] batch failed: {exc.__class__.__name__}: {exc}")

        elapsed = time.monotonic() - started_at
        sleep_for = max(0.0, SCAN_INTERVAL_SECONDS - elapsed)
        if sleep_for == 0.0 and elapsed > SCAN_INTERVAL_SECONDS:
            print(
                f"[scheduler] batch overran interval: "
                f"elapsed={elapsed:.1f}s interval={SCAN_INTERVAL_SECONDS}s"
            )
        time.sleep(sleep_for)


def start_script_scheduler() -> threading.Thread:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(
        target=_scheduler_loop,
        name="user-script-scheduler",
        daemon=True,
    )
    thread.start()
    print(
        f"[scheduler] started minute runner for {SCRIPTS_DIR} "
        f"with timeout={SCRIPT_TIMEOUT_SECONDS}s"
    )
    return thread


def main() -> None:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"[scheduler] started minute runner for {SCRIPTS_DIR} "
        f"with timeout={SCRIPT_TIMEOUT_SECONDS}s"
    )
    _scheduler_loop()


if __name__ == "__main__":
    main()
