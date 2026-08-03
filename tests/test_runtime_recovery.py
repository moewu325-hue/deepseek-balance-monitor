from __future__ import annotations

from collections import deque
from dataclasses import FrozenInstanceError
from queue import Queue
import threading
import unittest

from deepseek_balance_monitor.controller import (
    BalanceController,
    ControllerEventKind,
)
from deepseek_balance_monitor.credentials import CredentialCandidate, DiscoveryResult
from deepseek_balance_monitor.models import FetchResult, ProbeResult, ProbeStatus


KEY_ONE = "sk-synthetic-controller-one"
KEY_TWO = "sk-synthetic-controller-two"


def discovery(key: str, *, verified: bool = True) -> DiscoveryResult:
    status = ProbeStatus.VALID if verified else ProbeStatus.INDETERMINATE
    return DiscoveryResult(
        CredentialCandidate("synthetic", key),
        ProbeResult(status),
        verified,
    )


class FakeResolver:
    def __init__(self, results: list[DiscoveryResult]) -> None:
        self.results = deque(results)
        self.calls: list[frozenset[str]] = []
        self.thread_ids: list[int] = []

    def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
        self.calls.append(excluded)
        self.thread_ids.append(threading.get_ident())
        return self.results.popleft()


class FakeApi:
    def __init__(self, results: list[FetchResult]) -> None:
        self.results = deque(results)
        self.keys: list[str] = []
        self.thread_ids: list[int] = []

    def fetch_balance(self, key: str) -> FetchResult:
        self.keys.append(key)
        self.thread_ids.append(threading.get_ident())
        return self.results.popleft()


def take(controller: BalanceController, count: int):
    return [controller.updates.get(timeout=2) for _ in range(count)]


