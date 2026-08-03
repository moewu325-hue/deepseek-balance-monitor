from __future__ import annotations

import unittest

from deepseek_balance_monitor.windows import MUTEX_NAME, NamedMutex


class FakeWinApi:
    def __init__(self, handle: int, error: int) -> None:
        self.handle = handle
        self.error = error
        self.created: list[str] = []
        self.closed: list[int] = []

    def create_mutex(self, name: str) -> tuple[int, int]:
        self.created.append(name)
        return self.handle, self.error

    def close_handle(self, handle: int) -> None:
        self.closed.append(handle)


class FlakyCloseWinApi(FakeWinApi):
    def __init__(self, handle: int, error: int) -> None:
        super().__init__(handle, error)
        self.failures_remaining = 1

    def close_handle(self, handle: int) -> None:
        self.closed.append(handle)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise OSError(5, "synthetic close failure")


class NamedMutexTests(unittest.TestCase):
    def test_new_named_mutex_is_owned_and_closed_exactly_once(self) -> None:
        backend = FakeWinApi(101, 0)
        mutex = NamedMutex(backend=backend)

        self.assertTrue(mutex.acquire())
        self.assertTrue(mutex.acquire())
        self.assertEqual(backend.created, [r"Local\MoeWu.DeepSeekBalanceMonitor"])
        self.assertEqual(MUTEX_NAME, r"Local\MoeWu.DeepSeekBalanceMonitor")

        mutex.close()
        mutex.close()
        self.assertEqual(backend.closed, [101])

    def test_existing_mutex_closes_returned_handle_without_claiming_ownership(self) -> None:
        backend = FakeWinApi(202, 183)
        mutex = NamedMutex(backend=backend)

        self.assertFalse(mutex.acquire())

        self.assertEqual(backend.closed, [202])
        mutex.close()
        self.assertEqual(backend.closed, [202])

    def test_failed_mutex_creation_raises_without_closing_null_handle(self) -> None:
        backend = FakeWinApi(0, 5)
        mutex = NamedMutex(backend=backend)

        with self.assertRaises(OSError) as captured:
            mutex.acquire()

        self.assertNotIn(MUTEX_NAME, str(captured.exception))
        self.assertEqual(backend.closed, [])

    def test_owned_handle_close_failure_is_retained_for_retry(self) -> None:
        backend = FlakyCloseWinApi(303, 0)
        mutex = NamedMutex(backend=backend)
        self.assertTrue(mutex.acquire())

        with self.assertRaises(OSError):
            mutex.close()
        mutex.close()

        self.assertEqual(backend.closed, [303, 303])

    def test_existing_handle_close_failure_is_unowned_and_retryable(self) -> None:
        backend = FlakyCloseWinApi(404, 183)
        mutex = NamedMutex(backend=backend)

        with self.assertRaises(OSError):
            mutex.acquire()
        self.assertFalse(mutex.acquire())
        mutex.close()

        self.assertEqual(backend.created, [MUTEX_NAME])
        self.assertEqual(backend.closed, [404, 404])


if __name__ == "__main__":
    unittest.main()
