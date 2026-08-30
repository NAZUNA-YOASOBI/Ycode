#!/usr/bin/env python3
"""A tiny command-line todo list powered only by the Python standard library.

Tasks are stored in tasks.md in the same directory as this script.
"""

import os
import sys

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.md")

DONE_PREFIX = "- [x] "
OPEN_PREFIX = "- [ ] "


def read_tasks():
    """Read tasks from tasks.md, returning a list of (done, content) tuples."""
    if not os.path.exists(DATA_FILE):
        return []
    tasks = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith(DONE_PREFIX):
                tasks.append((True, line[len(DONE_PREFIX):]))
            elif line.startswith(OPEN_PREFIX):
                tasks.append((False, line[len(OPEN_PREFIX):]))
    return tasks


def write_tasks(tasks):
    """Write the list of (done, content) tuples back to tasks.md."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        for done, content in tasks:
            prefix = DONE_PREFIX if done else OPEN_PREFIX
            f.write(prefix + content + "\n")


def cmd_add(content):
    tasks = read_tasks()
    tasks.append((False, content))
    write_tasks(tasks)
    print(f"已添加任务：{content}")


def cmd_list():
    tasks = read_tasks()
    if not tasks:
        print("暂无任务。")
        return
    for number, (done, content) in enumerate(tasks, start=1):
        mark = "✔" if done else " "
        print(f"{number}. [{mark}] {content}")


def cmd_done(number):
    tasks = read_tasks()
    if number < 1 or number > len(tasks):
        print(f"错误：任务编号 {number} 不存在（当前共有 {len(tasks)} 个任务）。")
        return 1
    done, content = tasks[number - 1]
    if done:
        print(f"任务 {number}（{content}）已经是完成状态。")
    else:
        tasks[number - 1] = (True, content)
        write_tasks(tasks)
        print(f"已将任务 {number} 标记为完成：{content}")
    return 0


def main(argv):
    if not argv:
        print("用法：")
        print('  python todo.py add "任务内容"')
        print("  python todo.py list")
        print("  python todo.py done 任务编号")
        return 2

    command = argv[0]

    if command == "add":
        if len(argv) < 2 or not argv[1].strip():
            print('错误：请提供任务内容，例如 python todo.py add "买牛奶"。')
            return 2
        cmd_add(argv[1].strip())
        return 0

    if command == "list":
        cmd_list()
        return 0

    if command == "done":
        if len(argv) < 2:
            print("错误：请提供任务编号（正整数），例如 python todo.py done 1。")
            return 2
        raw = argv[1]
        if not raw.isdigit():
            print(f"错误：任务编号必须是正整数，收到“{raw}”。")
            return 2
        number = int(raw)
        return cmd_done(number)

    print(f"错误：未知命令“{command}”。支持的命令：add、list、done。")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
