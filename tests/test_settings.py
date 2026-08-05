from pathlib import Path
import threading
import unittest

from deepseek_balance_monitor.settings import SettingsWorker
from deepseek_balance_monitor.storage import AppConfig


class FakeStartup:
    def __init__(
        self,
        *,
        enabled: bool = False,
        failure: Exception | None = None,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.enabled = enabled
        self.failure = failure
        self.entered = entered
        self.release = release
        self.requests: list[bool] = []

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool) -> None:
        self.requests.append(enabled)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=2)
        if self.failure is not None:
            raise self.failure
        self.enabled = enabled


class SettingsWorkerTests(unittest.TestCase):
    def test_config_save_runs_off_caller_thread_and_latest_pending_wins(self) -> None:
        caller = threading.get_ident()
        saved: list[tuple[int, AppConfig]] = []
        worker = SettingsWorker(
            Path("config.json"),
            save_config=lambda _path, config: saved.append((threading.get_ident(), config)),
            startup=FakeStartup(),
        )
        worker.start()
        worker.request_save(AppConfig(window_x=1))
        worker.request_save(AppConfig(window_x=2))
        worker.close(timeout=None)
        self.assertTrue(saved)
        self.assertNotEqual(saved[-1][0], caller)
        self.assertEqual(saved[-1][1].window_x, 2)

    def test_close_waits_until_accepted_save_finishes(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def save(_path: Path, _config: AppConfig) -> None:
            entered.set()
            release.wait(timeout=2)
            finished.set()

        worker = SettingsWorker(Path("config.json"), save_config=save, startup=FakeStartup())
        worker.start()
        worker.request_save(AppConfig(window_x=3))
        self.assertTrue(entered.wait(timeout=2))
        closer = threading.Thread(target=lambda: worker.close(timeout=None))
        closer.start()
        closer.join(timeout=0.05)
        self.assertTrue(closer.is_alive())
        release.set()
        closer.join(timeout=2)
        self.assertFalse(closer.is_alive())
        self.assertTrue(finished.is_set())

    def test_failed_autostart_reverts_to_actual_state_and_reports_generic_error(self) -> None:
        startup = FakeStartup(enabled=False, failure=OSError("C:/private/path"))
        worker = SettingsWorker(
            Path("config.json"),
            save_config=lambda _path, _config: None,
            startup=startup,
        )
        worker.start()
        worker.request_autostart(True, AppConfig(auto_start=False))
        event = worker.updates.get(timeout=2)
        worker.close(timeout=None)
        self.assertFalse(event.config.auto_start)
        self.assertTrue(event.startup_changed)
        self.assertEqual(event.error, "开机自启动设置失败。")
        self.assertNotIn("private", repr(event))

    def test_successful_autostart_is_saved_and_reported_from_worker(self) -> None:
        caller = threading.get_ident()
        startup = FakeStartup(enabled=False)
        saved: list[tuple[int, AppConfig]] = []
        worker = SettingsWorker(
            Path("config.json"),
            save_config=lambda _path, config: saved.append((threading.get_ident(), config)),
            startup=startup,
        )
        worker.start()
        worker.request_autostart(True, AppConfig(auto_start=False))
        event = worker.updates.get(timeout=2)
        worker.close(timeout=None)
        self.assertTrue(startup.enabled)
        self.assertEqual(startup.requests, [True])
        self.assertTrue(event.config.auto_start)
        self.assertTrue(event.startup_changed)
        self.assertEqual(event.error, "")
        self.assertNotEqual(saved[-1][0], caller)
        self.assertTrue(saved[-1][1].auto_start)

    def test_config_failure_rolls_back_the_changed_startup_entry(self) -> None:
        startup = FakeStartup(enabled=False)

        def fail_save(_path: Path, _config: AppConfig) -> None:
            raise OSError("private failure")

        worker = SettingsWorker(Path("config.json"), save_config=fail_save, startup=startup)
        worker.start()
        worker.request_autostart(True, AppConfig(auto_start=False))
        event = worker.updates.get(timeout=2)
        worker.close(timeout=None)
        self.assertEqual(startup.requests, [True, False])
        self.assertFalse(startup.enabled)
        self.assertFalse(event.config.auto_start)
        self.assertEqual(event.error, "开机自启动设置失败。")

    def test_inflight_autostart_change_cannot_be_replaced(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        startup = FakeStartup(enabled=False, entered=entered, release=release)
        worker = SettingsWorker(
            Path("config.json"),
            save_config=lambda _path, _config: None,
            startup=startup,
        )
        worker.start()
        worker.request_autostart(True, AppConfig())
        self.assertTrue(entered.wait(timeout=2))
        with self.assertRaisesRegex(RuntimeError, "already pending"):
            worker.request_autostart(False, AppConfig())
        release.set()
        worker.close(timeout=None)

    def test_requests_are_rejected_after_close(self) -> None:
        worker = SettingsWorker(
            Path("config.json"),
            save_config=lambda _path, _config: None,
            startup=FakeStartup(),
        )
        worker.start()
        worker.close(timeout=None)
        with self.assertRaisesRegex(RuntimeError, "not running"):
            worker.request_save(AppConfig())


if __name__ == "__main__":
    unittest.main()
