from __future__ import annotations

import dataclasses
import unittest
from unittest.mock import patch

from deepseek_balance_monitor.credentials import (
    CredentialCandidate,
    CredentialReadResult,
    CredentialResolver,
    WindowsCredentialStore,
    _is_official_deepseek_anthropic_url,
    collect_claude_compat,
)
from deepseek_balance_monitor.models import ProbeResult, ProbeStatus


SECRET_ENV = "sk-env-super-secret"
SECRET_STORE = "sk-store-super-secret"
SECRET_COMPAT = "sk-compat-super-secret"


class FakeBackend:
    def __init__(self, blob: bytes | None = None, read_error: Exception | None = None) -> None:
        self.blob = blob
        self.read_error = read_error
        self.read_targets: list[str] = []
        self.writes: list[tuple[str, bytes]] = []
        self.deletes: list[str] = []

    def read(self, target: str) -> bytes | None:
        self.read_targets.append(target)
        if self.read_error is not None:
            raise self.read_error
        return self.blob

    def write(self, target: str, blob: bytes) -> None:
        self.writes.append((target, blob))

    def delete(self, target: str) -> None:
        self.deletes.append(target)


class FakeApiClient:
    def __init__(self, results: dict[str, ProbeResult]) -> None:
        self.results = results
        self.probed: list[str] = []

    def probe_key(self, key: str) -> ProbeResult:
        self.probed.append(key)
        return self.results[key]


class CredentialModelTests(unittest.TestCase):
    def test_result_models_are_immutable_and_repr_redacts_keys(self) -> None:
        candidate = CredentialCandidate("environment", SECRET_ENV)
        read_result = CredentialReadResult((candidate,), ("safe",))
        discovery = CredentialResolver(
            FakeApiClient({SECRET_ENV: ProbeResult(ProbeStatus.VALID)}),
            store=WindowsCredentialStore(backend=FakeBackend()),
        ).discover_from_values(
            (candidate,), excluded=frozenset()
        )

        for value in (candidate, read_result, discovery):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                value.diagnostics = ()
            self.assertNotIn(SECRET_ENV, repr(value))


class WindowsCredentialStoreTests(unittest.TestCase):
    def test_dedicated_target_round_trip_uses_strict_utf16le(self) -> None:
        backend = FakeBackend()
        store = WindowsCredentialStore(backend=backend)

        store.write_dedicated(f"  {SECRET_STORE}  ")
        target, blob = backend.writes[0]
        self.assertEqual(target, "DeepSeekBalanceMonitor.ApiKey")
        self.assertEqual(blob, SECRET_STORE.encode("utf-16-le"))

        backend.blob = blob
        result = store.read_dedicated()
        self.assertEqual(result.candidates, (CredentialCandidate("windows_credential", SECRET_STORE),))
        store.delete_dedicated()
        self.assertEqual(backend.deletes, ["DeepSeekBalanceMonitor.ApiKey"])

    def test_empty_blob_is_rejected_without_secret_diagnostic(self) -> None:
        result = WindowsCredentialStore(backend=FakeBackend(b"")).read_dedicated()

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.diagnostics, ("Dedicated Windows credential is invalid: <redacted>",))

    def test_oversized_blob_is_rejected(self) -> None:
        result = WindowsCredentialStore(backend=FakeBackend(b"x" * 2562)).read_dedicated()

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.diagnostics, ("Dedicated Windows credential is invalid: <redacted>",))

    def test_odd_or_invalid_utf16_blob_is_rejected(self) -> None:
        for blob in (b"x", b"\x00\xd8"):
            with self.subTest(blob=blob):
                result = WindowsCredentialStore(backend=FakeBackend(blob)).read_dedicated()
                self.assertEqual(result.candidates, ())
                self.assertEqual(
                    result.diagnostics,
                    ("Dedicated Windows credential is invalid: <redacted>",),
                )

    def test_empty_and_oversized_writes_are_rejected_before_native_call(self) -> None:
        backend = FakeBackend()
        store = WindowsCredentialStore(backend=backend)

        for key in ("   ", "x" * 1281):
            with self.subTest(length=len(key)), self.assertRaises(ValueError):
                store.write_dedicated(key)
        self.assertEqual(backend.writes, [])

    def test_unpaired_surrogate_write_raises_only_sanitized_error(self) -> None:
        backend = FakeBackend()
        store = WindowsCredentialStore(backend=backend)
        key = f"{SECRET_STORE}\ud800suffix"

        with self.assertRaises(ValueError) as captured:
            store.write_dedicated(key)

        error = captured.exception
        self.assertEqual(
            error.args,
            ("Dedicated Windows credential is invalid: <redacted>",),
        )
        self.assertIsNone(getattr(error, "object", None))
        for rendered in (str(error), repr(error), repr(error.args)):
            self.assertNotIn(SECRET_STORE, rendered)
        self.assertEqual(backend.writes, [])

    def test_native_reader_failure_is_structured_and_sanitized(self) -> None:
        backend = FakeBackend(read_error=RuntimeError(SECRET_STORE))

        result = WindowsCredentialStore(backend=backend).read_dedicated()

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.diagnostics, ("Dedicated Windows credential read failed",))
        self.assertNotIn(SECRET_STORE, repr(result))


