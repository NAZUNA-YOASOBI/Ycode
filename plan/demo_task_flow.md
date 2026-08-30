# 演示任务流程

## 演示目标

从空目录开始创建一个简单的 Python 命令行待办事项程序，分阶段完成实现和测试，展示 Ycode 的文件工具、Shell、上下文压缩、会话持久化和流式工作过程。

## 第一阶段：创建程序

在新 session 中发送：

```text
请从零开始，在 test/tiny_todo 目录中创建一个简单的 Python 命令行待办事项程序。

所有操作必须限制在 test/tiny_todo 内，只使用 Python 标准库。

实现：
- `python todo.py add "任务内容"`：添加未完成任务；
- `python todo.py list`：按编号列出任务；
- `python todo.py done 任务编号`：标记任务完成；
- 使用同目录的 `tasks.md` 保存任务；
- 编号不存在时输出清晰错误；
- 创建 `todo.py`、`tasks.md` 和 `README.md`。

不要创建或运行测试。开始前用 glob 检查目录，创建文件后用 glob、grep 和 read_file 检查代码。完成后说明创建了哪些文件以及如何运行。
```

## 第二阶段：检查程序

第一阶段结束后，在同一个 session 中发送：

```text
请检查 test/tiny_todo 中刚才创建的程序和 README，修正明显的命令示例或错误提示问题。仍然不要创建测试，也不要修改目录外的文件。
```

## 第三阶段：压缩并验证记忆

第二阶段结束后，输入严格的：

```text
/compact
```

压缩完成后，发送一个不调用工具的记忆检查任务：

```text
请不要调用任何工具，也不要修改文件。请根据当前会话上下文，简要说明：
1. 我们刚才创建了什么程序；
2. 创建了哪些文件；
3. 程序目前支持哪些命令；
4. 下一步还需要完成什么工作。
```

如果回答能正确说出 `test/tiny_todo`、`todo.py`、`tasks.md`、`README.md`、已有命令和待测试状态，说明压缩摘要已被正确恢复。

全新 session 至少需要两个已完成的任务块后才能压缩较早历史；只有一个任务块时会提示没有可压缩内容。

## 第四阶段：补充测试

记忆检查完成后发送：

```text
现在为 test/tiny_todo 创建 test_todo.py，使用 unittest 测试添加、列出和完成任务，并运行全部测试。如果测试失败，请继续修改程序，直到测试全部通过。所有操作仍然只能在 test/tiny_todo 内。
```

## 预期过程

```text
从零创建程序
  -> glob 检查目录
  -> write_file 创建文件
  -> glob、grep、read_file 检查代码
  -> 完成第一次任务
  -> 检查并修正文档
  -> /compact 压缩较早任务
  -> 询问是否记得之前工作
  -> 创建测试并用 run_command 运行
  -> 根据失败结果修改代码
  -> 测试通过并给出最终回答
```

演示过程中可以选择模型和推理等级，观察 Thinking、Tools、Tool call、Result 和 Final answer 的流式显示与折叠；压缩后刷新页面或切换 session，可以同时展示 `Context summary` 和历史会话持久化。
