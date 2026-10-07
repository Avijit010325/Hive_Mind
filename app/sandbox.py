"""Client for the shared sandbox: the one Linux container agents work in.

The sandbox is a separate, unprivileged container running a small command
runner (sandbox/runner.py). Each agent has its own folder and its own preview
port inside it. Agents are separated by folder, not by a security boundary:
anything one agent's commands can read, another's can too.
"""
import logging
import secrets
import threading
import time

import httpx

from .config import settings
from .db import Sandbox, SessionLocal

log = logging.getLogger("hivemind.sandbox")

OUTPUT_LIMIT = 3500    # characters of shell output handed back to the model
PORT_BASE = 20000      # agent N serves its preview on PORT_BASE + N

_http = httpx.Client(timeout=httpx.Timeout(330, connect=3))
_lock = threading.Lock()
_available: tuple[float, bool] = (0.0, False)


class SandboxError(Exception):
    """Carries a message that is safe to hand back to the model or the user."""


def available() -> bool:
    """Is the sandbox container up? Cached for a while."""
    global _available
    if not settings.tools_enabled:
        return False
    with _lock:
        checked, ok = _available
        if time.time() - checked < (60 if ok else 10):
            return ok
    try:
        ok = _http.get(f"{settings.sandbox_url}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        ok = False
    with _lock:
        _available = (time.time(), ok)
    return ok


def port(agent_id: int) -> int:
    return PORT_BASE + agent_id % 20000


def host() -> str:
    return httpx.URL(settings.sandbox_url).host


def token(agent_id: int) -> str:
    """The secret in an agent's preview links, created on first use."""
    with SessionLocal() as db:
        row = db.get(Sandbox, agent_id)
        if row is None:
            row = Sandbox(agent_id=agent_id, token=secrets.token_urlsafe(18))
            db.add(row)
            db.commit()
        return row.token


def preview_url(agent_id: int) -> str:
    return f"{settings.public_url}/p/{token(agent_id)}/"


def _call(action: str, agent_id: int, **payload) -> dict:
    if not available():
        raise SandboxError("The sandbox computer is not available on this server.")
    try:
        r = _http.post(f"{settings.sandbox_url}/{action}", json={"agent": agent_id, "port": port(agent_id), **payload})
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Sandbox %s failed: %s", action, e)
        raise SandboxError("The sandbox computer did not respond.")
    if r.status_code == 400:
        raise SandboxError(str(data.get("error", "Invalid request.")))
    if r.status_code != 200:
        log.warning("Sandbox %s error: %s", action, data)
        raise SandboxError("The sandbox computer hit an internal error.")
    return data


def _clip(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n… [{len(text) - limit} characters omitted] …\n{text[-half:]}"


def run(agent_id: int, command: str, timeout: int = 120, limit: int = OUTPUT_LIMIT) -> tuple[int, str]:
    """Run a shell command in the agent's workspace. Returns (exit code, output clipped to `limit`)."""
    data = _call("run", agent_id, command=command, timeout=timeout)
    text = data["output"].replace("\x00", "").strip()
    if data.get("timed_out"):
        text += f"\n[stopped after {timeout}s. For servers or anything long-running use the serve tool.]"
    return data["code"], _clip(text.strip(), limit)


def write_file(agent_id: int, path: str, content: str) -> str:
    return _call("write", agent_id, path=path, content=content)["path"]


def serve(agent_id: int, directory: str = ".", command: str = "") -> str:
    """(Re)start the agent's web server and return its public preview link."""
    data = _call("serve", agent_id, directory=directory, command=command)
    if not data.get("ok"):
        raise SandboxError(
            f"Nothing answered after starting. A custom server must listen on 0.0.0.0 and the port in $PORT. "
            f"Log:\n{data.get('log', '')}"
        )
    return preview_url(agent_id)


def state(agent_id: int) -> dict:
    """What the dashboard shows about an agent's computer."""
    info = {"available": available(), "exists": False, "preview": ""}
    if info["available"]:
        try:
            data = _call("state", agent_id)
            info["exists"] = data["exists"]
            info["preview"] = preview_url(agent_id) if data["serving"] else ""
        except SandboxError:
            pass
    return info


def remove(agent_id: int) -> None:
    """Delete an agent's files, stop its server and retire its preview link."""
    try:
        if available():
            _call("reset", agent_id)
    except SandboxError as e:
        log.warning("Could not reset sandbox for agent %s: %s", agent_id, e)
    with SessionLocal() as db:
        row = db.get(Sandbox, agent_id)
        if row:
            db.delete(row)
            db.commit()
