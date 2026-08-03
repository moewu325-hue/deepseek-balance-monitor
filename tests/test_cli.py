from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from deepseek_balance_monitor.credentials import (
    CredentialCandidate,
    CredentialReadResult,
    DiscoveryResult,
)
from deepseek_balance_monitor.models import ProbeResult, ProbeStatus
from deepseek_balance_monitor.storage import AppPaths
import deepseek_balance_monitor.cli as cli


SECRET = "sk-synthetic-cli-secret"
OTHER_SECRET = "sk-synthetic-claude-secret"


class FakeApi:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result
        self.probed: list[str] = []

    def probe_key(self, key: str) -> ProbeResult:
        self.probed.append(key)
        return self.result


class FakeStore:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write_dedicated(self, key: str) -> None:
        self.written.append(key)


class FakeResolver:
    def __init__(self, result: DiscoveryResult) -> None:
        self.result = result
        self.discover_calls: list[frozenset[str]] = []
        self.compat_candidates: tuple[CredentialCandidate, ...] = ()

    def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
        self.discover_calls.append(excluded)
        return self.result

    def discover_from_values(
        self,
        candidates: tuple[CredentialCandidate, ...],
        *,
        excluded: frozenset[str],
        diagnostics: tuple[str, ...] = (),
    ) -> DiscoveryResult:
        self.compat_candidates = candidates
        self.discover_calls.append(excluded)
        return self.result


@dataclass
class FakeServices:
    api: FakeApi
    store: FakeStore
    resolver: FakeResolver


def services_for(
    discovery: DiscoveryResult | None = None,
    *,
    probe: ProbeResult | None = None,
) -> FakeServices:
    probe = probe or ProbeResult(ProbeStatus.VALID)
    discovery = discovery or DiscoveryResult(
        CredentialCandidate("synthetic", SECRET), probe, True
    )
    return FakeServices(FakeApi(probe), FakeStore(), FakeResolver(discovery))


def invoke(args: list[str], services: FakeServices | None = None) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    service_patch = patch.object(cli, "_create_services", return_value=services)
    with service_patch, redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main(args)
    return code, stdout.getvalue(), stderr.getvalue()


class CheckCommandTests(unittest.TestCase):
    def test_check_returns_contract_exit_codes(self) -> None:
        cases = (
            (
                DiscoveryResult(
                    CredentialCandidate("synthetic", SECRET),
                    ProbeResult(ProbeStatus.VALID),
                    True,
                ),
                0,
            ),
            (DiscoveryResult(), 1),
            (
                DiscoveryResult(
                    CredentialCandidate("synthetic", SECRET),
                    ProbeResult(ProbeStatus.INDETERMINATE, error="temporary"),
                    False,
                ),
                2,
            ),
        )
        for discovery, expected in cases:
            with self.subTest(expected=expected):
                code, stdout, stderr = invoke(["--check"], services_for(discovery))
                self.assertEqual(code, expected)
                self.assertNotIn(SECRET, stdout + stderr)

    def test_check_internal_failure_is_sanitized_exit_three(self) -> None:
        with patch.object(cli, "_create_services", side_effect=RuntimeError(SECRET)):
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.main(["--check"])

        self.assertEqual(code, 3)
        self.assertNotIn(SECRET, stdout.getvalue() + stderr.getvalue())

    def test_check_reader_diagnostic_is_unverified_not_absent_and_is_sanitized(self) -> None:
        diagnostic = f"credential reader failed for {SECRET}"
        uncertain = DiscoveryResult(diagnostics=(diagnostic,))

        code, stdout, stderr = invoke(["--check"], services_for(uncertain))

        self.assertEqual(code, 2)
        self.assertIn("无法确认", stdout)
        self.assertNotIn(diagnostic, stdout + stderr)
        self.assertNotIn(SECRET, stdout + stderr)

    def test_check_does_not_create_gui_mutex_paths_or_persistent_files(self) -> None:
        services = services_for()
        with patch.object(cli, "_launch_gui", side_effect=AssertionError("GUI created")), patch.object(
            cli, "NamedMutex", side_effect=AssertionError("mutex created")
        ), patch.object(cli, "_app_paths", side_effect=AssertionError("paths created")), patch.object(
            cli, "migrate_legacy", side_effect=AssertionError("migration ran")
        ):
            code, _, _ = invoke(["--check"], services)

        self.assertEqual(code, 0)
        self.assertEqual(services.store.written, [])
        self.assertEqual(services.resolver.discover_calls, [frozenset()])


