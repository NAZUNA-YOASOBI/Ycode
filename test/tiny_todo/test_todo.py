#!/usr/bin/env python3
"""Unit tests for the tiny todo app.

Tests exercise add, list, and done, using a temporary tasks file so the
real tasks.md is never touched.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

# Ensure the app module is importable from this directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import todo  # noqa: E402


class TodoTestCase(unittest.TestCase):
    def setUp(self):
        # Point the app at a throwaway tasks file for each test.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_tasks = os.path.join(self.temp_dir.name, "tasks.md")
        todo.TASKS_FILE = self.temp_tasks
        todo.write_tasks([])

    def tearDown(self):
        self.temp_dir.cleanup()

    def _capture(self, func, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            func(*args)
        return buf.getvalue()


class TestAdd(TodoTestCase):
    def test_add_creates_not_done_task(self):
        todo.add("Buy milk")
        self.assertEqual(todo.read_tasks(), ["- [ ] Buy milk"])

    def test_add_multiple_tasks(self):
        todo.add("Buy milk")
        todo.add("Pay rent")
        self.assertEqual(todo.read_tasks(), ["- [ ] Buy milk", "- [ ] Pay rent"])

    def test_add_strips_whitespace(self):
        todo.add("  Buy milk  ")
        self.assertEqual(todo.read_tasks(), ["- [ ] Buy milk"])

    def test_add_rejects_empty_text(self):
        with self.assertRaises(SystemExit) as cm:
            todo.add("   ")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(todo.read_tasks(), [])

    def test_add_prints_confirmation(self):
        out = self._capture(todo.add, "Buy milk")
        self.assertIn('Added: "Buy milk"', out)


class TestList(TodoTestCase):
    def test_list_empty(self):
        out = self._capture(todo.list_tasks)
        self.assertEqual(out.strip(), "No tasks.")

    def test_list_numbers_and_status(self):
        todo.add("Buy milk")
        todo.add("Pay rent")
        todo.mark_done(1)
        out = self._capture(todo.list_tasks)
        self.assertIn("1. [x] Buy milk", out)
        self.assertIn("2. [ ] Pay rent", out)


class TestDone(TodoTestCase):
    def test_done_marks_task(self):
        todo.add("Buy milk")
        todo.mark_done(1)
        self.assertEqual(todo.read_tasks(), ["- [x] Buy milk"])

    def test_done_does_not_touch_other_tasks(self):
        todo.add("Buy milk")
        todo.add("Pay rent")
        todo.mark_done(1)
        self.assertEqual(todo.read_tasks(), ["- [x] Buy milk", "- [ ] Pay rent"])

    def test_done_invalid_number_exits(self):
        todo.add("Buy milk")
        with self.assertRaises(SystemExit) as cm:
            todo.mark_done("abc")
        self.assertEqual(cm.exception.code, 1)

    def test_done_out_of_range_exits(self):
        todo.add("Buy milk")
        with self.assertRaises(SystemExit) as cm:
            todo.mark_done(5)
        self.assertEqual(cm.exception.code, 1)
        with self.assertRaises(SystemExit):
            todo.mark_done(0)

    def test_done_already_done_is_noop(self):
        todo.add("Buy milk")
        todo.mark_done(1)
        out = self._capture(todo.mark_done, 1)
        self.assertIn("already done", out)
        self.assertEqual(todo.read_tasks(), ["- [x] Buy milk"])


if __name__ == "__main__":
    unittest.main()
