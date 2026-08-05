from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import deepseek_balance_monitor.app as app_module
from deepseek_balance_monitor.app import (
    BubbleApp,
    ESTIMATED_DECREASE_LABEL,
    GuiBootstrap,
    ManualDecision,
    RapidClickTracker,
    UsageRecorder,
    UsageRecorderEvent,
    _ManualRecoveryResolver,
    build_balance_display,
    format_estimated_decrease,
    run_app,
    show_manual_key_dialog,
    update_usage,
    verify_manual_credential,
)
from deepseek_balance_monitor.controller import (
    BalanceController,
    ControllerEventKind,
    ControllerUpdate,
)
from deepseek_balance_monitor.canvas_ui import PanelState
from deepseek_balance_monitor.credentials import CredentialCandidate, DiscoveryResult
from deepseek_balance_monitor.models import (
    BalanceInfo,
    BalanceSnapshot,
    FetchResult,
    ProbeResult,
    ProbeStatus,
)
from deepseek_balance_monitor.storage import (
    AppConfig,
    UsageBaseline,
    UsageLog,
    UsageRecord,
)
from deepseek_balance_monitor.settings import SettingsEvent


SECRET = "sk-ui-synthetic-secret"


def info(currency: str, total: str, topped: str | None = None) -> BalanceInfo:
    topped_balance = total if topped is None else topped
    return BalanceInfo(currency, Decimal(total), Decimal("0"), Decimal(topped_balance))


class FakeProbeApi:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result
        self.keys: list[str] = []

    def probe_key(self, key: str) -> ProbeResult:
        self.keys.append(key)
        return self.result


class FakeStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.writes: list[str] = []

    def write_dedicated(self, key: str) -> None:
        self.writes.append(key)
        if self.fail:
            raise OSError(SECRET)


class ThreadAwareApi:
    def __init__(self, results: list[ProbeResult]) -> None:
        self.results = list(results)
        self.keys: list[str] = []
        self.thread_ids: list[int] = []

    def probe_key(self, key: str) -> ProbeResult:
        self.keys.append(key)
        self.thread_ids.append(threading.get_ident())
        return self.results.pop(0)


class ThreadAwareStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.thread_ids: list[int] = []

    def write_dedicated(self, key: str) -> None:
        self.thread_ids.append(threading.get_ident())
        super().write_dedicated(key)


class FakeResolver:
    def __init__(self, result: DiscoveryResult) -> None:
        self.result = result
        self.calls: list[frozenset[str]] = []
        self.thread_ids: list[int] = []

    def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
        self.calls.append(excluded)
        self.thread_ids.append(threading.get_ident())
        return self.result


