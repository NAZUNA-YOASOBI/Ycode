# 编程智能体参考与设计方向

本文档记录已经讨论并确认的架构方向，供后续设计和实现时使用。文档中的“参考”指理解公开项目的设计思路，不指复制源码、修改源码后提交，或依赖其框架。

## 参考结论

主要参考 DeepSeek Harness 的核心循环，辅助参考 Codex 的工具设计和错误处理；不直接复用任何一方的工程结构或源码。

选择 DeepSeek Harness 的原因：

- 核心循环集中在少数 TypeScript 文件中，比 Codex 的大型 Rust 实现更容易阅读、理解和讲解。
- 官方架构文档明确区分了 `turn`、`step`、模型请求和工具执行。
- 其核心流程与题目要求直接对应：历史管理、工具定义、模型输出解析、循环终止和错误处理。
- Codex 的核心流程被沙箱、权限、插件、流式输出、多智能体等产品功能包围，不适合作为初学者理解 agent 的第一份参考。
- DeepSeek Harness 整体仍然很复杂，因此只提取中央循环，不采用它的 Cordis 插件架构。

### 项目对比

下表中的仓库规模为调查时通过 GitHub API 获取的近似统计，主要用于比较阅读和实现成本，不是项目的固定属性。

| 项目 | Codex | DeepSeek Harness |
| --- | --- | --- |
| 官方仓库 | `openai/codex` | `deepseek-ai/deepseek-harness` |
| 许可证 | Apache-2.0 | MIT |
| 主要语言 | Rust | TypeScript |
| 调查时成熟度 | 持续发布正式版本 | 开发者预览，发布版本为 RC 预发布版 |
| 调查时仓库规模 | 约 6683 个文件 | 约 7903 个文件 |
| 核心循环位置 | 较长的 Rust 产品级流程 | 主要集中在 `agent.ts` 和 `tool-calls.ts` |
| 整体架构 | 完整的生产级 coding agent | 基于 Cordis 的“所有功能都是插件”架构 |

DeepSeek Harness 的 README 明确提示开发者预览阶段可能发生破坏兼容性的变更。因此我们固定参考已调查的源码和文档，不把它作为项目依赖。

## DSH 的工具数量与配置

“文件相关工具是 DSH 的精简模式”这个理解接近我们要采用的方向，但不是 DSH 官方固定术语。官方使用 `profile` 和 `bundle` 组装运行时能力；当前固定提交中没有一个统一的“只有五个文件工具”的 DSH 总模式。

官方工具目录列出 26 个已发布工具包，但这不是某个 profile 一次运行时必然同时启用的数量。文件相关能力主要分布在 `read`、`write`、`edit`、`read_image`、`glob`、`grep` 和 `str_replace_editor`，命令执行则由 `bash` 或 `pwsh` 提供。`headless` 是一次性任务 profile，它叠加在 `dsh-base` 上并去掉 Web 服务，不等于只保留文件工具。

因此，本项目的五个工具是从 DSH 基础能力中提取后独立重写的最小组合：`read_file`、`list_files`、`write_file`、`replace_text` 和 `run_command`。面试时应表述为“参考 DSH 的文件与命令能力，做了五工具的最小化实现”，不应表述为“DSH 官方只有五个工具”或“这是 DSH 官方的精简模式”。

DSH 能显示上下文窗口，并不是因为标准 `GET /models` 接口返回了该字段。它先从安装的模型目录读取模型元数据，再允许提供商配置通过 `contextWindow` 覆盖；仍未知时使用路由级 `defaultContextWindow`。可选推理等级同样来自目录或配置中的 `reasoningEfforts`，不是对任意兼容网关动态探测得出。

DSH 的 `acme-think` 示例中 `max: ultra` 只是展示私有网关如何转换自定义参数，并不是 DeepSeek 模型的配置。DeepSeek 官方适配器实际提供 `off`、`low`、`high`、`max`：`off` 发送 `thinking.type=disabled`，其余等级发送 `thinking.type=enabled` 和同名 `reasoning_effort`；不选择等级时省略两个字段，保留提供商默认行为。模型能力由本地配置声明，远端接口仍负责最终验证。

## 文件、Shell 与 Web 能力的取舍

