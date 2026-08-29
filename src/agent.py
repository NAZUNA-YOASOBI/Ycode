#!/usr/bin/env python3
"""使用 OpenAI 兼容对话接口的简易编程智能体。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# 约束模型的工作范围，并说明何时结束当前任务。
SYSTEM_PROMPT = """You are Ycode, a concise coding agent.
Work only inside the provided workspace. Inspect files before changing them.
Use the available tools for file operations and commands; do not pretend that a change was made.
Use web_search only when current external information would help; it is optional.
When the task is complete, stop calling tools and explain the result briefly.
Do not delete files, terminate processes, or access paths outside the workspace.
"""

# 这些定义会随每次模型请求发送，让模型知道可调用哪些本地能力。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "description": "First line to read, 1-based and inclusive."},
                    "end_line": {"type": "integer", "description": "Last line to read, 1-based and inclusive."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string", "description": "Relative pattern such as **/*.py."}},
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search UTF-8 text files for a literal string inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "description": "File or directory to search, default is ."},
                    "max_results": {"type": "integer", "description": "Maximum matches to return, default is 500."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List the immediate entries in a workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path, default is ."}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write UTF-8 text to a file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_text",
            "description": "Replace text in a UTF-8 file. By default the old text must occur exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence when true."},
                },
                "required": ["path", "old", "new"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command with the workspace as its working directory.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current external information and return cited sources.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

MODEL_CONTEXT_LIMITS = {
    "deepseek-v4-flash": 1_048_576,
    "deepseek-v4-pro": 1_048_576,
}
# 推理等级来自本地模型能力表，不从远端模型列表推断。
DEEPSEEK_REASONING_EFFORTS = {
    "off": None,
    "low": "low",
    "high": "high",
    "max": "max",
}
MODEL_REASONING_EFFORTS = {
    "deepseek-v4-flash": DEEPSEEK_REASONING_EFFORTS,
    "deepseek-v4-pro": DEEPSEEK_REASONING_EFFORTS,
    "deepseek-v4-flash-vision-exp": DEEPSEEK_REASONING_EFFORTS,
}
SESSION_DIR_NAME = "sessions"
DEFAULT_SESSION_ID = "default"
CONTEXT_COMPACT_THRESHOLD = 0.8
CONTEXT_RETAIN_RATIO = 0.16
COMPACTION_MAX_TOKENS = 2048
COMPACTION_TAG = "<compacted-summary>"
COMPACTION_CLOSE_TAG = "</compacted-summary>"


class ContextOverflowError(RuntimeError):
    """模型服务商确认请求超过上下文窗口。"""


# 将模型请求和工具执行所需的可调参数集中保存。
@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    context_limit: int | None = None
    reasoning_effort: str | None = None
    max_steps: int = 8
    model_retries: int = 1
    model_timeout: float = 60.0
    command_timeout: float = 20.0
    output_limit: int = 6000
    search_model: str = "deepseek-v4-flash"


def api_key_from_env() -> str | None:
    return os.getenv("AGENT_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")


def fetch_models(base_url: str, api_key: str, timeout: float) -> list[str]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"model list API returned HTTP {exc.code}: {clip(detail, 1000)}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"model list request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("model list API returned invalid JSON") from exc
    except UnicodeError as exc:
        raise RuntimeError("model list API returned invalid UTF-8") from exc

    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("model list API response did not contain data")
    models: list[str] = []
    for entry in entries:
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(model_id, str) and model_id and model_id not in models:
            models.append(model_id)
    if not models:
        raise RuntimeError("model list API returned no usable models")
    return models


def clip(text: str, limit: int) -> str:
    # 工具结果过长时截断，避免占满后续上下文。
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated at {limit} characters]"


def estimate_tokens(value: Any) -> int:
    """用保守的字符启发式估算 JSON 内容的 token 数。"""
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    ascii_length = sum(character.isascii() for character in serialized)
    return max(1, (ascii_length + 3) // 4 + len(serialized) - ascii_length)


def estimate_request_tokens(messages: list[dict[str, Any]]) -> int:
    """估算消息和固定工具定义在一次请求中占用的 token。"""
    return estimate_tokens(messages) + estimate_tokens(TOOLS + [WEB_SEARCH_TOOL]) + 256


def prefix_has_balanced_tools(messages: list[dict[str, Any]], boundary: int) -> bool:
    """确认压缩边界前的 assistant 工具调用都已有对应结果。"""
    pending: set[str] = set()
    for message in messages[1:boundary]:
        if message.get("role") == "assistant":
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
                    return False
                pending.add(call["id"])
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                return False
            pending.remove(call_id)
    return not pending


def task_block_starts(messages: list[dict[str, Any]]) -> list[int]:
    """返回每个真实用户任务的起始位置，跳过内部摘要和 compact 命令。"""
    starts: list[int] = []
    for index, message in enumerate(messages[1:], start=1):
        content = message.get("content")
        if (
            message.get("role") == "user"
            and isinstance(content, str)
            and content.strip() != "/compact"
            and not content.lstrip().startswith(COMPACTION_TAG)
        ):
            starts.append(index)
    return starts


def align_boundary_to_task_start(task_starts: list[int], boundary: int) -> int:
    """将消息边界向前对齐到完整任务块的开头。"""
    for start in reversed(task_starts):
        if start <= boundary:
            return start
    return task_starts[0]


def select_compaction_boundary(
    messages: list[dict[str, Any]],
    context_limit: int,
    keep_last_task_block: bool = False,
) -> int | None:
    """选择不拆分任务块和工具调用链的压缩边界。"""
    task_starts = task_block_starts(messages)
    if not task_starts:
        return None

    if keep_last_task_block:
        boundary = task_starts[-1]
    else:
        retain_tokens = max(1, int(context_limit * CONTEXT_RETAIN_RATIO))
        retained_tokens = 0
        candidate: int | None = None
        for index in range(len(messages) - 1, 0, -1):
            retained_tokens += estimate_tokens(messages[index])
            if retained_tokens >= retain_tokens:
                candidate = index
                break
        if candidate is None:
            return None
        boundary = align_boundary_to_task_start(task_starts, candidate)

    if boundary <= 1 or boundary >= len(messages):
        return None
    boundaries = [boundary, *reversed([start for start in task_starts if start < boundary])]
    for candidate in boundaries:
        if candidate > 1 and prefix_has_balanced_tools(messages, candidate):
            return candidate
    return None


def format_history_for_summary(messages: list[dict[str, Any]]) -> str:
    """把待压缩消息转换成摘要模型容易读取的纯文本。"""
    sections: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if role == "user":
            sections.append(f"User:\n{content}")
        elif role == "assistant":
            lines = [f"Assistant:\n{content}" if content else "Assistant:"]
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if not isinstance(function, dict):
                        continue
                    name = function.get("name", "")
                    arguments = function.get("arguments", "{}")
                    lines.append(f"Tool request: {name}\n{arguments}")
            sections.append("\n".join(lines))
        elif role == "tool":
            sections.append(f"Tool result ({message.get('tool_call_id', '')}):\n{content}")
    return "\n\n".join(sections)


def summarize_history(messages: list[dict[str, Any]], config: Config) -> str:
    """用一次无工具模型请求生成可继续工作的历史检查点。"""
    instruction = """Summarize the earlier coding-agent history below into a concise Markdown checkpoint.
