"""Command runner for the shared sandbox container.

A tiny HTTP service the HiveMind API calls to run commands, write files and
start preview servers. Every agent works in its own folder, /workspace/<id>,
and serves previews on its own port. It only listens on the internal sandbox
network and runs as an unprivileged user.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/workspace"
OUTPUT_LIMIT = 200_000
servers: dict[int, subprocess.Popen] = {}
lock = threading.Lock()


def workspace(agent: int) -> str:
    path = os.path.join(ROOT, str(int(agent)))
    os.makedirs(path, exist_ok=True)
    return path


def inside(agent: int, path: str) -> str:
    """Resolve `path` within the agent's workspace, refusing anything that escapes it."""
    base = workspace(agent)
    full = os.path.normpath(os.path.join(base, path or "."))
    if full != base and not full.startswith(base + os.sep):
        raise ValueError("Path must stay inside your workspace.")
    return full


def env_for(agent: int, port: int) -> dict:
    return {**os.environ, "PORT": str(port), "TERM": "dumb", "WORKSPACE": workspace(agent)}


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run(agent: int, command: str, timeout: int, port: int) -> dict:
    proc = subprocess.Popen(
        ["bash", "-c", command], cwd=workspace(agent), env=env_for(agent, port),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return {"code": proc.returncode, "output": out[-OUTPUT_LIMIT:].decode("utf-8", "replace"), "timed_out": False}
    except subprocess.TimeoutExpired:
        kill_group(proc)
        out, _ = proc.communicate()
        return {"code": 124, "output": out[-OUTPUT_LIMIT:].decode("utf-8", "replace"), "timed_out": True}


def write(agent: int, path: str, content: str) -> dict:
    full = inside(agent, path)
    if full == workspace(agent):
        raise ValueError("Give a file name, not just the workspace folder.")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": os.path.relpath(full, workspace(agent))}


def serve(agent: int, directory: str, command: str, port: int) -> dict:
    folder = inside(agent, directory)
    if not os.path.isdir(folder):
        raise ValueError(f"The folder {directory or '.'} does not exist. Create the files first.")
    log_path = os.path.join("/tmp", f"serve-{agent}.log")
    with lock:
        old = servers.pop(agent, None)
        if old:
            kill_group(old)
        subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
        time.sleep(0.3)
        start = command.strip() or f"python3 -m http.server {port} --bind 0.0.0.0"
        with open(log_path, "wb") as log:
            servers[agent] = subprocess.Popen(
                ["bash", "-c", start], cwd=folder, env=env_for(agent, port),
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
            )
    for _ in range(24):
        if listening(port):
            return {"ok": True}
        time.sleep(0.5)
    with open(log_path, "rb") as log:
        tail = log.read()[-800:].decode("utf-8", "replace")
    return {"ok": False, "log": tail}


def reset(agent: int) -> dict:
    with lock:
        old = servers.pop(agent, None)
        if old:
            kill_group(old)
    shutil.rmtree(os.path.join(ROOT, str(int(agent))), ignore_errors=True)
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply(200, {"ok": True}) if self.path == "/health" else self.reply(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            agent, port = int(body["agent"]), int(body.get("port") or 0)
            if self.path == "/run":
                result = run(agent, str(body["command"]), min(int(body.get("timeout") or 120), 300), port)
            elif self.path == "/write":
                result = write(agent, str(body["path"]), str(body["content"]))
            elif self.path == "/serve":
                result = serve(agent, str(body.get("directory") or "."), str(body.get("command") or ""), port)
            elif self.path == "/state":
                result = {"exists": os.path.isdir(os.path.join(ROOT, str(agent))), "serving": bool(port) and listening(port)}
            elif self.path == "/reset":
                result = reset(agent)
            else:
                return self.reply(404, {"error": "not found"})
            self.reply(200, result)
        except ValueError as e:
            self.reply(400, {"error": str(e)})
        except Exception as e:  # keep the runner alive whatever a request does
            self.reply(500, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 7000), Handler).serve_forever()
