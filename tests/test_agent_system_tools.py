import os
import tempfile
import unittest

from app.agent.tools import (
    copy_file,
    execute_shell_command,
    get_disk_usage,
    get_file_info,
    get_system_info,
    list_directory,
    make_directory,
    move_file,
    search_files,
)


class AgentSystemToolsTest(unittest.TestCase):
    def test_list_directory_returns_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "demo.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("hello")

            output = list_directory.invoke({"path": tmp})

            self.assertIn("demo.txt", output)
            self.assertIn("file", output)

    def test_search_files_matches_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "agent_demo.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("print('ok')")

            output = search_files.invoke({"pattern": "*.py", "root": tmp})

            self.assertIn("agent_demo.py", output)

    def test_get_file_info_reports_file_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "info.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("content")

            output = get_file_info.invoke({"path": file_path})

            self.assertIn("type: file", output)
            self.assertIn("size_bytes:", output)

    def test_copy_file_respects_no_overwrite_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.txt")
            dst = os.path.join(tmp, "dst.txt")
            with open(src, "w", encoding="utf-8") as f:
                f.write("source")
            with open(dst, "w", encoding="utf-8") as f:
                f.write("target")

            output = copy_file.invoke({"src": src, "dst": dst})

            self.assertIn("目标已存在", output)
            with open(dst, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "target")

    def test_move_file_moves_source_to_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.txt")
            dst = os.path.join(tmp, "dst.txt")
            with open(src, "w", encoding="utf-8") as f:
                f.write("source")

            output = move_file.invoke({"src": src, "dst": dst})

            self.assertIn("移动成功", output)
            self.assertFalse(os.path.exists(src))
            self.assertTrue(os.path.exists(dst))

    def test_make_directory_creates_nested_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "a", "b")

            output = make_directory.invoke({"path": target})

            self.assertIn("目录已创建或已存在", output)
            self.assertTrue(os.path.isdir(target))

    def test_system_info_and_disk_usage_return_structured_text(self):
        system_output = get_system_info.invoke({})
        disk_output = get_disk_usage.invoke({"path": "."})

        self.assertIn("system:", system_output)
        self.assertIn("python:", system_output)
        self.assertIn("free_gb:", disk_output)

    def test_execute_shell_command_rejects_dangerous_command_before_approval(self):
        output = execute_shell_command.invoke({"command": "rm -rf /"})

        self.assertIn("高风险操作", output)


if __name__ == "__main__":
    unittest.main()