从能力类别看，DSH 可以概括为五组常见插件：文件系统、Shell 命令、Web、子 Agent 和 Skills。它们不是五个固定的单一工具；每组可以提供多个模型可见工具。例如文件组包含 `read`、`write`、`edit`、`read_image`，Shell 组包含 `bash` 或 `pwsh`，Web 组包含 `web_search` 和 `web_fetch`。

本项目当前的 `run_command` 已经是 Shell 组的最小实现，作用是让模型在工作目录中运行测试、脚本和其他开发命令。因此第一版并不是“只完成了文件编辑”，而是已经完成了文件操作和 Shell 两类基础能力。

DSH 的 `web_search` 也不是本地搜索算法。官方 DeepSeek 提供方会向 `https://api.deepseek.com/anthropic/v1/messages` 发起一次 Anthropic 兼容的模型请求，并启用服务端原生 `web_search_20250305` 工具；本地代码负责组装请求、发送 HTTP、解析结构化搜索结果和整理来源。这不需要 MCP，但需要 DeepSeek 专用接口、额外的协议解析和可用的网络凭据。

### Anthropic 兼容接口的合规判断

DeepSeek 官方文档说明，`https://api.deepseek.com/anthropic` 是 DeepSeek 提供的 Anthropic Messages 格式兼容入口。它只是请求格式和接口路径兼容，实际调用的仍是 DeepSeek 模型，不是调用 Claude 模型，也不是引入 Claude Code、DeepSeek Harness 或其他 Agent 框架。

按题目原文，这种调用是合规的：题目允许模型厂商 API 客户端、OpenAI 兼容网关和模型原生 tool calling；题目禁止的是 API 服务端托管的代码执行或文件工具，例如 Code Interpreter、Files API。模型 API 本身不属于被禁止的代码或文件工具。为减少依赖和歧义，本项目如需调用该入口，优先使用标准库直接发送 HTTP 请求，不把 Anthropic SDK 当作 Agent 框架使用。

DSH 使用该入口时额外启用了 DeepSeek 的服务端原生 `web_search_20250305` 工具。网页搜索的索引和检索由 DeepSeek 服务端完成，本地代码只负责发请求、解析结果和把结果放回消息历史。题目没有禁止服务端网页搜索，只要它不替代本项目自行实现的文件读写、命令执行、历史管理、工具分派和循环逻辑即可。因此网页搜索可以作为可选能力加入，但不能表述成“本地实现了搜索引擎”，也不应让它成为编程任务能够运行的前提。

### 服务端托管工具的边界示例

题目禁止的“服务端托管”指：代码执行或文件处理发生在模型服务商的服务器上，本项目只上传数据、发出请求并接收结果。例如：

- 使用 Code Interpreter 或类似 `code_interpreter` 工具，把 `calculator.py` 上传到服务商的沙箱，由服务商运行 Python 或测试命令，再返回输出。
- 使用 Files API、托管文件搜索或托管文件编辑能力，让服务商保存、读取或修改项目文件，再下载处理结果。
- 调用一个云端 Shell 或云端代码执行 API，让服务商代替本项目执行 `pytest`、脚本或系统命令。

允许且应当自行实现的流程是：文件留在本地工作目录，模型通过普通 tool calling 请求 `read_file`、`replace_text` 或 `run_command`，本项目的 Python 代码实际读取、修改文件或通过本地进程执行命令。模型 API 只负责生成请求，不负责执行这些编程操作。

网页搜索属于不同类别：搜索索引和检索确实由 DeepSeek 服务端完成，但它不是题目点名禁止的代码执行或文件工具。因此可以作为可选能力调用；必须如实说明搜索由服务商提供，并保证编程任务的文件、命令和 Agent 循环仍由本项目自行实现。

基于题目的真实编程任务、两分钟视频和简短面试，第一版保留文件 + Shell 两类能力作为主流程，并加入 `web_search`。搜索与主 Agent 共用同一个 API key，工具始终注册；该 key 必须能够访问 DeepSeek 搜索接口。搜索工具只通过标准库直接 HTTP 调用 DeepSeek 服务端原生搜索，不引入 MCP、Agent 框架或 DSH 依赖，也不把网页搜索误称为本地实现的搜索引擎。

