from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .api import ApiClient
from .models import ProbeResult, ProbeStatus


_DEDICATED_TARGET = "DeepSeekBalanceMonitor.ApiKey"
_CLAUDE_COMPAT_TARGET = "ClaudeCode.AnthropicAuthToken"
_MAX_CREDENTIAL_BLOB_BYTES = 2560
_OFFICIAL_ANTHROPIC_URLS = frozenset(
    {
        "https://api.deepseek.com/anthropic",
        "https://api.deepseek.com:443/anthropic",
    }
)


@dataclass(frozen=True)
class CredentialCandidate:
    source: str
    key: str = field(repr=False)


@dataclass(frozen=True)
class CredentialReadResult:
    candidates: tuple[CredentialCandidate, ...] = field(default=(), repr=False)
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveryResult:
    candidate: CredentialCandidate | None = field(default=None, repr=False)
    probe: ProbeResult | None = field(default=None, repr=False)
    verified: bool = False
    diagnostics: tuple[str, ...] = ()


class _CredentialBackend(Protocol):
    def read(self, target: str) -> bytes | None: ...

    def write(self, target: str, blob: bytes) -> None: ...

    def delete(self, target: str) -> None: ...


class WindowsCredentialStore:
    def __init__(self, backend: _CredentialBackend | None = None) -> None:
        self._backend = backend if backend is not None else _CtypesCredentialBackend()

    def read_dedicated(self) -> CredentialReadResult:
        try:
            blob = self._backend.read(_DEDICATED_TARGET)
        except Exception:
            return CredentialReadResult(diagnostics=("Dedicated Windows credential read failed",))
        if blob is None:
            return CredentialReadResult()
        key = _decode_dedicated_blob(blob)
        if key is None:
            return CredentialReadResult(
                diagnostics=("Dedicated Windows credential is invalid: <redacted>",)
            )
        return CredentialReadResult((CredentialCandidate("windows_credential", key),))

    def write_dedicated(self, key: str) -> None:
        normalized = key.strip()
        if (
            not normalized
            or "\x00" in normalized
            or any(0xD800 <= ord(character) <= 0xDFFF for character in normalized)
        ):
            raise ValueError("Dedicated Windows credential is invalid: <redacted>")
        blob = normalized.encode("utf-16-le")
        if len(blob) > _MAX_CREDENTIAL_BLOB_BYTES:
            raise ValueError("Dedicated Windows credential is invalid: <redacted>")
        self._backend.write(_DEDICATED_TARGET, blob)

    def delete_dedicated(self) -> None:
        self._backend.delete(_DEDICATED_TARGET)


class CredentialResolver:
    def __init__(
        self,
        api_client: ApiClient,
        *,
        env: Mapping[str, str] | None = None,
        store: WindowsCredentialStore | None = None,
    ) -> None:
        self._api_client = api_client
        self._env = os.environ if env is None else env
        self._store = WindowsCredentialStore() if store is None else store

    def collect_normal(self) -> CredentialReadResult:
        candidates: list[CredentialCandidate] = []
        env_key = self._env.get("DEEPSEEK_API_KEY")
        if env_key is not None:
            candidates.append(CredentialCandidate("environment", env_key))

        stored = self._store.read_dedicated()
        candidates.extend(stored.candidates)
        return CredentialReadResult(
            _normalize_candidates(candidates),
            stored.diagnostics,
        )

    def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
        collected = self.collect_normal()
        return self.discover_from_values(
            collected.candidates,
            excluded=excluded,
            diagnostics=collected.diagnostics,
        )

    def discover_from_values(
        self,
        candidates: tuple[CredentialCandidate, ...],
        *,
        excluded: frozenset[str],
        diagnostics: tuple[str, ...] = (),
    ) -> DiscoveryResult:
        excluded_normalized = {key.strip() for key in excluded}
        for candidate in _normalize_candidates(candidates):
            if candidate.key in excluded_normalized:
                continue
            probe = self._api_client.probe_key(candidate.key)
            if probe.status in {ProbeStatus.VALID, ProbeStatus.AUTHENTICATED_NO_BALANCE}:
                return DiscoveryResult(candidate, probe, True, diagnostics)
            if probe.status is ProbeStatus.INDETERMINATE:
                return DiscoveryResult(candidate, probe, False, diagnostics)
        return DiscoveryResult(diagnostics=diagnostics)


