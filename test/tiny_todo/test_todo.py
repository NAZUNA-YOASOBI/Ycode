"""Unit tests for tiny_todo, using only the Python standard library."""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import todo


class TinyTodoTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_file = os.path.join(self.tmpdir.name, "tasks.md")
        self.old_data_file = todo.DATA_FILE
        todo.DATA_FILE = self.data_file

    def tearDown(self):
        todo.DATA_FILE = self.old_data_file
        self.tmpdir.cleanup()

    def run_cmd_list(self):
        """Run todo.cmd_list() and return its printed output."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            todo.cmd_list()
        return buf.getvalue()

    def test_add_writes_open_task(self):
        result = todo.cmd_add("任务一")
        self.assertEqual(result, None)
        self.assertEqual(todo.read_tasks(), [(False, "任务一")])
        with open(self.data_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "- [ ] 任务一\n")

    def test_add_appends_after_existing(self):
        todo.cmd_add("任务一")
        todo.cmd_add("任务二")
        self.assertEqual(
            todo.read_tasks(), [(False, "任务一"), (False, "任务二")]
        )

    def test_list_shows_numbered_open_tasks(self):
        todo.cmd_add("任务一")
        todo.cmd_add("任务二")
        output = self.run_cmd_list()
        self.assertIn("1. [ ] 任务一", output)
        self.assertIn("2. [ ] 任务二", output)

    def test_list_shows_checkmark_for_done(self):
        todo.cmd_add("任务一")
        todo.cmd_done(1)
        output = self.run_cmd_list()
        self.assertIn("1. [✔] 任务一", output)

    def test_list_empty(self):
        self.assertEqual(self.run_cmd_list(), "暂无任务。\n")

    def test_done_marks_task_complete(self):
        todo.cmd_add("任务一")
        result = todo.cmd_done(1)
        self.assertEqual(result, 0)
        self.assertEqual(todo.read_tasks(), [(True, "任务一")])
        with open(self.data_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "- [x] 任务一\n")

    def test_done_out_of_range(self):
        todo.cmd_add("任务一")
        result = todo.cmd_done(99)
        self.assertEqual(result, 1)
        self.assertEqual(todo.read_tasks(), [(False, "任务一")])

    def test_done_already_done(self):
        todo.cmd_add("任务一")
        todo.cmd_done(1)
        result = todo.cmd_done(1)
        self.assertEqual(result, 0)
        self.assertEqual(todo.read_tasks(), [(True, "任务一")])

    def test_main_add(self):
        self.assertEqual(todo.main(["add", "命令行任务"]), 0)
        self.assertEqual(todo.read_tasks(), [(False, "命令行任务")])

    def test_main_add_empty(self):
        self.assertEqual(todo.main(["add", "  "]), 2)
        self.assertEqual(todo.main(["add"]), 2)

    def test_main_list(self):
        todo.cmd_add("任务一")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(todo.main(["list"]), 0)
        self.assertIn("1. [ ] 任务一", buf.getvalue())

    def test_main_done_valid(self):
        todo.cmd_add("任务一")
        self.assertEqual(todo.main(["done", "1"]), 0)
        self.assertEqual(todo.read_tasks(), [(True, "任务一")])

    def test_main_done_missing_number(self):
        todo.cmd_add("任务一")
        self.assertEqual(todo.main(["done", "99"]), 1)

    def test_main_done_non_integer(self):
        self.assertEqual(todo.main(["done", "abc"]), 2)
        self.assertEqual(todo.main(["done", "1.5"]), 2)
        self.assertEqual(todo.main(["done", "-1"]), 2)

    def test_main_done_no_argument(self):
        self.assertEqual(todo.main(["done"]), 2)

    def test_main_no_arguments(self):
        self.assertEqual(todo.main([]), 2)

    def test_main_unknown_command(self):
        self.assertEqual(todo.main(["unknown"]), 2)


if __name__ == "__main__":
    unittest.main()
