#!/usr/bin/env python3
"""为编程智能体提供本地 Web 界面。"""

from __future__ import annotations

import argparse
import json
import os
import threading
import uuid
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent import (
    MODEL_CONTEXT_LIMITS,
    MODEL_REASONING_EFFORTS,
    SESSION_DIR_NAME,
    api_key_from_env,
    build_config,
    create_session,
    fetch_models,
    load_session_record,
    run_agent,
    session_path,
)


PAGE_PATH = Path(__file__).resolve().parent.parent / "web" / "index.html"
MAX_REQUEST_BYTES = 64 * 1024
MODEL_LIST_TIMEOUT = 20.0


def write_event(stream: Any, event: dict[str, Any]) -> None:
    line = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
    stream.write(line)
    stream.flush()


def get_model_options() -> tuple[list[str], str | None, str | None]:
    configured_model = os.getenv("AGENT_MODEL")
    api_key = api_key_from_env()
    if not api_key:
        return [], configured_model, "API key is not configured"
    base_url = os.getenv("AGENT_BASE_URL", "https://api.deepseek.com/v1")
    try:
        models = fetch_models(base_url, api_key, MODEL_LIST_TIMEOUT)
    except RuntimeError as exc:
        return [], None, str(exc)
    selected_model = configured_model if configured_model in models else models[0]
    return models, selected_model, None


def session_public(record: dict[str, Any], include_messages: bool = True) -> dict[str, Any]:
    messages = record.get("messages") or []
    data = {
        "id": record["id"],
        "title": record["title"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "prompt_tokens": record["prompt_tokens"],
    }
    if include_messages:
        data["messages"] = messages[1:]
    return data


def list_sessions(root: Path) -> list[dict[str, Any]]:
    directory = root / SESSION_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    sessions: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            record = load_session_record(path)
        except RuntimeError as exc:
            sessions.append({
                "id": path.stem,
                "title": "Unreadable session",
                "created_at": "",
                "updated_at": "",
                "prompt_tokens": None,
                "error": str(exc),
            })
            continue
        if record is not None:
            sessions.append(session_public(record, include_messages=False))
    sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return sessions


def ensure_current_session(root: Path) -> dict[str, Any]:
    sessions = list_sessions(root)
    for summary in sessions:
        if "error" not in summary:
            record = load_session_record(session_path(root, summary["id"]))
            if record is not None:
                return record
    session_id = uuid.uuid4().hex
    return create_session(root, session_id)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 1 or length > MAX_REQUEST_BYTES:
        raise ValueError("request body has an invalid size")
    data = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


class AgentWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: Path):
        super().__init__(address, AgentWebHandler)
        self.root = root
        self.run_lock = threading.Lock()


class AgentWebHandler(BaseHTTPRequestHandler):
    server: AgentWebServer

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            body = PAGE_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/config":
            models, selected_model, model_error = get_model_options()
            session_error: str | None = None
            try:
                current_session = ensure_current_session(self.server.root)
                sessions = list_sessions(self.server.root)
            except (OSError, RuntimeError, ValueError) as exc:
                current_session = None
                sessions = []
                session_error = str(exc)
            self.send_json(200, {
                "model": selected_model or "Not configured",
                "models": models,
                "model_error": model_error,
                "session": session_public(current_session) if current_session else None,
                "sessions": sessions,
                "session_error": session_error,
                "reasoning_effort": None,
                "reasoning_efforts": {
                    model: list(MODEL_REASONING_EFFORTS[model])
                    for model in models
                    if model in MODEL_REASONING_EFFORTS
                },
                "context_limit": os.getenv("AGENT_CONTEXT_LIMIT"),
                "context_limits": {
                    model: MODEL_CONTEXT_LIMITS[model]
                    for model in models
                    if model in MODEL_CONTEXT_LIMITS
                },
                "workspace": str(self.server.root),
                "api_configured": bool(api_key_from_env()),
            })
            return
        if path == "/api/sessions":
            try:
                self.send_json(200, {"sessions": list_sessions(self.server.root)})
            except OSError as exc:
                self.send_json(500, {"error": f"unable to list sessions: {exc}"})
            return
        if path.startswith("/api/sessions/"):
            session_id = path[len("/api/sessions/"):]
            try:
                record = load_session_record(session_path(self.server.root, session_id))
            except (RuntimeError, ValueError) as exc:
                self.send_json(400, {"error": str(exc)})
                return
            if record is None:
                self.send_json(404, {"error": "session not found"})
                return
            self.send_json(200, {"session": session_public(record)})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/sessions":
            if not self.server.run_lock.acquire(blocking=False):
                self.send_json(409, {"error": "another task is already running"})
                return
            try:
                session_id = uuid.uuid4().hex
                try:
                    record = create_session(self.server.root, session_id)
                except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
                    self.send_json(500, {"error": f"unable to create session: {exc}"})
                    return
                self.send_json(201, {"session": session_public(record)})
            finally:
                self.server.run_lock.release()
            return
        if path != "/api/run":
            self.send_json(404, {"error": "not found"})
            return
        if not self.server.run_lock.acquire(blocking=False):
            self.send_json(409, {"error": "another task is already running"})
            return

        try:
            try:
                data = read_json_body(self)
                task = data.get("task")
                if not isinstance(task, str) or not task.strip():
                    raise ValueError("task must be a non-empty string")
                session_id = data.get("session_id")
                if not isinstance(session_id, str) or not session_id.strip():
                    raise ValueError("session_id must be a non-empty string")
                session_file = session_path(self.server.root, session_id.strip())
                if load_session_record(session_file) is None:
                    raise ValueError("session not found")
                selected_model = data.get("model")
                if selected_model is not None:
                    if not isinstance(selected_model, str) or not selected_model.strip():
                        raise ValueError("model must be a non-empty string")
                    selected_model = selected_model.strip()
                reasoning_effort = data.get("reasoning_effort")
                if reasoning_effort is not None:
                    if not isinstance(reasoning_effort, str) or not reasoning_effort:
                        raise ValueError("reasoning_effort must be a non-empty string or null")
                config = build_config(model=selected_model, reasoning_effort=reasoning_effort)
            except (UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
                self.send_json(400, {"error": str(exc)})
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

            try:
                # 每个事件单独写成一行，浏览器可以边接收边更新界面。
                emit = partial(write_event, self.wfile)
                code = run_agent(
                    task.strip(),
                    self.server.root,
                    config,
                    emit,
                    session_file=session_file,
                )
                emit({"type": "done", "code": code})
            except (BrokenPipeError, ConnectionResetError):
                return
        finally:
            self.server.run_lock.release()

    def log_message(self, message: str, *args: Any) -> None:
        print(f"[web] {message % args}")


def parse_args() -> tuple[Path, int]:
    parser = argparse.ArgumentParser(description="Run the Ycode web interface.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Workspace directory.")
    parser.add_argument("--port", type=int, default=8000, help="Local server port.")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"workspace directory does not exist: {root}")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not PAGE_PATH.is_file():
        parser.error(f"web page does not exist: {PAGE_PATH}")
    return root, args.port


def main() -> int:
    root, port = parse_args()
    server = AgentWebServer(("127.0.0.1", port), root)
    print(f"Ycode: http://127.0.0.1:{server.server_port}")
    print(f"Workspace: {root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
