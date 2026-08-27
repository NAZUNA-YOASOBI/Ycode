小型 Coding Agent

运行环境：Python 3.10 及以上，无第三方依赖。

运行：
1. 设置 AGENT_API_KEY 和 AGENT_MODEL。
2. 可选设置 AGENT_BASE_URL；默认使用 OpenAI 兼容接口地址 https://api.openai.com/v1。
3. 可选设置 DEEPSEEK_API_KEY，启用 DeepSeek 服务端网页搜索；未设置时不注册 web_search 工具。
4. 搜索模型默认使用 deepseek-v4-flash，可通过 DEEPSEEK_SEARCH_MODEL 或 --search-model 覆盖。
5. 执行：python src/agent.py "用一句话描述要完成的编程任务"

Agent 将任务、历史消息和工具定义发送给模型。模型需要读写文件、执行命令或搜索资料时返回工具调用，Agent 执行后把结果写回历史，再继续请求模型，直到模型给出最终回答或达到最大步数。

当前工具：read_file、list_files、write_file、replace_text、run_command，以及可选的 web_search。
web_search 调用 DeepSeek Anthropic 兼容接口中的服务端原生搜索，只返回搜索来源，不负责本地文件或命令操作。
工具结果有长度限制，命令有超时限制，文件路径限制在工作目录内。
