#!/usr/bin/env python3
"""A small coding agent using an OpenAI-compatible chat-completions API."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 约束模型的工作范围，并说明何时结束当前任务。
SYSTEM_PROMPT = """You are a concise coding agent.
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
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
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


# 将模型请求和工具执行所需的可调参数集中保存。
@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    max_steps: int = 8
    model_retries: int = 1
    model_timeout: float = 60.0
    command_timeout: float = 20.0
    output_limit: int = 6000
    search_api_key: str | None = None
    search_model: str = "deepseek-v4-flash"


def clip(text: str, limit: int) -> str:
    # 工具结果过长时截断，避免占满后续上下文。
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated at {limit} characters]"


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
    return path.read_text(encoding="utf-8")


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
    path.write_text(content, encoding="utf-8")
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
    path.write_text(text.replace(old, new, -1 if replace_all else 1), encoding="utf-8")
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
    if not config.search_api_key:
        raise ValueError("web search is unavailable; set DEEPSEEK_API_KEY")

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
            "x-api-key": config.search_api_key,
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


def request_model(messages: list[dict[str, Any]], config: Config) -> dict[str, Any]:
    # 组装 OpenAI 兼容请求，并对临时网络错误进行有限重试。
    tools = TOOLS + ([WEB_SEARCH_TOOL] if config.search_api_key else [])
    payload = {
        "model": config.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    request = urllib.request.Request(
        f"{config.base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(config.model_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=config.model_timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model API returned HTTP {exc.code}: {clip(detail, 1000)}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == config.model_retries:
                raise RuntimeError(f"model request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("model API returned invalid JSON") from exc
        except UnicodeError as exc:
            raise RuntimeError("model API returned invalid UTF-8") from exc
    else:
        raise RuntimeError("model request failed")

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("model API response did not contain a message") from exc
    if not isinstance(message, dict):
        raise RuntimeError("model API message has an invalid format")
    return message


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


def run_agent(task: str, root: Path, config: Config) -> int:
    # 一个任务由多个 step 组成：模型请求工具，工具结果回到历史后继续请求。
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT + f"\nWorkspace: {root}"},
        {"role": "user", "content": task},
    ]
    for step in range(1, config.max_steps + 1):
        print(f"\n[step {step}]")
        # 获取模型的下一步决定；模型出错时结束当前任务。
        try:
            message = request_model(messages, config)
        except RuntimeError as exc:
            print(f"Model error: {exc}")
            return 1

        content = message.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            print("Model error: tool_calls has an invalid format")
            return 1

        # 先保存模型消息，保证下一次请求能看到完整上下文。
        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)

        # 没有工具调用表示模型已经给出最终回答。
        if not tool_calls:
            print(f"Final answer:\n{content or '(empty response)'}")
            return 0

        if content:
            print(f"Model reply:\n{content}")
        # 一个响应可以包含多个工具调用，逐个执行并记录结果。
        for call in tool_calls:
            if not isinstance(call, dict):
                print("Tool error: tool call has an invalid format")
                return 1
            try:
                call_id, name, arguments = parse_tool_call(call)
            except ValueError as exc:
                print(f"Tool error: {exc}")
                return 1
            print(f"Tool call: {name} {json.dumps(arguments, ensure_ascii=False)}")
            try:
                result = execute_tool(name, arguments, root, config)
            except (OSError, UnicodeError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                result = f"Tool error: {exc}"
            result = clip(result, config.output_limit)
            print(f"Tool result:\n{result}")
            # 工具结果必须回到历史，模型才能根据执行结果继续工作。
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

    print(f"Stopped after {config.max_steps} steps.")
    return 1


def parse_args() -> tuple[str, Path, Config]:
    # 命令行参数和环境变量共同组成一次运行配置。
    parser = argparse.ArgumentParser(description="Run a small coding agent.")
    parser.add_argument("task", nargs="?", help="Programming task for the agent.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Workspace directory.")
    parser.add_argument("--base-url", default=os.getenv("AGENT_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL"))
    parser.add_argument("--search-model", default=os.getenv("DEEPSEEK_SEARCH_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--model-retries", type=int, default=1)
    parser.add_argument("--model-timeout", type=float, default=60.0)
    parser.add_argument("--command-timeout", type=float, default=20.0)
    parser.add_argument("--output-limit", type=int, default=6000)
    args = parser.parse_args()

    task = args.task or input("Task: ").strip()
    if not task:
        parser.error("a task is required")
    api_key = os.getenv("AGENT_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        parser.error("set AGENT_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY")
    if not args.model:
        parser.error("set AGENT_MODEL or pass --model")
    if (
        args.max_steps < 1
        or args.model_retries < 0
        or args.model_timeout <= 0
        or args.command_timeout <= 0
        or args.output_limit < 1
    ):
        parser.error("limits must be positive")

    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"workspace directory does not exist: {root}")
    return task, root, Config(
        api_key=api_key,
        base_url=args.base_url,
        model=args.model,
        search_api_key=os.getenv("DEEPSEEK_API_KEY"),
        search_model=args.search_model,
        max_steps=args.max_steps,
        model_retries=args.model_retries,
        model_timeout=args.model_timeout,
        command_timeout=args.command_timeout,
        output_limit=args.output_limit,
    )


def main() -> int:
    task, root, config = parse_args()
    return run_agent(task, root, config)


if __name__ == "__main__":
    raise SystemExit(main())