def _is_official_deepseek_anthropic_url(url: str) -> bool:
    return url in _OFFICIAL_ANTHROPIC_URLS


def collect_claude_compat(
    *,
    env: Mapping[str, str],
    settings: Mapping[str, Any],
    known_credential_reader: Callable[[str], str | None] | None = None,
) -> CredentialReadResult:
    candidates: list[CredentialCandidate] = []
    diagnostics: list[str] = []
    settings_env_value = settings.get("env")
    settings_env = settings_env_value if isinstance(settings_env_value, Mapping) else {}

    _append_paired_claude_token(candidates, "claude_environment", env)
    _append_paired_claude_token(candidates, "claude_settings", settings_env)

    effective_base = (
        env.get("ANTHROPIC_BASE_URL")
        if "ANTHROPIC_BASE_URL" in env
        else settings_env.get("ANTHROPIC_BASE_URL")
    )
    if (
        known_credential_reader is not None
        and isinstance(effective_base, str)
        and _is_official_deepseek_anthropic_url(effective_base)
    ):
        try:
            key = known_credential_reader(_CLAUDE_COMPAT_TARGET)
        except Exception:
            diagnostics.append("Claude compatibility credential read failed")
        else:
            if isinstance(key, str):
                candidates.append(CredentialCandidate("claude_windows_credential", key))

    return CredentialReadResult(_normalize_candidates(candidates), tuple(diagnostics))


def _append_paired_claude_token(
    candidates: list[CredentialCandidate], source: str, values: Mapping[str, Any]
) -> None:
    base_url = values.get("ANTHROPIC_BASE_URL")
    token = values.get("ANTHROPIC_AUTH_TOKEN")
    if (
        isinstance(base_url, str)
        and _is_official_deepseek_anthropic_url(base_url)
        and isinstance(token, str)
    ):
        candidates.append(CredentialCandidate(source, token))


def _normalize_candidates(
    candidates: tuple[CredentialCandidate, ...] | list[CredentialCandidate],
) -> tuple[CredentialCandidate, ...]:
    normalized: list[CredentialCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.key.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(CredentialCandidate(candidate.source, key))
    return tuple(normalized)


def _decode_dedicated_blob(blob: bytes) -> str | None:
    if (
        not isinstance(blob, bytes)
        or not blob
        or len(blob) % 2
        or len(blob) > _MAX_CREDENTIAL_BLOB_BYTES
    ):
        return None
    try:
        decoded = blob.decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    normalized = decoded.strip()
    if not normalized or "\x00" in normalized:
        return None
    return normalized


class _CtypesCredentialBackend:
    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Credential Manager is unavailable")
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        credential_pointer = ctypes.POINTER(self._CREDENTIALW)
        self._advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(credential_pointer),
        ]
        self._advapi32.CredReadW.restype = wintypes.BOOL
        self._advapi32.CredWriteW.argtypes = [
            ctypes.POINTER(self._CREDENTIALW),
            wintypes.DWORD,
        ]
        self._advapi32.CredWriteW.restype = wintypes.BOOL
        self._advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi32.CredDeleteW.restype = wintypes.BOOL
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi32.CredFree.restype = None

    def read(self, target: str) -> bytes | None:
        pointer = ctypes.POINTER(self._CREDENTIALW)()
        if not self._advapi32.CredReadW(
            target, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            error = ctypes.get_last_error()
            if error == self._ERROR_NOT_FOUND:
                return None
            raise OSError(error, "Credential read failed")
        try:
            credential = pointer.contents
            return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        finally:
            self._advapi32.CredFree(pointer)

    def write(self, target: str, blob: bytes) -> None:
        buffer = (wintypes.BYTE * len(blob)).from_buffer_copy(blob)
        credential = self._CREDENTIALW()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "DeepSeekBalanceMonitor"
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise OSError(error, "Credential write failed")

    def delete(self, target: str) -> None:
        if self._advapi32.CredDeleteW(target, self._CRED_TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != self._ERROR_NOT_FOUND:
            raise OSError(error, "Credential delete failed")