class FakeRoot:
    def __init__(self) -> None:
        self.callbacks: list[object] = []
        self.after_thread_ids: list[int] = []

    def after(self, delay_ms: int, callback: object) -> str:
        self.after_thread_ids.append(threading.get_ident())
        self.callbacks.append(callback)
        return f"after-{len(self.callbacks)}"

    def pump_until(self, predicate: object, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if self.callbacks:
                callback = self.callbacks.pop(0)
                callback()
            else:
                time.sleep(0.005)
        self.fail_timeout(predicate)

    @staticmethod
    def fail_timeout(predicate: object) -> None:
        raise AssertionError(f"timed out waiting for {predicate!r}")


class BalanceDisplayTests(unittest.TestCase):
    def test_preferred_currency_and_known_symbols_are_displayed_without_sum(self) -> None:
        snapshot = BalanceSnapshot(
            True,
            (info("USD", "3.25"), info("CNY", "8.50", topped="4.25")),
        )

        cny = build_balance_display(snapshot, "CNY")
        usd = build_balance_display(snapshot, "USD")

        self.assertEqual((cny.currency, cny.balance_text), ("CNY", "¥8.50"))
        self.assertEqual((usd.currency, usd.balance_text), ("USD", "$3.25"))
        self.assertEqual(cny.topped_up_balance, Decimal("4.25"))
        self.assertEqual(cny.topped_up_text, "¥4.25")
        self.assertTrue(cny.available)
        self.assertNotIn("11.75", repr((cny, usd)))

    def test_unknown_currency_displays_code_before_amount(self) -> None:
        display = build_balance_display(BalanceSnapshot(True, (info("EUR", "4"),)), "CNY")

        self.assertEqual(display.balance_text, "EUR 4.00")

    def test_ambiguous_multi_currency_without_preference_is_rejected(self) -> None:
        snapshot = BalanceSnapshot(True, (info("USD", "3"), info("EUR", "4")))

        with self.assertRaisesRegex(ValueError, "首选币种"):
            build_balance_display(snapshot, "CNY")

    def test_estimated_decrease_wording_is_explicit_and_currency_aware(self) -> None:
        self.assertEqual(ESTIMATED_DECREASE_LABEL, "今日余额减少（估算）")
        self.assertEqual(
            format_estimated_decrease("CNY", Decimal("2.5")),
            "今日余额减少（估算）：¥2.50",
        )
        self.assertEqual(
            format_estimated_decrease("EUR", Decimal("2.5")),
            "今日余额减少（估算）：EUR 2.50",
        )


class UsageCalculationTests(unittest.TestCase):
    def test_history_retention_limit_is_exported(self) -> None:
        self.assertEqual(
            getattr(app_module, "HISTORY_RETENTION_LIMIT", None),
            10_000,
        )

    def test_update_usage_discards_oldest_records_over_retention_limit(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        records = tuple(
            UsageRecord(str(index), "USD", Decimal(index))
            for index in range(app_module.HISTORY_RETENTION_LIMIT)
        )
        log = UsageLog(
            {"USD": UsageBaseline(when.date(), Decimal("20000"))},
            records,
        )

        result = update_usage(log, "USD", Decimal("10000"), when)

        self.assertEqual(len(result.log.records), app_module.HISTORY_RETENTION_LIMIT)
        self.assertEqual(result.log.records[0].time, "1")
        self.assertEqual(result.log.records[-1].time, when.isoformat())

    def test_adjacent_equal_sample_keeps_history_but_returns_current_estimate(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        existing = UsageRecord("earlier", "USD", Decimal("7"))
        log = UsageLog(
            {"USD": UsageBaseline(when.date(), Decimal("10"))},
            (existing,),
        )

        result = update_usage(log, "usd", Decimal("7.0"), when)

        self.assertEqual(result.estimated_decrease, Decimal("3"))
        self.assertEqual(result.log.records, (existing,))

    def test_daily_baselines_are_isolated_by_currency(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        initial = UsageLog(
            {
                "USD": UsageBaseline(when.date(), Decimal("10")),
                "CNY": UsageBaseline(when.date(), Decimal("20")),
            },
            (),
        )

        usd = update_usage(initial, "USD", Decimal("7"), when)
        cny = update_usage(usd.log, "CNY", Decimal("19"), when)

        self.assertEqual(usd.estimated_decrease, Decimal("3"))
        self.assertEqual(cny.estimated_decrease, Decimal("1"))
        self.assertEqual(cny.log.baselines["USD"].total_balance, Decimal("10"))
        self.assertEqual(cny.log.baselines["CNY"].total_balance, Decimal("20"))
        self.assertEqual([record.currency for record in cny.log.records], ["USD", "CNY"])

    def test_new_day_resets_only_the_current_currency_baseline(self) -> None:
        old = datetime(2026, 8, 2, 23, 59, tzinfo=timezone.utc)
        current = datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc)
        log = UsageLog(
            {
                "USD": UsageBaseline(old.date(), Decimal("10")),
                "CNY": UsageBaseline(old.date(), Decimal("20")),
            },
            (),
        )

        result = update_usage(log, "USD", Decimal("9"), current)

        self.assertEqual(result.estimated_decrease, Decimal("0"))
        self.assertEqual(result.log.baselines["USD"], UsageBaseline(current.date(), Decimal("9")))
        self.assertEqual(result.log.baselines["CNY"], log.baselines["CNY"])

    def test_balance_top_up_never_produces_negative_estimate(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        log = UsageLog({"USD": UsageBaseline(when.date(), Decimal("10"))}, ())

        result = update_usage(log, "USD", Decimal("12"), when)

        self.assertEqual(result.estimated_decrease, Decimal("0"))


class ManualCredentialDecisionTests(unittest.TestCase):
    def test_valid_key_can_remain_memory_only(self) -> None:
        store = FakeStore()

        outcome = verify_manual_credential(
            FakeProbeApi(ProbeResult(ProbeStatus.VALID)), store, SECRET, persist=False
        )

        self.assertEqual(outcome.decision, ManualDecision.ACCEPTED_VERIFIED)
        self.assertTrue(outcome.discovery.verified)
        self.assertEqual(outcome.discovery.candidate.key, SECRET)
        self.assertEqual(store.writes, [])

    def test_explicit_persistence_requires_verified_or_authenticated_status(self) -> None:
        for status in (ProbeStatus.VALID, ProbeStatus.AUTHENTICATED_NO_BALANCE):
            with self.subTest(status=status):
                store = FakeStore()
                outcome = verify_manual_credential(
                    FakeProbeApi(ProbeResult(status)), store, SECRET, persist=True
                )

                self.assertEqual(outcome.decision, ManualDecision.ACCEPTED_VERIFIED)
                self.assertTrue(outcome.persisted)
                self.assertEqual(store.writes, [SECRET])

    def test_indeterminate_key_is_memory_only_even_when_persistence_was_requested(self) -> None:
        store = FakeStore()

        outcome = verify_manual_credential(
            FakeProbeApi(ProbeResult(ProbeStatus.INDETERMINATE, error=SECRET)),
            store,
            SECRET,
            persist=True,
        )

        self.assertEqual(outcome.decision, ManualDecision.ACCEPTED_UNVERIFIED)
        self.assertFalse(outcome.persisted)
        self.assertFalse(outcome.discovery.verified)
        self.assertEqual(store.writes, [])
        self.assertNotIn(SECRET, outcome.message)

    def test_invalid_key_is_rejected_and_never_persisted(self) -> None:
        store = FakeStore()

        outcome = verify_manual_credential(
            FakeProbeApi(ProbeResult(ProbeStatus.INVALID)), store, SECRET, persist=True
        )

        self.assertEqual(outcome.decision, ManualDecision.REJECTED)
        self.assertIsNone(outcome.discovery)
        self.assertEqual(store.writes, [])

    def test_persistence_failure_falls_back_to_memory_without_secret_diagnostic(self) -> None:
        outcome = verify_manual_credential(
            FakeProbeApi(ProbeResult(ProbeStatus.VALID)),
            FakeStore(fail=True),
            SECRET,
            persist=True,
        )

        self.assertEqual(outcome.decision, ManualDecision.ACCEPTED_VERIFIED)
        self.assertFalse(outcome.persisted)
        self.assertIn("内存", outcome.message)
        self.assertNotIn(SECRET, repr(outcome))
        self.assertNotIn(SECRET, outcome.message)


class RapidClickTests(unittest.TestCase):
    def test_second_click_at_400ms_is_inclusive_but_401ms_is_not(self) -> None:
        tracker = RapidClickTracker()

        self.assertFalse(tracker.register(1_000))
        self.assertTrue(tracker.register(1_400))
        self.assertFalse(tracker.register(2_000))
        self.assertFalse(tracker.register(2_401))


class GuiBootstrapTests(unittest.TestCase):
    def _bootstrap(
        self,
        *,
        resolver: object,
        api: object,
        store: object,
        prompts: list[tuple[str, bool] | None],
    ) -> tuple[GuiBootstrap, FakeRoot, list[DiscoveryResult], list[str], list[int], list[int]]:
        root = FakeRoot()
        ready: list[DiscoveryResult] = []
        notices: list[str] = []
        prompt_threads: list[int] = []
        ready_threads: list[int] = []

        def prompt_manual() -> tuple[str, bool] | None:
            prompt_threads.append(threading.get_ident())
            return prompts.pop(0)

        bootstrap = GuiBootstrap(
            root,
            resolver=resolver,
            api_client=api,
            store=store,
            prompt_manual=prompt_manual,
            show_error=notices.append,
            show_notice=notices.append,
            on_ready=lambda result: (
                ready_threads.append(threading.get_ident()),
                ready.append(result),
            ),
            on_cancel=lambda: notices.append("cancelled"),
        )
        self.addCleanup(bootstrap.close)
        return bootstrap, root, ready, notices, prompt_threads, ready_threads

    def test_normal_discovery_runs_once_off_main_thread_and_ready_is_main_thread(self) -> None:
        discovery = DiscoveryResult(
            CredentialCandidate("environment", SECRET),
            ProbeResult(ProbeStatus.VALID),
            True,
        )
        resolver = FakeResolver(discovery)
        main_thread = threading.get_ident()
        bootstrap, root, ready, _, prompts, ready_threads = self._bootstrap(
            resolver=resolver,
            api=ThreadAwareApi([]),
            store=ThreadAwareStore(),
            prompts=[],
        )

        bootstrap.start()
        root.pump_until(lambda: bool(ready))

        self.assertEqual(resolver.calls, [frozenset()])
        self.assertNotEqual(resolver.thread_ids, [main_thread])
        self.assertEqual(prompts, [])
        self.assertEqual(ready_threads, [main_thread])
        self.assertTrue(all(thread_id == main_thread for thread_id in root.after_thread_ids))

    def test_invalid_manual_key_is_rejected_then_dialog_is_reopened(self) -> None:
        api = ThreadAwareApi(
            [ProbeResult(ProbeStatus.INVALID), ProbeResult(ProbeStatus.VALID)]
        )
        bootstrap, root, ready, notices, prompt_threads, _ = self._bootstrap(
            resolver=FakeResolver(DiscoveryResult()),
            api=api,
            store=ThreadAwareStore(),
            prompts=[(SECRET, True), (SECRET, False)],
        )
        main_thread = threading.get_ident()

        bootstrap.start()
        root.pump_until(lambda: bool(ready))

        self.assertEqual(len(prompt_threads), 2)
        self.assertTrue(all(thread_id == main_thread for thread_id in prompt_threads))
        self.assertTrue(any("无效" in message for message in notices))
        self.assertTrue(ready[0].verified)
        self.assertTrue(all(thread_id != main_thread for thread_id in api.thread_ids))

    def test_verified_manual_persistence_runs_on_worker_thread(self) -> None:
        api = ThreadAwareApi([ProbeResult(ProbeStatus.AUTHENTICATED_NO_BALANCE)])
        store = ThreadAwareStore()
        bootstrap, root, ready, _, _, _ = self._bootstrap(
            resolver=FakeResolver(DiscoveryResult()),
            api=api,
            store=store,
            prompts=[(SECRET, True)],
        )
        main_thread = threading.get_ident()

        bootstrap.start()
        root.pump_until(lambda: bool(ready))

        self.assertEqual(store.writes, [SECRET])
        self.assertEqual(len(store.thread_ids), 1)
        self.assertNotEqual(store.thread_ids[0], main_thread)
        self.assertTrue(ready[0].verified)

    def test_manual_only_start_skips_normal_discovery_and_keeps_probe_off_main(self) -> None:
        api = ThreadAwareApi([ProbeResult(ProbeStatus.VALID)])
        store = ThreadAwareStore()
        resolver = FakeResolver(DiscoveryResult())
        bootstrap, root, ready, _, prompt_threads, _ = self._bootstrap(
            resolver=resolver,
            api=api,
            store=store,
            prompts=[(SECRET, True)],
        )
        main_thread = threading.get_ident()

        bootstrap.start_manual()
        root.pump_until(lambda: bool(ready))

        self.assertEqual(resolver.calls, [])
        self.assertEqual(prompt_threads, [main_thread])
        self.assertTrue(all(thread_id != main_thread for thread_id in api.thread_ids))
        self.assertTrue(all(thread_id != main_thread for thread_id in store.thread_ids))

    def test_indeterminate_manual_key_never_persists_and_shows_notice(self) -> None:
        api = ThreadAwareApi([ProbeResult(ProbeStatus.INDETERMINATE, error=SECRET)])
        store = ThreadAwareStore()
        bootstrap, root, ready, notices, _, _ = self._bootstrap(
            resolver=FakeResolver(DiscoveryResult()),
            api=api,
            store=store,
            prompts=[(SECRET, True)],
        )

        bootstrap.start()
        root.pump_until(lambda: bool(ready))

        self.assertFalse(ready[0].verified)
        self.assertEqual(store.writes, [])
        self.assertTrue(any("内存" in message for message in notices))
        self.assertNotIn(SECRET, repr(notices))

    def test_manual_cancel_finishes_safely_on_main_thread(self) -> None:
        bootstrap, root, ready, notices, prompt_threads, _ = self._bootstrap(
            resolver=FakeResolver(DiscoveryResult()),
            api=ThreadAwareApi([]),
            store=ThreadAwareStore(),
            prompts=[None],
        )
        main_thread = threading.get_ident()

        bootstrap.start()
        root.pump_until(lambda: "cancelled" in notices)

        self.assertEqual(ready, [])
        self.assertEqual(prompt_threads, [main_thread])

    def test_close_during_blocked_discovery_drops_late_result(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingResolver:
            def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
                entered.set()
                release.wait(2)
                return DiscoveryResult(
                    CredentialCandidate("environment", SECRET),
                    ProbeResult(ProbeStatus.VALID),
                    True,
                )

        bootstrap, root, ready, _, _, _ = self._bootstrap(
            resolver=BlockingResolver(),
            api=ThreadAwareApi([]),
            store=ThreadAwareStore(),
            prompts=[],
        )

        bootstrap.start()
        self.assertTrue(entered.wait(2))
        bootstrap.close()
        release.set()
        time.sleep(0.03)
        for callback in list(root.callbacks):
            callback()

        self.assertEqual(ready, [])

    def test_ready_discovery_can_seed_controller_without_second_discovery(self) -> None:
        discovery = DiscoveryResult(
            CredentialCandidate("environment", SECRET),
            ProbeResult(ProbeStatus.VALID),
            True,
        )
        resolver = FakeResolver(discovery)
        controllers: list[BalanceController] = []

        class FetchApi:
            def fetch_balance(self, key: str) -> FetchResult:
                return FetchResult(None, status_code=200)

        root = FakeRoot()

        def on_ready(result: DiscoveryResult) -> None:
            controller = BalanceController(
                api_client=FetchApi(),
                resolver=resolver,
                initial_discovery=result,
            )
            controllers.append(controller)
            controller.start()

        bootstrap = GuiBootstrap(
            root,
            resolver=resolver,
            api_client=ThreadAwareApi([]),
            store=ThreadAwareStore(),
            prompt_manual=lambda: None,
            show_error=lambda message: None,
            show_notice=lambda message: None,
            on_ready=on_ready,
            on_cancel=lambda: None,
        )
        self.addCleanup(bootstrap.close)

        bootstrap.start()
        root.pump_until(lambda: bool(controllers))
        self.addCleanup(controllers[0].close)
        deadline = time.monotonic() + 2
        while controllers[0].updates.qsize() < 2 and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertEqual(resolver.calls, [frozenset()])
        self.assertEqual(controllers[0].updates.qsize(), 2)


class ManualRecoveryResolverTests(unittest.TestCase):
    def test_runtime_manual_controller_cannot_read_normal_credential_sources(self) -> None:
        resolver = _ManualRecoveryResolver()

        first = resolver.discover(frozenset())
        second = resolver.discover(frozenset({SECRET}))

        self.assertIsNone(first.candidate)
        self.assertIsNone(second.candidate)
        self.assertEqual(first.diagnostics, ())


class _FakeVariable:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.history = [value]

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value
        self.history.append(value)


class _FakeWidget:
    def __init__(self, owner: object, kind: str, **options: object) -> None:
        self.owner = owner
        self.kind = kind
        self.options = options
        self.destroyed = False
        self.bindings: dict[str, object] = {}

    def grid(self, **options: object) -> None:
        pass

    def pack(self, **options: object) -> None:
        pass

    def pack_forget(self) -> None:
        pass

    def place(self, **options: object) -> None:
        pass

    def bind(self, event: str, callback: object) -> None:
        self.bindings[event] = callback

    def focus_set(self) -> None:
        pass

    def destroy(self) -> None:
        self.destroyed = True

    def transient(self, root: object) -> None:
        self.options.setdefault("transient_owners", []).append(root)

    def grab_set(self) -> None:
        pass

    def protocol(self, name: str, command: object) -> None:
        self.options[name] = command

    def title(self, value: str) -> None:
        self.options["title"] = value

    def resizable(self, width: bool, height: bool) -> None:
        pass

    def columnconfigure(self, index: int, weight: int) -> None:
        pass


class FakeTkModule:
    def __init__(self) -> None:
        self.variables: list[_FakeVariable] = []
        self.widgets: list[_FakeWidget] = []

    def StringVar(self, master: object = None, value: str = "") -> _FakeVariable:
        variable = _FakeVariable(value)
        self.variables.append(variable)
        return variable

    def BooleanVar(self, master: object = None, value: bool = False) -> _FakeVariable:
        variable = _FakeVariable(value)
        self.variables.append(variable)
        return variable

    def _widget(self, kind: str, master: object = None, **options: object) -> _FakeWidget:
        widget = _FakeWidget(self, kind, **options)
        self.widgets.append(widget)
        return widget

    def Toplevel(self, master: object = None, **options: object) -> _FakeWidget:
        return self._widget("Toplevel", master, **options)

    def Frame(self, master: object = None, **options: object) -> _FakeWidget:
        return self._widget("Frame", master, **options)

    def Label(self, master: object = None, **options: object) -> _FakeWidget:
        return self._widget("Label", master, **options)

    def Entry(self, master: object = None, **options: object) -> _FakeWidget:
        return self._widget("Entry", master, **options)

    def Checkbutton(self, master: object = None, **options: object) -> _FakeWidget:
        return self._widget("Checkbutton", master, **options)

    def Button(self, master: object = None, **options: object) -> _FakeWidget:
        return self._widget("Button", master, **options)


class DialogRoot:
    def __init__(self, module: FakeTkModule, *, state: str = "normal") -> None:
        self.module = module
        self.state_value = state

    def state(self) -> str:
        return self.state_value

    def wait_window(self, window: object) -> None:
        key_variable, persist_variable, _ = self.module.variables
        self.persist_default = persist_variable.get()
        key_variable.set(SECRET)
        submit = next(
            widget.options["command"]
            for widget in self.module.widgets
            if widget.kind == "Button" and widget.options.get("text") == "使用"
        )
        submit()


class ManualDialogTests(unittest.TestCase):
    def test_entry_is_masked_persistence_defaults_false_and_secret_variable_is_cleared(self) -> None:
        module = FakeTkModule()
        root = DialogRoot(module)

        result = show_manual_key_dialog(root, tk_module=module)

        entry = next(widget for widget in module.widgets if widget.kind == "Entry")
        self.assertEqual(entry.options["show"], "*")
        self.assertFalse(root.persist_default)
        self.assertEqual(result, (SECRET, False))
        self.assertEqual(module.variables[0].get(), "")
        self.assertEqual(module.variables[0].history[-1], "")
        window = next(widget for widget in module.widgets if widget.kind == "Toplevel")
        self.assertEqual(window.options["transient_owners"], [root])

    def test_withdrawn_root_is_not_assigned_as_transient_owner(self) -> None:
        module = FakeTkModule()
        root = DialogRoot(module, state="withdrawn")

        result = show_manual_key_dialog(root, tk_module=module)

        window = next(widget for widget in module.widgets if widget.kind == "Toplevel")
        self.assertEqual(result, (SECRET, False))
        self.assertNotIn("transient_owners", window.options)


class UsageRecorderTests(unittest.TestCase):
    def test_load_and_atomic_save_are_worker_only_and_publish_currency_scoped_result(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        baseline = UsageLog({"USD": UsageBaseline(when.date(), Decimal("10"))}, ())
        call_threads: list[int] = []
        saved: list[UsageLog] = []

        def load_log(path: Path) -> UsageLog:
            call_threads.append(threading.get_ident())
            return baseline

        def save_log(path: Path, log: UsageLog) -> None:
            call_threads.append(threading.get_ident())
            saved.append(log)

        recorder = UsageRecorder(
            Path("synthetic-usage.json"),
            load_log=load_log,
            save_log=save_log,
            now=lambda: when,
        )
        self.addCleanup(recorder.close)
        main_thread = threading.get_ident()

        recorder.start()
        recorder.record("USD", Decimal("7"))
        event = recorder.updates.get(timeout=2)

        self.assertTrue(call_threads)
        self.assertTrue(all(thread_id != main_thread for thread_id in call_threads))
        self.assertEqual(event.currency, "USD")
        self.assertEqual(event.estimated_decrease, Decimal("3"))
        self.assertEqual(saved[0].baselines["USD"].total_balance, Decimal("10"))

    def test_loads_once_and_failed_save_does_not_advance_in_memory_log(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        baseline = UsageLog({"USD": UsageBaseline(when.date(), Decimal("10"))}, ())
        load_calls = 0
        save_attempts: list[UsageLog] = []

        def load_log(path: Path) -> UsageLog:
            nonlocal load_calls
            load_calls += 1
            return baseline

        def save_log(path: Path, log: UsageLog) -> None:
            save_attempts.append(log)
            if len(save_attempts) == 1:
                raise OSError("synthetic save failure")

        recorder = UsageRecorder(
            Path("synthetic-usage.json"),
            load_log=load_log,
            save_log=save_log,
            now=lambda: when,
        )
        self.addCleanup(recorder.close)
        recorder.start()

        recorder.record("USD", Decimal("8"))
        failed = recorder.updates.get(timeout=2)
        recorder.record("USD", Decimal("7"))
        succeeded = recorder.updates.get(timeout=2)
        recorder.record("USD", Decimal("6"))
        recorder.updates.get(timeout=2)

        self.assertEqual(failed.message, "历史记录更新失败。")
        self.assertEqual(succeeded.estimated_decrease, Decimal("3"))
        self.assertEqual(load_calls, 1)
        self.assertEqual(
            [record.total_balance for record in save_attempts[1].records],
            [Decimal("7")],
        )
        self.assertEqual(
            [record.total_balance for record in save_attempts[2].records],
            [Decimal("7"), Decimal("6")],
        )

    def test_pending_samples_are_bounded_and_latest_sample_wins(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        baseline = UsageLog({"USD": UsageBaseline(when.date(), Decimal("2000"))}, ())
        save_entered = threading.Event()
        save_release = threading.Event()
        saved: list[UsageLog] = []

        def save_log(path: Path, log: UsageLog) -> None:
            saved.append(log)
            if len(saved) == 1:
                save_entered.set()
                if not save_release.wait(2):
                    raise AssertionError("test did not release save")

        recorder = UsageRecorder(
            Path("synthetic-usage.json"),
            load_log=lambda path: baseline,
            save_log=save_log,
            now=lambda: when,
        )
        recorder.start()
        try:
            recorder.record("USD", Decimal("1500"))
            self.assertTrue(save_entered.wait(2))
            for balance in range(1_000):
                recorder.record("USD", Decimal(balance))

            self.assertEqual(recorder._commands.maxsize, 1)
            self.assertEqual(recorder._commands.qsize(), 1)
            save_release.set()
            recorder.updates.get(timeout=2)
            recorder.updates.get(timeout=2)
            recorder.close(timeout=None)

            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[-1].records[-1].total_balance, Decimal("999"))
        finally:
            recorder.close(timeout=0)
            save_release.set()
            if recorder._thread is not None:
                recorder._thread.join(2)

    def test_close_waits_until_accepted_pending_sample_is_saved(self) -> None:
        when = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
        save_entered = threading.Event()
        save_release = threading.Event()
        close_returned = threading.Event()
        saved: list[UsageLog] = []

        def save_log(path: Path, log: UsageLog) -> None:
            saved.append(log)
            if len(saved) == 1:
                save_entered.set()
                if not save_release.wait(2):
                    raise AssertionError("test did not release save")

        recorder = UsageRecorder(
            Path("synthetic-usage.json"),
            load_log=lambda path: UsageLog(),
            save_log=save_log,
            now=lambda: when,
        )
        recorder.start()
        recorder.record("USD", Decimal("8"))
        self.assertTrue(save_entered.wait(2))
        recorder.record("USD", Decimal("7"))

        close_thread = threading.Thread(
            target=lambda: (recorder.close(timeout=None), close_returned.set()),
            daemon=True,
        )
        close_thread.start()
        returned_before_release = close_returned.wait(0.2)
        save_release.set()
        close_thread.join(2)
        recorder.close(timeout=None)

        self.assertFalse(returned_before_release)
        self.assertTrue(close_returned.is_set())
        self.assertEqual(
            [log.records[-1].total_balance for log in saved],
            [Decimal("8"), Decimal("7")],
        )


class RuntimeShutdownTests(unittest.TestCase):
    def test_replaced_runtime_workers_are_joined_during_final_shutdown(self) -> None:
        calls: list[tuple[str, object]] = []

        class Closable:
            def __init__(self, name: str) -> None:
                self._name = name

            def start(self) -> None:
                calls.append((f"{self._name}.start", None))

            def close(self, timeout: float | None = 0.0) -> None:
                calls.append((f"{self._name}.close", timeout))

        bubble = BubbleApp.__new__(BubbleApp)
        bubble._main_thread_id = threading.get_ident()
        bubble._closed = False
        bubble._recovery_active = True
        bubble._recovery = Closable("recovery")
        bubble._controller = Closable("old_controller")
        bubble._history = Closable("history")
        bubble._controller_factory = lambda discovery: Closable("new_controller")
        bubble._retired_recoveries = []
        bubble._retired_controllers = []
        bubble._view_state = PanelState()
        bubble._view = _RecordingView()
        bubble._settings = Closable("settings")

        bubble._replace_controller(DiscoveryResult())
        bubble.wait_for_shutdown()

        self.assertEqual(
            calls,
            [
                ("recovery.close", 0.0),
                ("old_controller.close", 0.2),
                ("new_controller.start", None),
                ("recovery.close", None),
                ("old_controller.close", None),
                ("new_controller.close", None),
                ("history.close", None),
                ("settings.close", None),
            ],
        )

    def test_bubble_wait_for_shutdown_joins_every_owned_worker(self) -> None:
        calls: list[tuple[str, object]] = []

        class Closable:
            def __init__(self, name: str) -> None:
                self._name = name

            def close(self, timeout: float | None = 0.0) -> None:
                calls.append((self._name, timeout))

        bubble = BubbleApp.__new__(BubbleApp)
        bubble._main_thread_id = threading.get_ident()
        bubble._recovery = Closable("recovery")
        bubble._controller = Closable("controller")
        bubble._history = Closable("history")
        bubble._settings = Closable("settings")
        bubble._retired_recoveries = []
        bubble._retired_controllers = []

        bubble.wait_for_shutdown()

        self.assertEqual(
            calls,
            [
                ("recovery", None),
                ("controller", None),
                ("history", None),
                ("settings", None),
            ],
        )

    def test_run_app_waits_for_full_quiescence_after_window_close(self) -> None:
        calls: list[tuple[str, object]] = []

        class FakeRoot:
            def withdraw(self) -> None:
                pass

            def protocol(self, name: str, callback: object) -> None:
                pass

            def deiconify(self) -> None:
                pass

            def destroy(self) -> None:
                pass

            def mainloop(self) -> None:
                pass

        class FakeBootstrap:
            def __init__(self, root: object, **kwargs: object) -> None:
                self._on_ready = kwargs["on_ready"]

            def start(self) -> None:
                self._on_ready(DiscoveryResult())

            def close(self, timeout: float | None = 0.0) -> None:
                calls.append(("bootstrap.close", timeout))

        class FakeBubble:
            def __init__(self, root: object, **kwargs: object) -> None:
                pass

            def start(self) -> None:
                pass

            def request_exit(self) -> None:
                pass

            def close(self) -> None:
                calls.append(("bubble.close", None))

            def wait_for_shutdown(self) -> None:
                calls.append(("bubble.wait_for_shutdown", None))

        fake_tk = ModuleType("tkinter")
        fake_tk.Tk = FakeRoot
        fake_tk.messagebox = SimpleNamespace(
            showerror=lambda *a, **k: None,
            showinfo=lambda *a, **k: None,
            askyesno=lambda *a, **k: False,
        )
        fake_paths = SimpleNamespace(
            config_path=Path("synthetic-config.json"),
            usage_log_path=Path("synthetic-usage.json"),
        )

        with (
            patch.dict(sys.modules, {"tkinter": fake_tk}),
            patch.object(app_module, "ApiClient", return_value=object()),
            patch.object(app_module, "WindowsCredentialStore", return_value=object()),
            patch.object(app_module, "CredentialResolver", return_value=object()),
            patch.object(app_module.AppPaths, "from_environment", return_value=fake_paths),
            patch.object(app_module, "load_config", return_value=AppConfig()),
            patch.object(app_module, "BalanceController", return_value=object()),
            patch.object(app_module, "UsageRecorder", return_value=object()),
            patch.object(app_module, "GuiBootstrap", FakeBootstrap),
            patch.object(app_module, "BubbleApp", FakeBubble),
        ):
            self.assertEqual(run_app(), 0)

        self.assertEqual(
            calls,
            [
                ("bootstrap.close", None),
                ("bubble.close", None),
                ("bubble.wait_for_shutdown", None),
            ],
        )


class _ThreadBoundVariable:
    def __init__(self) -> None:
        self.values: list[str] = []
        self.thread_ids: list[int] = []

    def set(self, value: str) -> None:
        self.values.append(value)
        self.thread_ids.append(threading.get_ident())


class _HistorySink:
    def __init__(self) -> None:
        self.records: list[tuple[str, Decimal]] = []

    def record(self, currency: str, total: Decimal) -> None:
        self.records.append((currency, total))


class _RecordingView:
    def __init__(self) -> None:
        self.renders: list[PanelState] = []
        self.close_calls = 0
        self.autostart_states: list[tuple[bool, bool]] = []

    def render(self, state: PanelState) -> None:
        self.renders.append(state)

    def close(self) -> None:
        self.close_calls += 1

    def set_autostart_state(self, enabled: bool, *, pending: bool = False) -> None:
        self.autostart_states.append((enabled, pending))


class _QueueSettings:
    def __init__(self) -> None:
        self.updates: Queue[SettingsEvent] = Queue()
        self.close_timeouts: list[float | None] = []
        self.saves: list[AppConfig] = []
        self.autostart_requests: list[tuple[bool, AppConfig]] = []

    def request_save(self, config: AppConfig) -> None:
        self.saves.append(config)

    def request_autostart(self, enabled: bool, config: AppConfig) -> None:
        self.autostart_requests.append((enabled, config))

    def close(self, timeout: float | None = 2.0) -> None:
        self.close_timeouts.append(timeout)


class BubbleIntegrationTests(unittest.TestCase):
    def make_bubble(self) -> BubbleApp:
        bubble = object.__new__(BubbleApp)
        bubble._main_thread_id = threading.get_ident()
        bubble._closed = False
        bubble._confirming_exit = False
        bubble._pending_autostart_value = None
        bubble._config = AppConfig(currency="CNY")
        bubble._view_state = PanelState()
        bubble._view = _RecordingView()
        bubble._history = _ClosableHistorySink()
        bubble._controller = _OldController()
        bubble._settings = _QueueSettings()
        bubble._controller_factory = None
        bubble._recovery_factory = None
        bubble._recovery = None
        bubble._retired_recoveries = []
        bubble._retired_controllers = []
        bubble._recovery_active = False
        bubble._poll_timer = None
        bubble._refresh_timer = None
        bubble._root = _DestroyRoot()
        bubble._show_error = lambda _title, _message, **_options: None
        return bubble

    def test_exit_requires_confirmation_once_and_confirmed_close_is_idempotent(self) -> None:
        bubble = self.make_bubble()
        confirm = Mock(side_effect=[False, True])
        bubble._ask_yes_no = confirm

        bubble.request_exit()
        self.assertFalse(bubble._closed)
        bubble.request_exit()
        self.assertTrue(bubble._closed)
        bubble.request_exit()

        self.assertEqual(
            confirm.call_args_list,
            [
                call(
                    "退出确认",
                    "确定要退出 DeepSeek 余额监控吗？",
                    parent=bubble._root,
                ),
                call(
                    "退出确认",
                    "确定要退出 DeepSeek 余额监控吗？",
                    parent=bubble._root,
                ),
            ],
        )
        self.assertEqual(bubble._controller.close_calls, 1)
        self.assertEqual(bubble._view.close_calls, 1)

    def test_constructor_composes_initial_canvas_state_without_widget_variables(self) -> None:
        root = Mock()
        view = _RecordingView()
        bubble = BubbleApp(
            root,
            controller=_OldController(),
            config=AppConfig(window_x=12, window_y=34, auto_start=True),
            history=_ClosableHistorySink(),
            tk_module=SimpleNamespace(),
            view=view,
            ask_yes_no=lambda *_args, **_options: False,
            show_error=lambda *_args, **_options: None,
        )
        self.assertEqual(view.autostart_states, [(True, False)])
        self.assertEqual(view.renders[-1].status_text, "CONNECTING...")
        root.geometry.assert_called_once_with("255x215+12+34")
        self.assertFalse(hasattr(bubble, "_balance_var"))

    def test_multi_currency_fetch_sends_only_preferred_currency_to_view(self) -> None:
        bubble = self.make_bubble()
        snapshot = BalanceSnapshot(
            True,
            (info("USD", "3.25"), info("CNY", "8.50", topped="4.25")),
        )
        bubble.apply_controller_update(
            ControllerUpdate(
                ControllerEventKind.FETCH,
                fetch=FetchResult(snapshot, status_code=200),
            )
        )
        state = bubble._view.renders[-1]
        self.assertEqual(state.display.currency, "CNY")
        self.assertEqual(state.display.total_balance, Decimal("8.50"))
        self.assertEqual(state.display.topped_up_balance, Decimal("4.25"))
        self.assertNotIn("11.75", repr(state))

    def test_history_decrease_is_not_multiplied_during_peak_time(self) -> None:
        bubble = self.make_bubble()
        bubble._view_state = PanelState(display=build_balance_display(
            BalanceSnapshot(True, (info("CNY", "8.50"),)),
            "CNY",
        ))
        bubble._apply_history_event(UsageRecorderEvent("CNY", Decimal("2.50")))
        self.assertEqual(bubble._view.renders[-1].estimated_decrease, Decimal("2.50"))

    def test_autostart_menu_stays_pending_until_worker_event(self) -> None:
        bubble = self.make_bubble()
        bubble._autostart_pending = False
        bubble._set_autostart(True)
        self.assertEqual(
            bubble._settings.autostart_requests,
            [(True, bubble._config)],
        )
        self.assertEqual(bubble._view.autostart_states[-1], (True, True))

        updated = replace(bubble._config, auto_start=True)
        bubble._apply_settings_event(SettingsEvent(updated, startup_changed=True))
        self.assertFalse(bubble._autostart_pending)
        self.assertEqual(bubble._view.autostart_states[-1], (True, False))


class _ClosableHistorySink(_HistorySink):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self, timeout: float = 2.0) -> None:
        self.close_calls += 1


class _OldController:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self, timeout: float = 2.0) -> None:
        self.close_calls += 1


class _RecoveryHarness:
    def __init__(self, ready: object, cancel: object) -> None:
        self.ready = ready
        self.cancel = cancel
        self.start_calls = 0
        self.close_calls = 0

    def start_manual(self) -> None:
        self.start_calls += 1

    def close(self, timeout: float = 0.0) -> None:
        self.close_calls += 1


class _DestroyRoot:
    def __init__(self) -> None:
        self.destroy_calls = 0

    def destroy(self) -> None:
        self.destroy_calls += 1


class _DestroyedAfterRoot(_DestroyRoot):
    def __init__(self) -> None:
        super().__init__()
        self.after_calls = 0

    def after(self, delay_ms: int, callback: object) -> None:
        self.after_calls += 1
        if self.destroy_calls:
            raise RuntimeError("after called after destroy")


class _QueueController(_OldController):
    def __init__(self) -> None:
        super().__init__()
        self.updates: Queue[ControllerUpdate] = Queue()


class _QueueHistory(_ClosableHistorySink):
    def __init__(self) -> None:
        super().__init__()
        self.updates: Queue[object] = Queue()


class _SynchronousCancelRecovery(_RecoveryHarness):
    def start_manual(self) -> None:
        super().start_manual()
        self.cancel()


class BubbleThreadBoundaryTests(unittest.TestCase):
    def _bubble(self) -> BubbleApp:
        bubble = object.__new__(BubbleApp)
        bubble._main_thread_id = threading.get_ident()
        bubble._closed = False
        bubble._config = AppConfig(currency="CNY")
        bubble._view_state = PanelState()
        bubble._view = _RecordingView()
        bubble._history = _HistorySink()
        bubble._settings = _QueueSettings()
        bubble._controller = _OldController()
        bubble._controller_factory = None
        bubble._recovery_factory = None
        bubble._recovery = None
        bubble._retired_recoveries = []
        bubble._retired_controllers = []
        bubble._recovery_active = False
        bubble._poll_timer = None
        bubble._refresh_timer = None
        bubble._show_error = lambda *_args, **_kwargs: None
        return bubble

    def test_controller_result_is_applied_and_history_enqueued_on_main_thread(self) -> None:
        bubble = self._bubble()
        snapshot = BalanceSnapshot(True, (info("CNY", "8.50"),))
        update = ControllerUpdate(
            ControllerEventKind.FETCH,
            fetch=FetchResult(snapshot, status_code=200),
        )

        bubble.apply_controller_update(update)

        self.assertEqual(bubble._view.renders[-1].display.balance_text, "¥8.50")
        self.assertEqual(bubble._history.records, [("CNY", Decimal("8.50"))])

    def test_worker_thread_cannot_mutate_tk_variables(self) -> None:
        bubble = self._bubble()
        update = ControllerUpdate(ControllerEventKind.ERROR, message=SECRET)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                bubble.apply_controller_update(update)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertEqual(bubble._view.renders, [])
        self.assertNotIn(SECRET, repr(errors))

    def test_empty_runtime_discovery_starts_one_manual_recovery_and_seeds_replacement(self) -> None:
        bubble = self._bubble()
        old_controller = bubble._controller
        recoveries: list[_RecoveryHarness] = []
        seeded: list[DiscoveryResult] = []

        class NeverDiscover:
            def __init__(self) -> None:
                self.calls = 0

            def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
                self.calls += 1
                raise AssertionError("normal discovery must not run during recovery")

        class FetchApi:
            def fetch_balance(self, key: str) -> FetchResult:
                return FetchResult(None, status_code=200)

        resolver = NeverDiscover()

        def controller_factory(discovery: DiscoveryResult) -> BalanceController:
            seeded.append(discovery)
            return BalanceController(
                api_client=FetchApi(),
                resolver=resolver,
                initial_discovery=discovery,
            )

        def recovery_factory(ready: object, cancel: object) -> _RecoveryHarness:
            recovery = _RecoveryHarness(ready, cancel)
            recoveries.append(recovery)
            return recovery

        bubble._controller_factory = controller_factory
        bubble._recovery_factory = recovery_factory
        missing = ControllerUpdate(
            ControllerEventKind.DISCOVERY,
            verified=False,
            probe_status=None,
        )

        bubble.apply_controller_update(missing)
        bubble.apply_controller_update(missing)

        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0].start_calls, 1)
        discovery = DiscoveryResult(
            CredentialCandidate("manual", SECRET),
            ProbeResult(ProbeStatus.VALID),
            True,
        )
        recoveries[0].ready(discovery)
        replacement = bubble._controller
        self.addCleanup(replacement.close)
        deadline = time.monotonic() + 2
        while replacement.updates.qsize() < 2 and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertEqual(old_controller.close_calls, 1)
        self.assertEqual(seeded, [discovery])
        self.assertEqual(resolver.calls, 0)
        self.assertEqual(replacement.updates.qsize(), 2)
        self.assertNotIn(SECRET, repr(bubble._view.renders))

    def test_indeterminate_runtime_discovery_does_not_start_manual_recovery(self) -> None:
        bubble = self._bubble()
        created: list[object] = []
        bubble._recovery_factory = lambda ready, cancel: created.append((ready, cancel))

        bubble.apply_controller_update(
            ControllerUpdate(
                ControllerEventKind.DISCOVERY,
                verified=False,
                probe_status=ProbeStatus.INDETERMINATE,
            )
        )

        self.assertEqual(created, [])

    def test_runtime_recovery_cancel_closes_application_once(self) -> None:
        bubble = self._bubble()
        bubble._root = _DestroyRoot()
        bubble._history = _ClosableHistorySink()
        recoveries: list[_RecoveryHarness] = []

        def recovery_factory(ready: object, cancel: object) -> _RecoveryHarness:
            recovery = _RecoveryHarness(ready, cancel)
            recoveries.append(recovery)
            return recovery

        bubble._recovery_factory = recovery_factory
        bubble._controller_factory = lambda discovery: _OldController()
        missing = ControllerUpdate(ControllerEventKind.DISCOVERY)

        bubble.apply_controller_update(missing)
        recoveries[0].cancel()
        recoveries[0].cancel()

        self.assertTrue(bubble._closed)
        self.assertEqual(bubble._root.destroy_calls, 1)
        self.assertEqual(bubble._history.close_calls, 1)

    def test_poll_stops_immediately_when_synchronous_recovery_cancel_destroys_root(self) -> None:
        bubble = self._bubble()
        root = _DestroyedAfterRoot()
        controller = _QueueController()
        history = _QueueHistory()
        controller.updates.put(ControllerUpdate(ControllerEventKind.DISCOVERY))
        history.updates.put(UsageRecorderEvent("CNY", Decimal("1")))
        bubble._root = root
        bubble._controller = controller
        bubble._history = history
        bubble._controller_factory = lambda discovery: _OldController()
        bubble._recovery_factory = (
            lambda ready, cancel: _SynchronousCancelRecovery(ready, cancel)
        )

        bubble._poll_updates()

        self.assertTrue(bubble._closed)
        self.assertEqual(root.destroy_calls, 1)
        self.assertEqual(root.after_calls, 0)
        self.assertEqual(history.updates.qsize(), 1)


class SourceSafetyTests(unittest.TestCase):
    def test_cli_gui_target_is_available_without_accepting_a_key_argument(self) -> None:
        self.assertTrue(callable(run_app))
        self.assertEqual(run_app.__name__, "run_app")


if __name__ == "__main__":
    unittest.main()