搜索模型与主 Agent 模型相互独立。未指定时使用 `deepseek-v4-flash`；需要切换时通过 `DEEPSEEK_SEARCH_MODEL` 环境变量或 `--search-model` 命令行参数覆盖。搜索模型不单独请求远端模型列表，只把最终配置值写入搜索请求，以保留 DSH 的“默认值 + 可配置覆盖”行为，同时避免增加第二套模型发现和缓存逻辑。

## 开源与合规边界

- Codex 官方仓库：[`openai/codex`](https://github.com/openai/codex)，Apache-2.0 许可证，主要语言为 Rust。
- DeepSeek Harness 官方仓库：[`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness)，MIT 许可证，主要语言为 TypeScript。
- DeepSeek Harness 的 README 标注项目处于开发者预览阶段，并提示可能存在破坏兼容性的变更。
- 两个项目都只作为学习和对照对象。我们的项目必须独立编写，不复制源码，不使用 DeepSeek Harness 的 Cordis、插件树、事件瀑布、profile、bundle 或其他 agent 框架机制。
- 题目要求重要逻辑自行编写，因此“参考流程、独立重写”比直接改造任何开源项目更符合题意，也更容易在面试中解释设计决策。

## Agent 是什么

一个 coding agent 不是让模型直接操作电脑，而是由两部分协作完成任务：

- 大语言模型根据当前信息决定下一步做什么。
- 本地 agent 程序保存历史、调用模型、解析工具请求、执行本地操作，再把执行结果交给模型。

例如，模型可能输出下面的结构化工具请求：

```json
{
  "name": "read_file",
  "arguments": {
    "path": "src/app.py"
  }
}
```

这段内容只是请求。真正读取 `src/app.py` 的是 agent 中自行编写的本地工具函数。

## 核心流程

两个参考项目的中央流程可以简化为同一条主链：

```text
接收用户任务
    ↓
Agent 保存用户消息
    ↓
组装系统提示词、历史消息和工具定义
    ↓
调用大语言模型
    ↓
模型是否请求工具？
    ├─ 否：把模型回答作为最终结果，结束
    └─ 是：解析工具名称和参数
               ↓
          在本地执行工具
               ↓
          保存工具执行结果
               ↓
          带着更新后的历史再次调用模型
```

### Turn 与 Step

- `turn`：从用户提交一次任务开始，到 agent 给出最终答复结束的一整轮工作。
- `step`：一次模型调用，以及该次调用产生的工具执行。
- 一个 `turn` 通常包含多个 `step`。模型每调用一次工具并收到结果，就可能进入下一个 `step`。

### 真实任务示例

假设任务是“修复 `calculator.py` 中的除法错误并运行测试”：

```text
Turn 开始
  Step 1：模型请求 list_files
  Step 2：模型请求 read_file("calculator.py")
  Step 3：模型请求 replace_text(...)
  Step 4：模型请求 run_command("pytest")
  Step 5：模型看到测试通过，不再调用工具，输出最终说明
Turn 结束
```

每执行完一个工具，都必须把工具结果加入消息历史。测试失败时，错误输出也必须交给模型，模型才能根据错误继续修改代码。

## 必须自行实现的逻辑

- 消息历史：保存用户消息、模型回复、工具请求和工具结果。
- 上下文组装：每次调用模型前决定发送哪些历史消息和工具说明。
- 工具定义：向模型说明可用工具和参数格式。
- 工具分派：根据工具名称调用对应的本地函数。
- 模型输出解析：区分普通回答和结构化工具请求。
- 循环控制：执行工具后再次调用模型。
- 终止条件：模型不再请求工具、达到最大步骤数或出现不可恢复错误时结束。
- 错误处理：工具失败时把错误交给模型；模型 API 暂时失败时进行有限重试。

## 第一版：核心 Agent

第一版先完成能够清楚展示 Agent 核心流程的最小终端程序：

- 终端入口和一次任务的内存消息列表。
- OpenAI 兼容模型调用和工具注册表。
- `read_file`、`list_files`、`write_file`、`replace_text`、`run_command` 五个本地工具。
- 可选的 `web_search` 工具：调用 DeepSeek Anthropic 兼容接口的服务端原生搜索；搜索模型默认使用 `deepseek-v4-flash`，可通过 `DEEPSEEK_SEARCH_MODEL` 或 `--search-model` 覆盖。
- 模型请求工具时执行本地工具并把结果写回历史；模型不再请求工具时结束。
- 最大循环次数、有限重试、工作目录限制、命令超时、工具输出长度限制和错误处理。
- 在终端打印模型回复、工具调用、工具结果和最终结果。

## 第二版：Web 界面与会话管理

第二版复用第一版的 Agent 循环和本地工具，增加本机 Web 工作台及持久化会话：

- 使用 Python 标准库提供只监听 `127.0.0.1` 的 Web 服务，不增加 Web 框架或前端构建依赖。
- 核心循环输出结构化运行事件，模型响应按 OpenAI 兼容 SSE 增量读取；网页在对应内容出现时实时显示 step、模型回复、工具调用、工具结果、最终回答和错误状态。
- 增加 Ycode Web 界面、紫色主题、响应式布局和空输入时禁用的发送按钮。
- 主模型通过兼容接口的 `GET /models` 获取；页面支持鼠标选择模型，`AGENT_MODEL` 可指定默认模型，未指定时使用远端列表中的第一个模型。
- 已知 DeepSeek V4 模型按本地能力表提供 `Off`、`Low`、`High`、`Max` 推理等级选择；未知模型只使用 `Provider default`。推理模式下返回的 `reasoning_content` 随模型消息保存，保证工具调用后的下一步请求能够完整回放。
- 推理模式下，模型返回的 `reasoning_content` 会作为工作过程事件显示。每个用户任务有一个大的 `Agent process` 折叠区，内部按事件实际顺序显示可单独折叠且支持 Markdown 的 Thinking 和连续工具批次；模型回复和最终回答也使用 Markdown 渲染，工具批次仍保留 `Tools → Tool → Call/Result` 多级折叠，最终回答位于工作过程区外。
- 根据本地模型配置的上下文上限和接口返回的 `usage.prompt_tokens` 显示上下文占用百分比；最近一次用量随会话保存，未知数据不自行猜测。
- 会话历史保存到工作目录 `sessions/` 下的独立 JSON 文件，记录消息、标题、时间和最近一次 token 用量。
- 左侧历史栏支持新建、切换和恢复会话；刷新或重新启动 Web 服务后仍可继续历史对话。
- Web 与终端共用同一套模型配置和 API key，凭据只保留在服务端环境变量中。

当前仍未实现多智能体、插件系统、并行工具、复杂上下文压缩、后台任务和完整操作系统沙箱；这些能力不属于本项目的最小演示范围。

## 设计取舍

Codex 的产品级实现还包含流式输出、上下文压缩、重试、沙箱、审批、多智能体、插件和遥测等功能。DeepSeek Harness 则把模型、会话日志、工具、循环、权限和界面拆成 Cordis 插件，并通过事件连接。这些能力适合完整产品，但超出本题短时演示的必要范围。

本项目优先保证核心数据流简单可见：模型提出请求，本地执行请求，执行结果回到历史，模型继续判断。这样既满足题目要求，也能在面试中解释每个步骤为什么存在、如何结束以及发生错误时如何处理。

## 后续协作方式

涉及模型选择、架构方向、工具设计或其他重要知识点时，先说明原理、选择依据、实现流程和取舍，确保能够理解 agent 正在做什么以及为什么这样做；方案确认后再实施，不把大的方向和复杂实现一次性直接完成。

## 参考源码入口

- DeepSeek Harness 核心循环：[`packages/core/agent-loop/src/agent.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/b150a551b8d465e31e418e1b2eaf5e79bbb7d28e/packages/core/agent-loop/src/agent.ts)
- DeepSeek Harness 工具调度：[`packages/core/agent-loop/src/tool-calls.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/b150a551b8d465e31e418e1b2eaf5e79bbb7d28e/packages/core/agent-loop/src/tool-calls.ts)
- DeepSeek Harness 架构说明：[`docs/architecture.md`](https://github.com/deepseek-ai/deepseek-harness/blob/b150a551b8d465e31e418e1b2eaf5e79bbb7d28e/docs/architecture.md)
- Codex 核心回合流程：[`codex-rs/core/src/session/turn.rs`](https://github.com/openai/codex/blob/5af6979986a23fcd6bbeb1ef7b206cbc96e9a0a2/codex-rs/core/src/session/turn.rs)

这些链接用于后续复核公开资料，不代表项目会复制对应实现。源码分析以调查时的固定提交为准。