Keep exact file paths, changes, commands, errors, current state, and the next useful action.
Use these sections: ## Request, ## Files and changes, ## Commands and results, ## Current state, ## Next step.
Do not mention this summarization instruction. Output only the checkpoint Markdown."""
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "You summarize coding-agent history accurately and briefly."},
            {"role": "user", "content": f"{instruction}\n\n{format_history_for_summary(messages)}"},
        ],
        "max_tokens": COMPACTION_MAX_TOKENS,
        "stream": False,
    }
    if config.reasoning_effort == "off":
        payload["thinking"] = {"type": "disabled"}
    elif config.reasoning_effort is not None:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = MODEL_REASONING_EFFORTS[config.model][config.reasoning_effort]
    request = urllib.request.Request(
        f"{config.base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.model_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"context compaction API returned HTTP {exc.code}: {clip(detail, 1000)}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"context compaction request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("context compaction API returned invalid JSON") from exc
    except UnicodeError as exc:
        raise RuntimeError("context compaction API returned invalid UTF-8") from exc

    choices = data.get("choices") if isinstance(data, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("context compaction API returned an empty summary")
    return content.strip()


def is_context_overflow(detail: str) -> bool:
    """识别服务商返回的上下文窗口超限错误。"""
    text = detail.lower()
    return any(marker in text for marker in (
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "prompt is too long",
        "too many tokens",
    ))


def compact_history(
    messages: list[dict[str, Any]],
    config: Config,
    prompt_tokens: int | None,
    force: bool = False,
    keep_last_task_block: bool = False,
) -> tuple[str, int, int] | None:
    """总结旧历史并原地替换，成功后才修改消息列表。"""
    if config.context_limit is None:
        return None
    current_tokens = max(prompt_tokens or 0, estimate_request_tokens(messages))
    threshold = max(1, int(config.context_limit * CONTEXT_COMPACT_THRESHOLD))
    if not force and current_tokens < threshold:
        return None
    boundary = select_compaction_boundary(
        messages,
        config.context_limit,
        keep_last_task_block=keep_last_task_block,
    )
    if boundary is None:
        return None
    summary = summarize_history(messages[1:boundary], config)
    summary_message = {
        "role": "user",
        "content": f"{COMPACTION_TAG}\n{summary}\n{COMPACTION_CLOSE_TAG}",
    }
    compacted = [messages[0], summary_message, *messages[boundary:]]
    before_tokens = estimate_request_tokens(messages)
    after_tokens = estimate_request_tokens(compacted)
    if after_tokens >= before_tokens:
        return None
    messages[:] = compacted
    return summary, before_tokens, after_tokens


def session_path(root: Path, session_id: str) -> Path:
    if (
        not isinstance(session_id, str)
        or not session_id
        or len(session_id) > 128
        or not all(character.isalnum() or character in "-_" for character in session_id)
    ):
        raise ValueError("invalid session id")
    return root / SESSION_DIR_NAME / f"{session_id}.json"


def load_session_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to read session file: {path.name}") from exc
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not messages or not all(isinstance(item, dict) for item in messages):
        raise RuntimeError("session file has invalid message history")
    if messages[0].get("role") != "system":
        raise RuntimeError("session file has no system message")
    context_messages = data.get("context_messages") if isinstance(data, dict) else None
    if context_messages is None:
        context_messages = [dict(message) for message in messages]
    if (
        not isinstance(context_messages, list)
        or not context_messages
        or not all(isinstance(item, dict) for item in context_messages)
    ):
        raise RuntimeError("session file has invalid context history")
    if context_messages[0].get("role") != "system":
        raise RuntimeError("session context has no system message")
    for context_message in context_messages:
        content = context_message.get("content")
        if (
            context_message.get("role") == "user"
            and isinstance(content, str)
            and content.startswith(COMPACTION_TAG)
            and not any(message == context_message for message in messages)
        ):
            messages.append(dict(context_message))
    prompt_tokens = data.get("prompt_tokens") if isinstance(data, dict) else None
    if not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool) or prompt_tokens < 0:
        prompt_tokens = None
    record = dict(data)
    record["id"] = path.stem
    record["title"] = record.get("title") if isinstance(record.get("title"), str) else "New session"
    record["created_at"] = record.get("created_at") if isinstance(record.get("created_at"), str) else ""
    record["updated_at"] = record.get("updated_at") if isinstance(record.get("updated_at"), str) else ""
    record["messages"] = messages
    record["context_messages"] = context_messages
    record["prompt_tokens"] = prompt_tokens
    return record


def load_session(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    record = load_session_record(path)
    if record is None:
        return [], [], None
    return record["messages"], record["context_messages"], record["prompt_tokens"]


def session_title(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        title = " ".join(message["content"].split())
        if title:
            return title if len(title) <= 72 else title[:69] + "..."
    return "New session"


def save_session(
    path: Path,
    messages: list[dict[str, Any]],
    context_messages: list[dict[str, Any]],
    prompt_tokens: int | None,
) -> None:
    existing = load_session_record(path) if path.is_file() else None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = {
        "version": 1,
        "id": existing["id"] if existing else path.stem,
        "title": session_title(messages),
        "created_at": existing["created_at"] if existing and existing["created_at"] else now,
        "updated_at": now,
        "messages": messages,
        "context_messages": context_messages,
        "prompt_tokens": prompt_tokens,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def create_session(root: Path, session_id: str) -> dict[str, Any]:
    path = session_path(root, session_id)
    if path.exists():
        raise FileExistsError("session already exists")
    messages = [{"role": "system", "content": SYSTEM_PROMPT + f"\nWorkspace: {root}"}]
    save_session(path, messages, messages, None)
    record = load_session_record(path)
    if record is None:
        raise RuntimeError("unable to create session")
    return record


def workspace_path(root: Path, value: Any) -> Path:
    # 解析真实路径后确认它仍在工作目录内，防止相对路径越界。
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty string")
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("path must stay inside the workspace") from exc
    return path


def read_file(arguments: dict[str, Any], root: Path) -> str:
    # 文件工具都通过 workspace_path 检查访问范围。
    path = workspace_path(root, arguments.get("path"))
    if not path.is_file():
        raise ValueError(f"not a file: {path.relative_to(root)}")
    start_line = arguments.get("start_line", 1)
    end_line = arguments.get("end_line")
    if not isinstance(start_line, int) or isinstance(start_line, bool) or start_line < 1:
        raise ValueError("start_line must be a positive integer")
    if end_line is not None and (
        not isinstance(end_line, int)
        or isinstance(end_line, bool)
        or end_line < start_line
    ):
        raise ValueError("end_line must be an integer at least start_line")
    text = path.read_text(encoding="utf-8")
    if start_line == 1 and end_line is None:
        return text
    lines = text.splitlines(keepends=True)
    return "".join(lines[start_line - 1:end_line])


def glob_files(arguments: dict[str, Any], root: Path) -> str:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("pattern must be a non-empty string")
    pattern_path = Path(pattern)
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        raise ValueError("pattern must stay inside the workspace")
    matches: set[str] = set()
    for path in root.glob(pattern):
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        if resolved.is_file():
            matches.add(relative.as_posix())
    return "\n".join(sorted(matches)) or "(no matches)"


def grep_files(arguments: dict[str, Any], root: Path) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    path = workspace_path(root, arguments.get("path", "."))
    max_results = arguments.get("max_results", 500)
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
        raise ValueError("max_results must be a positive integer")
    if path.is_file():
        candidates = [path]
    elif path.is_dir():
        candidates = sorted(path.rglob("*"))
    else:
        raise ValueError(f"not a file or directory: {path.relative_to(root)}")
    matches: list[str] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(root)
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if query in line:
                matches.append(f"{relative.as_posix()}:{line_number}:{line}")
                if len(matches) >= max_results:
                    return "\n".join(matches)
    return "\n".join(matches) or "(no matches)"


def list_files(arguments: dict[str, Any], root: Path) -> str:
    path = workspace_path(root, arguments.get("path", "."))
    if not path.is_dir():
        raise ValueError(f"not a directory: {path.relative_to(root)}")
    entries = sorted(path.iterdir(), key=lambda entry: entry.name)
    return "\n".join(f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries) or "(empty)"


def write_file(arguments: dict[str, Any], root: Path) -> str:
    path = workspace_path(root, arguments.get("path"))
    content = arguments.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return f"Wrote {len(content.encode('utf-8'))} bytes to {path.relative_to(root)}."


def replace_text(arguments: dict[str, Any], root: Path) -> str:
    path = workspace_path(root, arguments.get("path"))
    old = arguments.get("old")
    new = arguments.get("new")
    replace_all = arguments.get("replace_all", False)
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("old and new must be strings")
    if not isinstance(replace_all, bool):
        raise ValueError("replace_all must be a boolean")
    if not old:
        raise ValueError("old must not be empty")
    if not path.is_file():
        raise ValueError(f"not a file: {path.relative_to(root)}")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ValueError("old text was not found")
    if count > 1 and not replace_all:
        raise ValueError(f"old text occurs {count} times; set replace_all to true")
    path.write_bytes(text.replace(old, new, -1 if replace_all else 1).encode("utf-8"))
    return f"Replaced {count} occurrence(s) in {path.relative_to(root)}."


def run_command(arguments: dict[str, Any], root: Path, timeout: float) -> str:
    # 在工作目录中执行模型给出的命令，并设置最长运行时间。
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    try:
        result = subprocess.run(
            command,
            cwd=root,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout:g} seconds."
    return (
        f"exit_code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def web_search(arguments: dict[str, Any], config: Config) -> str:
    # 通过 DeepSeek 的 Anthropic 兼容接口请求服务端原生网页搜索。
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    payload = {
        "model": config.search_model,
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": f"Perform a web search for the query: {query}"}],
        }],
        "tools": [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }],
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/anthropic/v1/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # 发送请求并解析服务端返回的 JSON。
    try:
        with urllib.request.urlopen(request, timeout=config.model_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"web search API returned HTTP {exc.code}: {clip(detail, 1000)}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"web search request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("web search API returned invalid JSON") from exc

    blocks = data.get("content") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        raise RuntimeError("web search response did not contain content blocks")

    # 先收集引用，再读取来源，兼容服务端返回块顺序变化。
    snippets: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        for citation in block.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            url = citation.get("url")
            cited_text = citation.get("cited_text")
            if isinstance(url, str) and url and isinstance(cited_text, str) and cited_text:
                snippets.setdefault(url, cited_text)

    sources: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    result_block_found = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "web_search_tool_result":
            continue
        result_block_found = True
        for item in block.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            title = item.get("title") if isinstance(item.get("title"), str) else ""
            sources.append((title, url, snippets.get(url, "")))

    if not result_block_found:
        raise RuntimeError("web search response contained no search result block")
    if not sources:
        return "(no web search sources)"
    lines = []
    for title, url, snippet in sources:
        line = f"- {title + ': ' if title else ''}{url}"
        if snippet:
            line += f"\n  {snippet}"
        lines.append(line)
    return "\n".join(lines)


# 文件工具按名称注册，命令和网页搜索因需要额外参数而单独分派。
TOOL_HANDLERS = {
    "read_file": read_file,
    "glob": glob_files,
    "grep": grep_files,
    "list_files": list_files,
    "write_file": write_file,
    "replace_text": replace_text,
}


def execute_tool(name: str, arguments: dict[str, Any], root: Path, config: Config) -> str:
    if name == "run_command":
        return run_command(arguments, root, config.command_timeout)
    if name == "web_search":
        return web_search(arguments, config)
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"unknown tool: {name}")
    return handler(arguments, root)


def iter_stream_data(response: Any) -> Iterator[str]:
    """读取 OpenAI 兼容接口返回的 SSE data 事件。"""
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not data_lines and not line.startswith(("event:", "id:", "retry:", ":")):
            data_lines.append(line)
    if data_lines:
        yield "\n".join(data_lines)


def merge_tool_call_delta(
    tool_calls: dict[int, dict[str, Any]],
    delta: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> None:
    index = delta.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        return
    call = tool_calls.setdefault(index, {
        "id": "",
        "type": "function",
        "function": {"name": "", "arguments": ""},
    })
    event: dict[str, Any] = {"type": "tool_call_delta", "index": index}
    call_id = delta.get("id")
    if isinstance(call_id, str) and call_id:
        call["id"] = call_id
        event["id"] = call_id
    function = delta.get("function")
    if not isinstance(function, dict):
        return
    name = function.get("name")
    if isinstance(name, str) and name:
        call["function"]["name"] += name
        event["name"] = name
    arguments = function.get("arguments")
    if isinstance(arguments, str) and arguments:
        call["function"]["arguments"] += arguments
        event["arguments"] = arguments
    if len(event) > 2:
        emit(event)


def consume_model_stream(
    response: Any,
    emit: Callable[[dict[str, Any]], None],
    stream_status: dict[str, bool] | None = None,
) -> tuple[dict[str, Any], int | None]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    prompt_tokens: int | None = None
    for raw_event in iter_stream_data(response):
        if raw_event == "[DONE]":
            break
        try:
            data = json.loads(raw_event)
        except json.JSONDecodeError as exc:
            raise RuntimeError("model stream returned invalid JSON") from exc
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
        candidate_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        if isinstance(candidate_tokens, int) and not isinstance(candidate_tokens, bool) and candidate_tokens >= 0:
            prompt_tokens = candidate_tokens
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            if stream_status is not None:
                stream_status["started"] = True
            reasoning_parts.append(reasoning)
            emit({"type": "reasoning_delta", "content": reasoning})
        content = delta.get("content")
        if isinstance(content, str) and content:
            if stream_status is not None:
                stream_status["started"] = True
            content_parts.append(content)
            emit({"type": "model_delta", "content": content})
        deltas = delta.get("tool_calls")
        if isinstance(deltas, list):
            if stream_status is not None and any(isinstance(item, dict) for item in deltas):
                stream_status["started"] = True
            for tool_delta in deltas:
                if isinstance(tool_delta, dict):
                    merge_tool_call_delta(tool_calls, tool_delta, emit)

    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    reasoning_content = "".join(reasoning_parts)
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    return message, prompt_tokens


def request_model(
    messages: list[dict[str, Any]],
    config: Config,
    emit: Callable[[dict[str, Any]], None],
) -> tuple[dict[str, Any], int | None]:
    # 组装 OpenAI 兼容请求，并按 SSE 增量读取模型响应。
    payload = {
        "model": config.model,
        "messages": messages,
        "tools": TOOLS + [WEB_SEARCH_TOOL],
        "tool_choice": "auto",
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    # 默认模式不发送推理字段，其余等级按 DeepSeek 协议转换。
    if config.reasoning_effort == "off":
        payload["thinking"] = {"type": "disabled"}
    elif config.reasoning_effort is not None:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = MODEL_REASONING_EFFORTS[config.model][config.reasoning_effort]
    for attempt in range(config.model_retries + 1):
        stream_status = {"started": False}
        request = urllib.request.Request(
            f"{config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=config.model_timeout) as response:
                return consume_model_stream(response, emit, stream_status)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if is_context_overflow(detail):
                raise ContextOverflowError(
                    f"model context window exceeded: {clip(detail, 1000)}",
                ) from exc
            raise RuntimeError(f"model API returned HTTP {exc.code}: {clip(detail, 1000)}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if stream_status["started"] or attempt == config.model_retries:
                raise RuntimeError(f"model request failed: {exc}") from exc
        except UnicodeError as exc:
            raise RuntimeError("model stream returned invalid UTF-8") from exc
    raise RuntimeError("model request failed")


def parse_tool_call(call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    # 将模型的结构化工具调用解析为本地函数需要的参数。
    call_id = call.get("id")
    function = call.get("function")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("tool call has no id")
    if not isinstance(function, dict):
        raise ValueError("tool call has no function")
    name = function.get("name")
    raw_arguments = function.get("arguments", "{}")
    if not isinstance(name, str) or not name:
        raise ValueError("tool call has no name")
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("tool arguments are not valid JSON") from exc
    else:
        arguments = raw_arguments
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    return call_id, name, arguments


def print_event(event: dict[str, Any]) -> None:
    event_type = event["type"]
    if event_type == "step":
        print(f"\n[step {event['step']}]")
    elif event_type in {"reasoning_delta", "model_delta"}:
        print(event["content"], end="", flush=True)
    elif event_type == "context":
        tokens = event["tokens"]
        limit = event["limit"]
        if limit is None:
            print("Context left: unknown")
        elif tokens is None:
            print("Context left: not reported")
        else:
            print(f"Context left: {max(limit - tokens, 0):,} tokens")
    elif event_type == "compaction":
        print(
            f"\nContext compacted: {event['before']:,} -> {event['after']:,} estimated tokens.",
        )
    elif event_type == "tool_call_delta":
        if event.get("name"):
            print(f"\nTool call: {event['name']} ", end="", flush=True)
        if event.get("arguments"):
            print(event["arguments"], end="", flush=True)
    elif event_type == "tool_result":
        print(f"Tool result:\n{event['content']}")
    elif event_type == "final":
        if event.get("streamed"):
            print()
        else:
            print(f"Final answer:\n{event['content'] or '(empty response)'}")
    elif event_type == "error":
        print(f"{event['source'].title()} error: {event['message']}")
    elif event_type == "stopped":
        print(f"Stopped after {event['max_steps']} steps.")


def emit_compaction(
    emit: Callable[[dict[str, Any]], None],
    compacted: tuple[str, int, int],
) -> None:
    """显示一次上下文压缩事件。"""
    summary, before_tokens, after_tokens = compacted
    emit({
        "type": "compaction",
        "content": summary,
        "before": before_tokens,
        "after": after_tokens,
    })


def append_compaction_marker(history_messages: list[dict[str, Any]], summary: str) -> None:
    """在完整历史中记录供 Web 恢复显示的压缩摘要。"""
    history_messages.append({
        "role": "user",
        "content": f"{COMPACTION_TAG}\n{summary}\n{COMPACTION_CLOSE_TAG}",
    })


def run_agent(
    task: str,
    root: Path,
    config: Config,
    emit: Callable[[dict[str, Any]], None] = print_event,
    session_file: Path | None = None,
) -> int:
    # 一个任务由多个 step 组成：模型请求工具，工具结果回到历史后继续请求。
    if session_file is None:
        history_messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT + f"\nWorkspace: {root}"},
        ]
        messages = [dict(history_messages[0])]
        prompt_tokens = None
    else:
        history_messages, messages, prompt_tokens = load_session(session_file)
        if not history_messages:
            history_messages = [
                {"role": "system", "content": SYSTEM_PROMPT + f"\nWorkspace: {root}"},
            ]
        if not messages:
            messages = [dict(history_messages[0])]
    if task == "/compact":
        try:
            compacted = compact_history(
                messages,
                config,
                prompt_tokens,
                force=True,
                keep_last_task_block=True,
            )
        except RuntimeError as exc:
            emit({"type": "error", "source": "context", "message": str(exc)})
            return 1
        if compacted is None:
            emit({"type": "error", "source": "context", "message": "no earlier history can be compacted"})
            return 1
        emit_compaction(emit, compacted)
        append_compaction_marker(history_messages, compacted[0])
        if session_file is not None:
            save_session(session_file, history_messages, messages, None)
        return 0
    user_message = {"role": "user", "content": task}
    history_messages.append(user_message)
    messages.append(dict(user_message))
    if session_file is not None:
        save_session(session_file, history_messages, messages, prompt_tokens)
    for step in range(1, config.max_steps + 1):
        try:
            compacted = compact_history(messages, config, prompt_tokens)
        except RuntimeError as exc:
            emit({"type": "error", "source": "context", "message": str(exc)})
            return 1
        if compacted is not None:
            emit_compaction(emit, compacted)
            append_compaction_marker(history_messages, compacted[0])
            prompt_tokens = None
            if session_file is not None:
                save_session(session_file, history_messages, messages, prompt_tokens)
        emit({"type": "step", "step": step})
        # 获取模型的下一步决定；模型出错时结束当前任务。
        try:
            message, prompt_tokens = request_model(messages, config, emit)
        except ContextOverflowError as exc:
            try:
                compacted = compact_history(messages, config, prompt_tokens, force=True)
            except RuntimeError as compact_exc:
                emit({"type": "error", "source": "context", "message": str(compact_exc)})
                return 1
            if compacted is None:
                emit({"type": "error", "source": "model", "message": str(exc)})
                return 1
            emit_compaction(emit, compacted)
            append_compaction_marker(history_messages, compacted[0])
            prompt_tokens = None
            if session_file is not None:
                save_session(session_file, history_messages, messages, prompt_tokens)
            try:
                message, prompt_tokens = request_model(messages, config, emit)
            except RuntimeError as retry_exc:
                emit({"type": "error", "source": "model", "message": str(retry_exc)})
                return 1
        except RuntimeError as exc:
            emit({"type": "error", "source": "model", "message": str(exc)})
            return 1
        emit({"type": "context", "tokens": prompt_tokens, "limit": config.context_limit})

        content = message.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            emit({"type": "error", "source": "model", "message": "tool_calls has an invalid format"})
            return 1

        # 先保存模型消息，保证下一次请求能看到完整上下文。
        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str):
            assistant_message["reasoning_content"] = reasoning_content
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        history_messages.append(dict(assistant_message))
        messages.append(assistant_message)

        # 没有工具调用表示模型已经给出最终回答。
        if not tool_calls:
            if session_file is not None:
                save_session(session_file, history_messages, messages, prompt_tokens)
            emit({"type": "final", "content": content, "streamed": bool(content)})
            return 0

        # 一个响应可以包含多个工具调用，逐个执行并记录结果。
        for index, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                emit({"type": "error", "source": "tool", "message": "tool call has an invalid format"})
                return 1
            try:
                call_id, name, arguments = parse_tool_call(call)
            except ValueError as exc:
                emit({"type": "error", "source": "tool", "message": str(exc)})
                return 1
            emit({
                "type": "tool_call_complete",
                "index": index,
                "name": name,
                "arguments": arguments,
            })
            try:
                result = execute_tool(name, arguments, root, config)
            except (OSError, UnicodeError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                result = f"Tool error: {exc}"
            result = clip(result, config.output_limit)
            emit({"type": "tool_result", "index": index, "name": name, "content": result})
            # 工具结果必须回到历史，模型才能根据执行结果继续工作。
            tool_message = {"role": "tool", "tool_call_id": call_id, "content": result}
            history_messages.append(dict(tool_message))
            messages.append(tool_message)
        if session_file is not None:
            save_session(session_file, history_messages, messages, prompt_tokens)

    emit({"type": "stopped", "max_steps": config.max_steps})
    return 1


def build_config(
    base_url: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    search_model: str | None = None,
    context_limit: int | None = None,
    max_steps: int = 8,
    model_retries: int = 1,
    model_timeout: float = 60.0,
    command_timeout: float = 20.0,
    output_limit: int = 6000,
) -> Config:
    # 终端和 Web 服务共用同一套环境变量与默认值。
    api_key = api_key_from_env()
    if not api_key:
        raise ValueError("set AGENT_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY")
    resolved_base_url = base_url or os.getenv("AGENT_BASE_URL", "https://api.deepseek.com/v1")
    resolved_model = model or os.getenv("AGENT_MODEL")
    if not resolved_model:
        try:
            resolved_model = fetch_models(resolved_base_url, api_key, model_timeout)[0]
        except RuntimeError as exc:
            raise ValueError(f"unable to choose a model: {exc}") from exc
    if reasoning_effort is not None and reasoning_effort not in MODEL_REASONING_EFFORTS.get(resolved_model, {}):
        raise ValueError(f'model "{resolved_model}" does not support reasoning effort "{reasoning_effort}"')
    if context_limit is None and os.getenv("AGENT_CONTEXT_LIMIT"):
        try:
            context_limit = int(os.environ["AGENT_CONTEXT_LIMIT"])
        except ValueError as exc:
            raise ValueError("AGENT_CONTEXT_LIMIT must be a positive integer") from exc
    if context_limit is None:
        context_limit = MODEL_CONTEXT_LIMITS.get(resolved_model)
    if context_limit is not None and context_limit < 1:
        raise ValueError("context limit must be positive")
    if max_steps < 1 or model_retries < 0 or model_timeout <= 0 or command_timeout <= 0 or output_limit < 1:
        raise ValueError("limits must be positive")
    return Config(
        api_key=api_key,
        base_url=resolved_base_url,
        model=resolved_model,
        context_limit=context_limit,
        reasoning_effort=reasoning_effort,
        search_model=search_model or os.getenv("DEEPSEEK_SEARCH_MODEL", "deepseek-v4-flash"),
        max_steps=max_steps,
        model_retries=model_retries,
        model_timeout=model_timeout,
        command_timeout=command_timeout,
        output_limit=output_limit,
    )


def parse_args() -> tuple[str, Path, Config]:
    # 命令行参数和环境变量共同组成一次运行配置。
    parser = argparse.ArgumentParser(description="Run Ycode.")
    parser.add_argument("task", nargs="?", help="Programming task for the agent.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Workspace directory.")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort", choices=DEEPSEEK_REASONING_EFFORTS)
    parser.add_argument("--search-model")
    parser.add_argument("--context-limit", type=int)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--model-retries", type=int, default=1)
    parser.add_argument("--model-timeout", type=float, default=60.0)
    parser.add_argument("--command-timeout", type=float, default=20.0)
    parser.add_argument("--output-limit", type=int, default=6000)
    args = parser.parse_args()

    task = args.task or input("Task: ").strip()
    if not task:
        parser.error("a task is required")

    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"workspace directory does not exist: {root}")
    try:
        config = build_config(
            base_url=args.base_url,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            search_model=args.search_model,
            context_limit=args.context_limit,
            max_steps=args.max_steps,
            model_retries=args.model_retries,
            model_timeout=args.model_timeout,
            command_timeout=args.command_timeout,
            output_limit=args.output_limit,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return task, root, config


def main() -> int:
    task, root, config = parse_args()
    return run_agent(task, root, config, session_file=session_path(root, DEFAULT_SESSION_ID))


if __name__ == "__main__":
    raise SystemExit(main())
