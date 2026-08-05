from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deepseek_balance_monitor.startup import StartupManager


class StartupManagerTests(unittest.TestCase):
    def test_default_is_disabled_and_enable_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager(
                {"APPDATA": temporary},
                Path(r"C:\Python\python.exe"),
                exists=lambda path: path.name.lower() == "pythonw.exe",
            )
            self.assertFalse(manager.is_enabled())
            manager.set_enabled(True)
            first = manager.entry_path.read_bytes()
            manager.set_enabled(True)
            self.assertEqual(manager.entry_path.read_bytes(), first)
            self.assertTrue(manager.is_enabled())
            decoded = first.decode("utf-16")
            self.assertIn(r"C:\Python\pythonw.exe", decoded)
            self.assertIn("-m deepseek_balance_monitor", decoded)
            self.assertNotRegex(decoded, r"sk-[A-Za-z0-9_-]{16,}")

    def test_enable_quotes_an_executable_path_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager(
                {"APPDATA": temporary},
                Path(r"C:\Program Files\Python\python.exe"),
                exists=lambda _path: True,
            )
            manager.set_enabled(True)
            content = manager.entry_path.read_text(encoding="utf-16")
            self.assertIn(
                'shell.Run """C:\\Program Files\\Python\\pythonw.exe"" -m '
                'deepseek_balance_monitor", 0, False',
                content,
            )

    def test_source_launcher_path_is_quoted_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager(
                {"APPDATA": temporary},
                Path(r"C:\Program Files\Python\python.exe"),
                launcher=Path(r"D:\DeepSeek Monitor\deepseek_monitor.py"),
                exists=lambda _path: True,
            )
            manager.set_enabled(True)
            content = manager.entry_path.read_text(encoding="utf-16")
            self.assertIn(
                'shell.Run """C:\\Program Files\\Python\\pythonw.exe"" '
                '""D:\\DeepSeek Monitor\\deepseek_monitor.py""", 0, False',
                content,
            )
            self.assertNotIn("-m deepseek_balance_monitor", content)

    def test_disable_removes_only_the_dedicated_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager({"APPDATA": temporary}, Path(r"C:\Python\python.exe"))
            manager.entry_path.parent.mkdir(parents=True)
            other = manager.entry_path.parent / "OtherApp.vbs"
            other.write_text("safe", encoding="utf-8")
            manager.set_enabled(True)
            manager.set_enabled(False)
            self.assertFalse(manager.entry_path.exists())
            self.assertEqual(other.read_text(encoding="utf-8"), "safe")

    def test_missing_appdata_fails_before_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "APPDATA"):
            StartupManager({}, Path(r"C:\Python\python.exe"))

    def test_replace_failure_preserves_existing_entry_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager({"APPDATA": temporary}, Path(r"C:\Python\python.exe"))
            manager.set_enabled(True)
            original = manager.entry_path.read_bytes()

            with patch("deepseek_balance_monitor.startup.os.replace", side_effect=OSError("synthetic")):
                with self.assertRaisesRegex(OSError, "synthetic"):
                    manager.set_enabled(True)

            self.assertEqual(manager.entry_path.read_bytes(), original)
            leftovers = list(manager.entry_path.parent.glob(".DeepSeekBalanceMonitor.vbs.*.tmp"))
            self.assertEqual(leftovers, [])

    def test_invalid_utf16_entry_is_treated_as_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager({"APPDATA": temporary}, Path(r"C:\Python\python.exe"))
            manager.entry_path.parent.mkdir(parents=True)
            manager.entry_path.write_bytes(b"\xff")
            self.assertFalse(manager.is_enabled())


if __name__ == "__main__":
    unittest.main()
