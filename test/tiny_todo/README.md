# Tiny Todo

一个极简的 Python 命令行待办事项程序，仅使用 Python 标准库，任务保存在同目录的 `tasks.md` 中。

## 文件

- `todo.py`  - 应用程序
- `tasks.md` - 任务存储文件（Markdown 格式）

## 使用方法

在 `test/tiny_todo` 目录下运行：

```bash
# 添加一个未完成任务
python todo.py add "任务内容"

# 添加多个词的任务（带引号可选，多余参数会拼接）
python todo.py add 买牛奶 和 面包

# 按编号列出所有任务（输出格式见下）
python todo.py list

# 将编号为 1 的任务标记为完成
python todo.py done 1
```

`list` 命令的输出示例：

```text
1. [ ] 买牛奶
2. [x] 已完成的任务
```

任务编号不存在或不是有效数字时，程序会输出清晰的错误信息，例如：

```text
Error: task number 99 does not exist.
```

## 数据格式

`tasks.md` 中每个任务占一行：

- `- [ ] 任务内容` -> 未完成
- `- [x] 任务内容` -> 已完成