class SetKeyCommandTests(unittest.TestCase):
    def test_set_key_rejects_command_line_value_without_echoing_it(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch("getpass.getpass", side_effect=AssertionError("prompted")), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = cli.main(["--set-key", SECRET])

        self.assertEqual(code, 2)
        self.assertNotIn(SECRET, stdout.getvalue())
        self.assertNotIn(SECRET, stderr.getvalue())

    def test_set_key_uses_hidden_prompt_and_requires_explicit_persistence(self) -> None:
        services = services_for(probe=ProbeResult(ProbeStatus.VALID))
        with patch("getpass.getpass", return_value=f"  {SECRET}  ") as hidden, patch(
            "builtins.input", return_value="y"
        ):
            code, stdout, stderr = invoke(["--set-key"], services)

        self.assertEqual(code, 0)
        hidden.assert_called_once()
        self.assertEqual(services.api.probed, [SECRET])
        self.assertEqual(services.store.written, [SECRET])
        self.assertNotIn(SECRET, stdout + stderr)

    def test_set_key_getpass_warning_fails_closed_before_echo_fallback(self) -> None:
        services = services_for(probe=ProbeResult(ProbeStatus.VALID))

        def warning_then_echo(_prompt: str) -> str:
            warnings.warn(f"unsafe terminal fallback {SECRET}", cli.getpass.GetPassWarning)
            return input("echo fallback")

        with patch("getpass.getpass", side_effect=warning_then_echo), patch(
            "builtins.input", side_effect=AssertionError("echo fallback used")
        ) as echo_fallback:
            code, stdout, stderr = invoke(["--set-key"], services)

        self.assertEqual(code, 3)
        echo_fallback.assert_not_called()
        self.assertEqual(services.api.probed, [])
        self.assertEqual(services.store.written, [])
        self.assertNotIn("unsafe terminal fallback", stdout + stderr)
        self.assertNotIn(SECRET, stdout + stderr)

    def test_set_key_verified_memory_only_does_not_write(self) -> None:
        services = services_for(probe=ProbeResult(ProbeStatus.AUTHENTICATED_NO_BALANCE))
        with patch("getpass.getpass", return_value=SECRET), patch(
            "builtins.input", return_value="n"
        ):
            code, _, _ = invoke(["--set-key"], services)

        self.assertEqual(code, 0)
        self.assertEqual(services.store.written, [])

    def test_set_key_invalid_or_indeterminate_never_persists(self) -> None:
        cases = (
            (ProbeResult(ProbeStatus.INVALID), 1),
            (ProbeResult(ProbeStatus.INDETERMINATE, error=SECRET), 2),
        )
        for result, expected in cases:
            with self.subTest(status=result.status):
                services = services_for(probe=result)
                with patch("getpass.getpass", return_value=SECRET), patch(
                    "builtins.input", side_effect=AssertionError("confirmation requested")
                ):
                    code, stdout, stderr = invoke(["--set-key"], services)

                self.assertEqual(code, expected)
                self.assertEqual(services.store.written, [])
                self.assertNotIn(SECRET, stdout + stderr)


class MigrationCommandTests(unittest.TestCase):
    def test_legacy_migration_uses_storage_allowlist_and_drops_key(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            paths = AppPaths(Path(target_dir))
            (source / "config.json").write_text(
                json.dumps(
                    {
                        "refresh_interval": 25,
                        "currency": "usd",
                        "auto_start": True,
                        "api_key": SECRET,
                        "unknown": "discard",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(cli, "_app_paths", return_value=paths), patch.object(
                cli, "_create_services", side_effect=AssertionError("services created")
            ):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli.main(["--migrate-legacy", str(source)])

            saved_text = paths.config_path.read_text(encoding="utf-8")
            saved = json.loads(saved_text)

        self.assertEqual(code, 0)
        self.assertEqual(saved["currency"], "USD")
        self.assertFalse(saved["auto_start"])
        self.assertNotIn("api_key", saved)
        self.assertNotIn("unknown", saved)
        self.assertNotIn(SECRET, saved_text + stdout.getvalue() + stderr.getvalue())

    def test_claude_migration_is_explicit_and_persists_only_verified_candidate(self) -> None:
        verified = DiscoveryResult(
            CredentialCandidate("claude_settings", OTHER_SECRET),
            ProbeResult(ProbeStatus.VALID),
            True,
        )
        services = services_for(verified)
        sources = CredentialReadResult(
            (CredentialCandidate("claude_settings", OTHER_SECRET),)
        )

        with patch.object(cli, "_collect_claude_sources", return_value=sources):
            code, stdout, stderr = invoke(["--migrate-claude-compat"], services)

        self.assertEqual(code, 0)
        self.assertEqual(services.resolver.compat_candidates, sources.candidates)
        self.assertEqual(services.store.written, [OTHER_SECRET])
        self.assertNotIn(OTHER_SECRET, stdout + stderr)

    def test_claude_migration_never_persists_indeterminate_candidate(self) -> None:
        unverified = DiscoveryResult(
            CredentialCandidate("claude_environment", OTHER_SECRET),
            ProbeResult(ProbeStatus.INDETERMINATE, error=OTHER_SECRET),
            False,
        )
        services = services_for(unverified)
        sources = CredentialReadResult(
            (CredentialCandidate("claude_environment", OTHER_SECRET),)
        )

        with patch.object(cli, "_collect_claude_sources", return_value=sources):
            code, stdout, stderr = invoke(["--migrate-claude-compat"], services)

        self.assertEqual(code, 2)
        self.assertEqual(services.store.written, [])
        self.assertNotIn(OTHER_SECRET, stdout + stderr)

    def test_claude_reader_diagnostic_is_unverified_and_never_persists(self) -> None:
        diagnostic = f"malformed credential {OTHER_SECRET}"
        uncertain = DiscoveryResult(diagnostics=(diagnostic,))
        services = services_for(uncertain)
        sources = CredentialReadResult(diagnostics=(diagnostic,))

        with patch.object(cli, "_collect_claude_sources", return_value=sources):
            code, stdout, stderr = invoke(["--migrate-claude-compat"], services)

        self.assertEqual(code, 2)
        self.assertEqual(services.store.written, [])
        self.assertIn("无法确认", stdout)
        self.assertNotIn(diagnostic, stdout + stderr)
        self.assertNotIn(OTHER_SECRET, stdout + stderr)

    def test_claude_source_collection_ignores_helper_and_requires_exact_url(self) -> None:
        accepted_settings = {
            "apiKeyHelper": f"write {OTHER_SECRET}",
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": OTHER_SECRET,
            },
        }
        rejected_settings = {
            "apiKeyHelper": f"write {OTHER_SECRET}",
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic/",
                "ANTHROPIC_AUTH_TOKEN": OTHER_SECRET,
            },
        }

        with patch.dict(cli.os.environ, {}, clear=True), patch.object(
            cli, "_load_claude_settings", return_value=accepted_settings
        ), patch.object(cli, "_claude_credential_reader", return_value=None), patch(
            "os.system", side_effect=AssertionError("helper executed")
        ), patch("subprocess.run", side_effect=AssertionError("helper executed")), patch(
            "subprocess.Popen", side_effect=AssertionError("helper executed")
        ):
            accepted = cli._collect_claude_sources()
        with patch.dict(cli.os.environ, {}, clear=True), patch.object(
            cli, "_load_claude_settings", return_value=rejected_settings
        ), patch.object(
            cli, "_claude_credential_reader", side_effect=AssertionError("credential read")
        ):
            rejected = cli._collect_claude_sources()

        self.assertEqual(accepted.candidates[0].key, OTHER_SECRET)
        self.assertEqual(rejected.candidates, ())


class GuiDispatchTests(unittest.TestCase):
    def test_default_mode_is_the_only_path_that_launches_gui(self) -> None:
        with patch.object(cli, "_launch_gui", return_value=7) as launch, patch.object(
            cli, "_create_services", side_effect=AssertionError("services created")
        ):
            code = cli.main([])

        self.assertEqual(code, 7)
        launch.assert_called_once_with()

    def test_help_returns_zero_without_launching_or_initializing_services(self) -> None:
        stdout = StringIO()
        with patch.object(cli, "_launch_gui", side_effect=AssertionError("GUI created")), patch.object(
            cli, "_create_services", side_effect=AssertionError("services created")
        ), redirect_stdout(stdout):
            code = cli.main(["--help"])

        self.assertEqual(code, 0)
        self.assertIn("--check", stdout.getvalue())

    def test_gui_runner_is_lazy_and_mutex_is_closed(self) -> None:
        class FakeMutex:
            instances: list["FakeMutex"] = []

            def __init__(self) -> None:
                self.closed = False
                self.instances.append(self)

            def acquire(self) -> bool:
                return True

            def close(self) -> None:
                self.closed = True

        with patch.object(cli, "NamedMutex", FakeMutex), patch.object(
            cli, "_get_gui_runner", return_value=lambda: 9
        ) as lazy_import:
            code = cli._launch_gui()

        self.assertEqual(code, 9)
        lazy_import.assert_called_once_with()
        self.assertTrue(FakeMutex.instances[0].closed)

    def test_existing_gui_instance_does_not_import_or_run_app(self) -> None:
        class ExistingMutex:
            def acquire(self) -> bool:
                return False

            def close(self) -> None:
                raise AssertionError("unowned handle closed")

        with patch.object(cli, "NamedMutex", return_value=ExistingMutex()), patch.object(
            cli, "_get_gui_runner", side_effect=AssertionError("app imported")
        ), redirect_stdout(StringIO()):
            code = cli._launch_gui()

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