class CredentialResolverTests(unittest.TestCase):
    def test_normal_priority_is_environment_then_dedicated_store(self) -> None:
        store = WindowsCredentialStore(backend=FakeBackend(SECRET_STORE.encode("utf-16-le")))
        resolver = CredentialResolver(FakeApiClient({}), env={"DEEPSEEK_API_KEY": SECRET_ENV}, store=store)

        collected = resolver.collect_normal()

        self.assertEqual(
            [(item.source, item.key) for item in collected.candidates],
            [("environment", SECRET_ENV), ("windows_credential", SECRET_STORE)],
        )

    def test_collection_strips_and_deduplicates_normalized_keys(self) -> None:
        store = WindowsCredentialStore(backend=FakeBackend(f" {SECRET_ENV} ".encode("utf-16-le")))
        resolver = CredentialResolver(
            FakeApiClient({}), env={"DEEPSEEK_API_KEY": f"  {SECRET_ENV}\t"}, store=store
        )

        result = resolver.collect_normal()

        self.assertEqual(result.candidates, (CredentialCandidate("environment", SECRET_ENV),))

    def test_reader_failure_keeps_other_candidates_and_diagnostic_is_sanitized(self) -> None:
        store = WindowsCredentialStore(backend=FakeBackend(read_error=RuntimeError(SECRET_STORE)))
        resolver = CredentialResolver(FakeApiClient({}), env={"DEEPSEEK_API_KEY": SECRET_ENV}, store=store)

        result = resolver.collect_normal()

        self.assertEqual(result.candidates, (CredentialCandidate("environment", SECRET_ENV),))
        self.assertEqual(result.diagnostics, ("Dedicated Windows credential read failed",))
        self.assertNotIn(SECRET_STORE, repr(result))

    def test_401_falls_back_to_next_candidate(self) -> None:
        api = FakeApiClient(
            {
                SECRET_ENV: ProbeResult(ProbeStatus.INVALID),
                SECRET_STORE: ProbeResult(ProbeStatus.VALID),
            }
        )
        resolver = CredentialResolver(api)

        result = resolver.discover_from_values(
            (
                CredentialCandidate("environment", SECRET_ENV),
                CredentialCandidate("windows_credential", SECRET_STORE),
            ),
            excluded=frozenset(),
        )

        self.assertEqual(result.candidate, CredentialCandidate("windows_credential", SECRET_STORE))
        self.assertTrue(result.verified)
        self.assertEqual(api.probed, [SECRET_ENV, SECRET_STORE])

    def test_authenticated_without_balance_is_verified(self) -> None:
        api = FakeApiClient({SECRET_ENV: ProbeResult(ProbeStatus.AUTHENTICATED_NO_BALANCE)})

        result = CredentialResolver(api).discover_from_values(
            (CredentialCandidate("environment", SECRET_ENV),), excluded=frozenset()
        )

        self.assertTrue(result.verified)
        self.assertEqual(result.probe.status, ProbeStatus.AUTHENTICATED_NO_BALANCE)

    def test_first_indeterminate_stops_and_is_selected_unverified(self) -> None:
        api = FakeApiClient(
            {
                SECRET_ENV: ProbeResult(ProbeStatus.INDETERMINATE, error="temporary"),
                SECRET_STORE: ProbeResult(ProbeStatus.VALID),
            }
        )

        result = CredentialResolver(api).discover_from_values(
            (
                CredentialCandidate("environment", SECRET_ENV),
                CredentialCandidate("windows_credential", SECRET_STORE),
            ),
            excluded=frozenset(),
        )

        self.assertEqual(result.candidate, CredentialCandidate("environment", SECRET_ENV))
        self.assertFalse(result.verified)
        self.assertEqual(api.probed, [SECRET_ENV])

    def test_excluded_keys_are_stripped_and_skipped(self) -> None:
        api = FakeApiClient({SECRET_STORE: ProbeResult(ProbeStatus.VALID)})

        result = CredentialResolver(api).discover_from_values(
            (
                CredentialCandidate("environment", f" {SECRET_ENV} "),
                CredentialCandidate("windows_credential", SECRET_STORE),
            ),
            excluded=frozenset({f"\t{SECRET_ENV} "}),
        )

        self.assertEqual(api.probed, [SECRET_STORE])
        self.assertEqual(result.candidate.key, SECRET_STORE)

    def test_discover_uses_normal_collection_and_preserves_reader_diagnostic(self) -> None:
        store = WindowsCredentialStore(backend=FakeBackend(read_error=RuntimeError(SECRET_STORE)))
        api = FakeApiClient({SECRET_ENV: ProbeResult(ProbeStatus.VALID)})
        resolver = CredentialResolver(api, env={"DEEPSEEK_API_KEY": SECRET_ENV}, store=store)

        result = resolver.discover(frozenset())

        self.assertTrue(result.verified)
        self.assertEqual(result.diagnostics, ("Dedicated Windows credential read failed",))
        self.assertNotIn(SECRET_ENV, repr(result))


