# Ycode

Ycode 是一个用 Python 编写的最小 coding agent。它通过 OpenAI 兼容接口与大语言模型交互，由本地程序执行文件操作和命令，并把执行结果继续交给模型，直到任务完成。

## 功能

- `glob`、`grep`、`read_file`、`list_files`、`write_file`、`replace_text`：受工作目录限制的本地文件工具。
- `run_command`：在工作目录中执行命令，并限制运行时间。
- `web_search`：调用 DeepSeek 服务端原生网页搜索。
- Agent 循环：解析模型回复、执行工具、保存结果并继续请求模型。
- 流式响应：按 SSE 增量显示推理、正文和工具调用参数。
- 模型选择：从兼容接口的 `/models` 列表读取可用模型。
- 推理等级：已知 DeepSeek V4 模型可选择 `Off`、`Low`、`High`、`Max`，默认由提供商决定。
- 思考过程：显示模型返回的 `reasoning_content`，支持 Markdown 渲染，并与工具调用按实际顺序归入可折叠的工作过程。
- 上下文显示：根据模型上限和 `prompt_tokens` 显示当前占用情况。
- 上下文压缩：达到压力阈值时摘要较早历史，保留近期工具链；服务端超限后压缩并重试一次。
- 主动压缩：输入 `/compact` 可直接压缩当前会话历史，不调用模型。
- Web 工作台：实时显示运行过程，并支持历史会话切换。
- 会话持久化：每个会话保存在 `sessions/` 下的独立 JSON 文件中。

## 运行环境

- Python 3.10 或更高版本
- 无第三方 Python 依赖
- 一个兼容接口的 API key

## 快速开始

PowerShell：

```powershell
$env:AGENT_API_KEY = "your-api-key"
$env:AGENT_BASE_URL = "https://api.deepseek.com/v1"
```

运行终端 Agent：

```powershell
python src/agent.py "创建一个简单的计算器并运行测试"
```

运行 Web 界面：

```powershell
python src/web.py
```

然后打开 <http://127.0.0.1:8000/>。

## 配置

| 环境变量 | 作用 |
| --- | --- |
| `AGENT_API_KEY` | 主 Agent 使用的 API key |
| `AGENT_BASE_URL` | OpenAI 兼容接口地址，默认是 `https://api.deepseek.com/v1` |
| `AGENT_MODEL` | 默认主模型；不设置时使用远端模型列表中的第一个模型 |
| `AGENT_CONTEXT_LIMIT` | 覆盖上下文上限，单位为 token |
| `DEEPSEEK_SEARCH_MODEL` | 网页搜索模型，默认是 `deepseek-v4-flash` |

`AGENT_API_KEY`、`OPENAI_API_KEY` 和 `DEEPSEEK_API_KEY` 都可以作为 API key 来源。凭据只在服务端环境变量中读取，不会发送到浏览器。

终端可通过 `--reasoning-effort` 选择 `off`、`low`、`high` 或 `max`；不传该参数时不发送推理等级。

## 项目结构

```text
src/agent.py                 Agent 核心循环、工具和模型请求
src/web.py                   本地 Web 服务和会话 API
web/index.html               单文件 Web 界面
sessions/                    本地会话记录
test/                        演示和测试用文件
plan/                        参考资料与设计记录
requirement/                 题目要求
```

## 工作流程

```text
用户任务
  -> 模型 SSE 流式请求
  -> 解析普通回复或工具调用
  -> 在本地工作目录执行工具
  -> 保存工具结果到会话历史
  -> 再次请求模型
```

模型只负责决定下一步操作；文件读写、命令执行、会话管理和循环控制都由本地代码完成。
