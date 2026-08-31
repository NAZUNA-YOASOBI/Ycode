#!/usr/bin/env python3
"""A simple command-line todo list application (Python standard library only).

Tasks are stored in tasks.md located in the same directory as this script.
Each task is a Markdown list item:
    - [ ] task text   -> not done
    - [x] task text   -> done
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_FILE = os.path.join(BASE_DIR, "tasks.md")

HEADER = "# Tasks\n"


def read_tasks():
    """Return the raw task lines from tasks.md (without the header)."""
    if not os.path.exists(TASKS_FILE):
        return []
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    if lines and lines[0] == HEADER.strip():
        lines = lines[1:]
    return lines


def write_tasks(lines):
    """Write the given task lines back to tasks.md."""
    content = HEADER
    if lines:
        content += "\n".join(lines) + "\n"
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        f.write(content)


def add(text):
    """Add a new (not done) task."""
    text = text.strip()
    if not text:
        print("Error: task content is empty.")
        sys.exit(1)
    lines = read_tasks()
    lines.append("- [ ] " + text)
    write_tasks(lines)
    print('Added: "{}"'.format(text))


def list_tasks():
    """List tasks with 1-based numbers."""
    lines = read_tasks()
    if not lines:
        print("No tasks.")
        return
    for i, line in enumerate(lines, start=1):
        done = line.startswith("- [x]")
        text = line[6:]
        mark = "[x]" if done else "[ ]"
        print("{}. {} {}".format(i, mark, text))


def mark_done(number):
    """Mark the task with the given 1-based number as done."""
    try:
        idx = int(number) - 1
    except ValueError:
        print('Error: "{}" is not a valid task number.'.format(number))
        sys.exit(1)

    lines = read_tasks()
    if idx < 0 or idx >= len(lines):
        print("Error: task number {} does not exist.".format(number))
        sys.exit(1)

    if lines[idx].startswith("- [x]"):
        print("Task {} is already done.".format(number))
        return

    lines[idx] = lines[idx].replace("- [ ]", "- [x]", 1)
    write_tasks(lines)
    print("Task {} marked as done.".format(number))


def usage():
    print(
        "Usage:\n"
        "  python todo.py add 任务内容     Add a new task\n"
        "  python todo.py list             List all tasks\n"
        "  python todo.py done 任务编号     Mark a task as done"
    )


def main():
    if len(sys.argv) < 2:
        usage()
        sys.exit(1)

    command = sys.argv[1]

    if command == "add":
        if len(sys.argv) < 3:
            print("Error: missing task content. Usage: python todo.py add 任务内容")
            sys.exit(1)
        add(" ".join(sys.argv[2:]))
    elif command == "list":
        list_tasks()
    elif command == "done":
        if len(sys.argv) < 3:
            print("Error: missing task number. Usage: python todo.py done 任务编号")
            sys.exit(1)
        mark_done(sys.argv[2])
    else:
        print('Error: unknown command "{}".'.format(command))
        usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