class ClaudeCompatibilityTests(unittest.TestCase):
    def test_exact_official_url_guard(self) -> None:
        accepted = (
            "https://api.deepseek.com/anthropic",
            "https://api.deepseek.com:443/anthropic",
        )
        rejected = (
            "http://api.deepseek.com/anthropic",
            "https://api.deepseek.com:444/anthropic",
            "https://evil.example/anthropic",
            "https://api.deepseek.com.evil.example/anthropic",
            "https://api.deepseek.com/anthropic/",
            "https://api.deepseek.com/anthropic?x=1",
            "https://api.deepseek.com/anthropic#x",
            "https://user@api.deepseek.com/anthropic",
            "https://api.deepseek.com/anthropic//../anthropic",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(_is_official_deepseek_anthropic_url(url))
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(_is_official_deepseek_anthropic_url(url))

    def test_tokens_are_paired_only_with_same_source_url(self) -> None:
        settings = {
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": " settings-token ",
            }
        }
        env = {
            "ANTHROPIC_BASE_URL": "https://evil.example/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "env-token",
        }

        result = collect_claude_compat(env=env, settings=settings)

        self.assertEqual(
            result.candidates,
            (CredentialCandidate("claude_settings", "settings-token"),),
        )

    def test_known_credential_uses_effective_environment_url_then_settings_url(self) -> None:
        calls: list[str] = []

        def reader(target: str) -> str:
            calls.append(target)
            return f" {SECRET_COMPAT} "

        settings = {"env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"}}
        blocked = collect_claude_compat(
            env={"ANTHROPIC_BASE_URL": "https://evil.example/anthropic"},
            settings=settings,
            known_credential_reader=reader,
        )
        allowed = collect_claude_compat(
            env={}, settings=settings, known_credential_reader=reader
        )

        self.assertEqual(blocked.candidates, ())
        self.assertEqual(allowed.candidates[0].key, SECRET_COMPAT)
        self.assertEqual(calls, ["ClaudeCode.AnthropicAuthToken"])

    def test_api_key_helper_is_never_executed_or_used_as_token(self) -> None:
        marker = SECRET_COMPAT
        settings = {
            "apiKeyHelper": f"write {marker}",
            "env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"},
        }

        with patch("os.system", side_effect=AssertionError("helper executed")), patch(
            "subprocess.run", side_effect=AssertionError("helper executed")
        ), patch("subprocess.Popen", side_effect=AssertionError("helper executed")):
            result = collect_claude_compat(env={}, settings=settings)

        self.assertEqual(result.candidates, ())
        self.assertNotIn(marker, repr(result))

    def test_compat_reader_failure_is_structured_and_does_not_drop_later_values(self) -> None:
        def broken_reader(target: str) -> str:
            raise RuntimeError(SECRET_COMPAT)

        env = {
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_AUTH_TOKEN": SECRET_ENV,
        }

        result = collect_claude_compat(
            env=env, settings={}, known_credential_reader=broken_reader
        )

        self.assertEqual(result.candidates, (CredentialCandidate("claude_environment", SECRET_ENV),))
        self.assertEqual(result.diagnostics, ("Claude compatibility credential read failed",))
        self.assertNotIn(SECRET_COMPAT, repr(result))


if __name__ == "__main__":
    unittest.main()