class RuntimeRecoveryTests(unittest.TestCase):
    def test_start_discovers_once_and_fetches_on_worker_thread(self) -> None:
        resolver = FakeResolver([discovery(KEY_ONE)])
        api = FakeApi([FetchResult(None, status_code=200)])
        controller = BalanceController(api_client=api, resolver=resolver)
        main_thread = threading.get_ident()
        self.addCleanup(controller.close)

        controller.start()
        controller.start()
        events = take(controller, 2)

        self.assertEqual([event.kind for event in events], [
            ControllerEventKind.DISCOVERY,
            ControllerEventKind.FETCH,
        ])
        self.assertEqual(resolver.calls, [frozenset()])
        self.assertEqual(api.keys, [KEY_ONE])
        self.assertNotEqual(api.thread_ids[0], main_thread)
        self.assertNotEqual(resolver.thread_ids[0], main_thread)

    def test_seeded_discovery_avoids_duplicate_startup_discovery(self) -> None:
        resolver = FakeResolver([])
        api = FakeApi([FetchResult(None, status_code=200)])
        controller = BalanceController(
            api_client=api,
            resolver=resolver,
            initial_discovery=discovery(KEY_ONE),
        )
        self.addCleanup(controller.close)

        controller.start()
        take(controller, 2)

        self.assertEqual(resolver.calls, [])
        self.assertEqual(api.keys, [KEY_ONE])

    def test_runtime_401_excludes_key_rediscovers_and_fetches_replacement(self) -> None:
        resolver = FakeResolver([discovery(KEY_ONE), discovery(KEY_TWO)])
        api = FakeApi(
            [
                FetchResult(None, error="HTTP 401", status_code=401),
                FetchResult(None, status_code=200),
            ]
        )
        controller = BalanceController(api_client=api, resolver=resolver)
        self.addCleanup(controller.close)

        controller.start()
        events = take(controller, 4)

        self.assertEqual(
            [event.kind for event in events],
            [
                ControllerEventKind.DISCOVERY,
                ControllerEventKind.FETCH,
                ControllerEventKind.DISCOVERY,
                ControllerEventKind.FETCH,
            ],
        )
        self.assertEqual(resolver.calls, [frozenset(), frozenset({KEY_ONE})])
        self.assertEqual(api.keys, [KEY_ONE, KEY_TWO])
        self.assertNotIn(KEY_ONE, repr(events))
        self.assertNotIn(KEY_TWO, repr(events))

    def test_runtime_429_does_not_exclude_or_rediscover(self) -> None:
        resolver = FakeResolver([discovery(KEY_ONE)])
        api = FakeApi(
            [
                FetchResult(None, error="HTTP 429", status_code=429),
                FetchResult(None, status_code=200),
            ]
        )
        controller = BalanceController(api_client=api, resolver=resolver)
        self.addCleanup(controller.close)

        controller.start()
        take(controller, 2)
        controller.request_refresh()
        event = take(controller, 1)[0]

        self.assertEqual(event.kind, ControllerEventKind.FETCH)
        self.assertEqual(resolver.calls, [frozenset()])
        self.assertEqual(api.keys, [KEY_ONE, KEY_ONE])

    def test_slow_fetch_coalesces_refresh_requests_to_one_pending_command(self) -> None:
        first_entered = threading.Event()
        first_release = threading.Event()
        second_entered = threading.Event()
        second_release = threading.Event()

        class BlockingApi:
            def __init__(self) -> None:
                self.keys: list[str] = []

            def fetch_balance(self, key: str) -> FetchResult:
                self.keys.append(key)
                if len(self.keys) == 1:
                    first_entered.set()
                    if not first_release.wait(2):
                        raise AssertionError("test did not release first fetch")
                elif len(self.keys) == 2:
                    second_entered.set()
                    if not second_release.wait(2):
                        raise AssertionError("test did not release second fetch")
                else:
                    raise AssertionError("refresh requests were not coalesced")
                return FetchResult(None, status_code=200)

        api = BlockingApi()
        controller = BalanceController(
            api_client=api,
            resolver=FakeResolver([discovery(KEY_ONE)]),
        )
        controller.start()
        try:
            self.assertTrue(first_entered.wait(2))
            for _ in range(1_000):
                controller.request_refresh()

            self.assertEqual(controller._commands.maxsize, 1)
            self.assertEqual(controller._commands.qsize(), 1)
            first_release.set()
            self.assertTrue(second_entered.wait(2))
            self.assertEqual(controller._commands.qsize(), 0)
            second_release.set()
            take(controller, 3)
            controller.close(timeout=None)
            controller.close(timeout=None)

            self.assertEqual(api.keys, [KEY_ONE, KEY_ONE])
            self.assertFalse(controller._thread.is_alive())
        finally:
            controller.close(timeout=0)
            first_release.set()
            second_release.set()
            if controller._thread is not None:
                controller._thread.join(2)

    def test_close_does_not_block_when_refresh_queue_is_full(self) -> None:
        fetch_entered = threading.Event()
        fetch_release = threading.Event()
        close_returned = threading.Event()

        class BlockingApi:
            def fetch_balance(self, key: str) -> FetchResult:
                fetch_entered.set()
                if not fetch_release.wait(2):
                    raise AssertionError("test did not release fetch")
                return FetchResult(None, status_code=200)

        controller = BalanceController(
            api_client=BlockingApi(),
            resolver=FakeResolver([discovery(KEY_ONE)]),
        )
        controller._commands = Queue(maxsize=1)
        controller.start()
        self.assertTrue(fetch_entered.wait(2))
        controller.request_refresh()

        close_thread = threading.Thread(
            target=lambda: (controller.close(timeout=0), close_returned.set()),
            daemon=True,
        )
        close_thread.start()
        returned_while_full = close_returned.wait(0.2)
        if not returned_while_full:
            controller._commands.get_nowait()
        fetch_release.set()
        close_thread.join(2)
        if controller._thread is not None:
            controller._thread.join(2)

        self.assertTrue(returned_while_full)
        self.assertTrue(close_returned.is_set())

    def test_updates_are_immutable_and_do_not_expose_current_key(self) -> None:
        resolver = FakeResolver([discovery(KEY_ONE)])
        api = FakeApi([FetchResult(None, error="temporary", status_code=503)])
        controller = BalanceController(api_client=api, resolver=resolver)
        self.addCleanup(controller.close)

        controller.start()
        event = take(controller, 2)[0]

        with self.assertRaises(FrozenInstanceError):
            event.verified = False
        self.assertNotIn(KEY_ONE, repr(event))
        self.assertNotIn(KEY_ONE, repr(controller))

    def test_worker_failure_publishes_only_generic_error(self) -> None:
        class BrokenResolver:
            def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
                raise RuntimeError(KEY_ONE)

        controller = BalanceController(
            api_client=FakeApi([]),
            resolver=BrokenResolver(),
        )
        self.addCleanup(controller.close)

        controller.start()
        event = take(controller, 1)[0]

        self.assertEqual(event.kind, ControllerEventKind.ERROR)
        self.assertNotIn(KEY_ONE, repr(event))
        self.assertNotIn(KEY_ONE, event.message or "")

    def test_fetch_error_cannot_forward_current_key_to_update_queue(self) -> None:
        resolver = FakeResolver([discovery(KEY_ONE)])
        api = FakeApi(
            [FetchResult(None, error=f"transport rejected {KEY_ONE}", status_code=503)]
        )
        controller = BalanceController(api_client=api, resolver=resolver)
        self.addCleanup(controller.close)

        controller.start()
        fetch_event = take(controller, 2)[1]

        self.assertNotIn(KEY_ONE, repr(fetch_event))
        self.assertNotIn(KEY_ONE, fetch_event.fetch.error or "")

    def test_close_during_startup_discovery_prevents_fetch_and_updates(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingResolver:
            def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test did not release discovery")
                return discovery(KEY_ONE)

        api = FakeApi([FetchResult(None, status_code=200)])
        controller = BalanceController(api_client=api, resolver=BlockingResolver())

        controller.start()
        self.assertTrue(entered.wait(2))
        controller.close(timeout=0)
        release.set()
        self.assertIsNotNone(controller._thread)
        controller._thread.join(2)

        self.assertFalse(controller._thread.is_alive())
        self.assertEqual(api.keys, [])
        self.assertTrue(controller.updates.empty())

    def test_worker_observes_preexisting_close_before_discovery(self) -> None:
        resolver = FakeResolver([discovery(KEY_ONE)])
        api = FakeApi([FetchResult(None, status_code=200)])
        controller = BalanceController(api_client=api, resolver=resolver)

        controller.close()
        controller._worker()

        self.assertEqual(resolver.calls, [])
        self.assertEqual(api.keys, [])
        self.assertTrue(controller.updates.empty())

    def test_close_during_401_fetch_prevents_rediscovery_and_followup_fetch(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingApi:
            def __init__(self) -> None:
                self.keys: list[str] = []

            def fetch_balance(self, key: str) -> FetchResult:
                self.keys.append(key)
                if len(self.keys) > 1:
                    raise AssertionError("follow-up fetch occurred after close")
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test did not release fetch")
                return FetchResult(None, error="HTTP 401", status_code=401)

        resolver = FakeResolver([discovery(KEY_ONE), discovery(KEY_TWO)])
        api = BlockingApi()
        controller = BalanceController(api_client=api, resolver=resolver)

        controller.start()
        self.assertTrue(entered.wait(2))
        controller.close(timeout=0)
        release.set()
        self.assertIsNotNone(controller._thread)
        controller._thread.join(2)

        self.assertFalse(controller._thread.is_alive())
        self.assertEqual(resolver.calls, [frozenset()])
        self.assertEqual(api.keys, [KEY_ONE])
        queued = []
        while not controller.updates.empty():
            queued.append(controller.updates.get_nowait())
        self.assertEqual([event.kind for event in queued], [ControllerEventKind.DISCOVERY])


if __name__ == "__main__":
    unittest.main()
