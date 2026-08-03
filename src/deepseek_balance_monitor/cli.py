from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence
import warnings

from .api import ApiClient
from .credentials import (
    CredentialReadResult,
    CredentialResolver,
    WindowsCredentialStore,
    _CtypesCredentialBackend,
    _decode_dedicated_blob,
    collect_claude_compat,
)
from .models import ProbeStatus
from .storage import AppPaths, migrate_legacy
from .windows import NamedMutex


@dataclass(frozen=True)
class _Services:
    api: ApiClient
    store: WindowsCredentialStore
    resolver: CredentialResolver


class _UsageError(Exception):
    pass


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message, sys.stderr)
        raise _ParserExit(status)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _UsageError:
        print("参数错误。请使用 --help 查看可用命令。", file=sys.stderr)
        return 2
    except _ParserExit as stopped:
        return stopped.status

    try:
        if args.check:
            return _run_check(_create_services())
        if args.set_key:
            return _run_set_key(_create_services())
        if args.migrate_legacy is not None:
            return _run_legacy_migration(Path(args.migrate_legacy))
        if args.migrate_claude_compat:
            return _run_claude_migration(_create_services())
        return _launch_gui()
    except Exception:
        print("操作失败，请检查环境后重试。", file=sys.stderr)
        return 3


def _build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(description="DeepSeek 余额监控")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="检查凭据与余额接口")
    modes.add_argument("--set-key", action="store_true", help="隐藏输入并验证 API Key")
    modes.add_argument("--migrate-legacy", metavar="PATH", help="迁移旧版非敏感配置")
    modes.add_argument(
        "--migrate-claude-compat",
        action="store_true",
        help="显式迁移兼容的 Claude 凭据",
    )
    return parser


def _create_services() -> _Services:
    api = ApiClient()
    store = WindowsCredentialStore()
    resolver = CredentialResolver(api, env=os.environ, store=store)
    return _Services(api, store, resolver)


def _run_check(services: _Services) -> int:
    result = services.resolver.discover(frozenset())
    if result.verified:
        print("凭据已验证。")
        return 0
    if result.candidate is not None:
        print("凭据暂时无法验证。")
        return 2
    if result.diagnostics:
        print("凭据读取异常，当前无法确认。")
        return 2
    print("未找到可用凭据。")
    return 1


def _run_set_key(services: _Services) -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        key = getpass.getpass("请输入 DeepSeek API Key：").strip()
    if not key:
        print("未输入有效凭据。")
        return 1
    result = services.api.probe_key(key)
    if result.status is ProbeStatus.INVALID:
        print("凭据无效。")
        return 1
    if result.status is ProbeStatus.INDETERMINATE:
        print("当前无法验证凭据，未保存。")
        return 2
    if result.status not in {
        ProbeStatus.VALID,
        ProbeStatus.AUTHENTICATED_NO_BALANCE,
    }:
        print("当前无法验证凭据，未保存。")
        return 2

    answer = input("保存到 Windows 凭据管理器？[y/N]：").strip().lower()
    if answer in {"y", "yes", "是"}:
        services.store.write_dedicated(key)
        print("凭据已验证并保存。")
    else:
        print("凭据已验证，本次未保存。")
    return 0


def _run_legacy_migration(source: Path) -> int:
    result = migrate_legacy(source, _app_paths())
    if result.config_migrated or result.usage_migrated:
        print("旧版非敏感数据迁移完成。")
    else:
        print("没有需要迁移的数据。")
    return 0


def _run_claude_migration(services: _Services) -> int:
    collected = _collect_claude_sources()
    result = services.resolver.discover_from_values(
        collected.candidates,
        excluded=frozenset(),
        diagnostics=collected.diagnostics,
    )
    if result.verified and result.candidate is not None:
        services.store.write_dedicated(result.candidate.key)
        print("兼容凭据已验证并迁移。")
        return 0
    if result.candidate is not None:
        print("兼容凭据暂时无法验证，未迁移。")
        return 2
    if result.diagnostics:
        print("兼容凭据读取异常，当前无法确认，未迁移。")
        return 2
    print("未找到可迁移的兼容凭据。")
    return 1


def _app_paths() -> AppPaths:
    return AppPaths.from_environment(os.environ)


def _collect_claude_sources() -> CredentialReadResult:
    return collect_claude_compat(
        env=os.environ,
        settings=_load_claude_settings(),
        known_credential_reader=_claude_credential_reader,
    )


def _load_claude_settings() -> Mapping[str, Any]:
    profile = os.environ.get("USERPROFILE")
    if not profile:
        return {}
    path = Path(profile) / ".claude" / "settings.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _claude_credential_reader(target: str) -> str | None:
    blob = _CtypesCredentialBackend().read(target)
    if blob is None:
        return None
    key = _decode_dedicated_blob(blob)
    if key is None:
        raise ValueError("Claude compatibility credential is invalid: <redacted>")
    return key


def _get_gui_runner() -> Callable[[], int | None]:
    from .app import run_app

    return run_app


def _launch_gui() -> int:
    mutex = NamedMutex()
    if not mutex.acquire():
        print("余额监控已在运行。")
        return 0
    try:
        result = _get_gui_runner()()
        return 0 if result is None else int(result)
    finally:
        mutex.close()
