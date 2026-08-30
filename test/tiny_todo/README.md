# Tiny Todo

一个极简的 Python 命令行待办事项程序，只使用 Python 标准库，无需安装任何依赖。

任务保存在同目录的 `tasks.md` 文件中。

## 使用方法

```bash
# 添加一个未完成的任务（内容含空格时必须加引号）
python todo.py add "任务内容"

# 按编号列出所有任务（✔ 表示已完成）
python todo.py list

# 根据 list 显示的编号将任务标记为完成
python todo.py done 任务编号
```

## 示例

```bash
python todo.py add "准备周会材料"
python todo.py list
# 输出：
# 1. [ ] 准备周会材料

python todo.py done 1
python todo.py list
# 输出：
# 1. [✔] 准备周会材料
```

## 说明

- `tasks.md` 初始为空（仅含标题行），第一次执行 `add` 后开始写入任务。
- 任务编号基于 `tasks.md` 中任务的先后顺序，从 1 开始。
- 任务编号必须是正整数；输入负数、小数、非数字内容时会输出清晰错误。
- 当任务编号不存在时（如任务已被删除或编号超出范围），程序会输出清晰的错误提示。
- 支持的命令：`add`、`list`、`done`。
