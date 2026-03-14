import json
import os
from typing import Any
from urllib import error, parse, request

from dotenv import load_dotenv
import tomllib

from agent import DEFAULTS_PATH, LIVEKIT_URL
import token_agent

load_dotenv("config/.env")


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def _optional_setting(name: str, default: str = "") -> str:
    try:
        with DEFAULTS_PATH.open("rb") as f:
            payload = tomllib.load(f)
    except (FileNotFoundError, OSError):
        return default
    defaults = payload.get("agent")
    if not isinstance(defaults, dict):
        return default
    value = defaults.get(name)
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _required_public_config(name: str) -> str:
    value = _optional_setting(name) or _optional_env(name)
    if not value:
        raise RuntimeError(
            f"Missing required non-secret config: {name}. "
            f"Set [agent].{name} in {DEFAULTS_PATH} (preferred) or {name} in config/.env."
        )
    return value


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} when calling {url}: {body}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error when calling {url}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}: {raw[:500]!r}") from exc


def _firebase_sign_in() -> tuple[str, str]:
    api_key = _required_public_config("FIREBASE_WEB_API_KEY")
    # "Username" in many apps is effectively email for Firebase password auth.
    login = (
        _optional_env("FIREBASE_AUTH_EMAIL")
        or _required_env("FIREBASE_AUTH_USERNAME")
    )
    password = _required_env("FIREBASE_AUTH_PASSWORD")

    sign_in_url = (
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?"
        + parse.urlencode({"key": api_key})
    )
    payload = {
        "email": login,
        "password": password,
        "returnSecureToken": True,
    }
    auth = _post_json(sign_in_url, payload, headers={})
    id_token = str(auth.get("idToken") or "").strip()
    uid = str(auth.get("localId") or "").strip()
    if not id_token:
        raise RuntimeError("Firebase sign-in succeeded without idToken in response.")
    if not uid:
        raise RuntimeError("Firebase sign-in succeeded without localId (uid) in response.")
    return id_token, uid


def _build_token_request_body(auth_uid: str) -> tuple[dict[str, Any], str]:
    target_uid = _optional_env("LIVEKIT_TARGET_UID", auth_uid)
    raw_custom = _optional_env("FIREBASE_LIVEKIT_TOKEN_REQUEST_JSON")
    if raw_custom:
        try:
            payload = json.loads(raw_custom)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "FIREBASE_LIVEKIT_TOKEN_REQUEST_JSON must be valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                "FIREBASE_LIVEKIT_TOKEN_REQUEST_JSON must decode to a JSON object."
            )
        return payload, target_uid

    # Firebase callable functions expect the request payload wrapped in "data".
    agent_name = _optional_env("FIREBASE_AGENT_NAME")
    data_payload: dict[str, Any] = {}
    if agent_name:
        data_payload["agentName"] = agent_name

    payload = {"data": data_payload}
    return payload, target_uid


def _extract_livekit_token(response: dict[str, Any]) -> str:
    # Firebase callable response format is usually {"result": {...}}.
    candidate_objects: list[dict[str, Any]] = [response]
    result_obj = response.get("result")
    if isinstance(result_obj, dict):
        candidate_objects.insert(0, result_obj)

    for obj in candidate_objects:
        for key in ("participantToken", "token", "livekitToken", "livekit_token", "jwt"):
            value = str(obj.get(key) or "").strip()
            if value:
                return value
    raise RuntimeError(
        "Token endpoint response does not include a token field. "
        "Expected one of: participantToken, token, livekitToken, livekit_token, jwt."
    )


def main() -> None:
    id_token, auth_uid = _firebase_sign_in()
    token_url = _required_public_config("FIREBASE_LIVEKIT_TOKEN_URL")
    payload, target_uid = _build_token_request_body(auth_uid)
    try:
        response = _post_json(
            token_url,
            payload,
            headers={"Authorization": f"Bearer {id_token}"},
        )
    except RuntimeError as exc:
        payload_preview = json.dumps(payload, ensure_ascii=True)
        raise RuntimeError(
            f"{exc}\n"
            f"Token endpoint request payload was: {payload_preview}\n"
            "If your Firebase function expects a different schema, set "
            "FIREBASE_LIVEKIT_TOKEN_REQUEST_JSON in config/.env to match your client app."
        ) from exc
    livekit_token = _extract_livekit_token(response)
    os.environ["LIVEKIT_TOKEN"] = livekit_token

    print(
        "[run-token-agent-firebase] authenticated via Firebase "
        f"auth_uid={auth_uid!r} target_uid={target_uid!r}"
    )
    print(f"[run-token-agent-firebase] livekit url: {LIVEKIT_URL}")
    print("[run-token-agent-firebase] received LiveKit token; starting token_agent.py")

    token_agent.main()


if __name__ == "__main__":
    main()
