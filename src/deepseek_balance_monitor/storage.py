from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping


REFRESH_INTERVAL_MIN_SECONDS = 10
REFRESH_INTERVAL_MAX_SECONDS = 86_400
WINDOW_POSITION_MIN = -100_000
WINDOW_POSITION_MAX = 100_000
OPACITY_MIN = 0.2
OPACITY_MAX = 1.0
LOW_BALANCE_WARNING_MAX = Decimal("1e12")
CURRENCY_MAX_LENGTH = 12
SNAP_EDGE_MAX_LENGTH = 32

_CONFIG_FIELDS = (
    "refresh_interval",
    "window_x",
    "window_y",
    "opacity",
    "low_balance_warning",
    "currency",
    "snap_edge",
    "collapsed",
    "auto_start",
    "display_mode",
)
_LEGACY_CONFIG_FIELDS = _CONFIG_FIELDS[:-1]


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def usage_log_path(self) -> Path:
        return self.root / "usage_log.json"

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> AppPaths:
        local_app_data = env.get("LOCALAPPDATA")
        if not isinstance(local_app_data, str) or not local_app_data.strip():
            raise ValueError("LOCALAPPDATA is required")
        return cls(Path(local_app_data) / "DeepSeekBalanceMonitor")


@dataclass(frozen=True)
class AppConfig:
    refresh_interval: int = 60
    window_x: int | None = None
    window_y: int | None = None
    opacity: float = 0.9
    low_balance_warning: str = "10.00"
    currency: str = "CNY"
    snap_edge: str | None = None
    collapsed: bool = False
    auto_start: bool = False
    display_mode: str = "panel"


@dataclass(frozen=True)
class UsageBaseline:
    date: date
    total_balance: Decimal


@dataclass(frozen=True)
class UsageRecord:
    time: str
    currency: str
    total_balance: Decimal


@dataclass(frozen=True)
class UsageLog:
    baselines: Mapping[str, UsageBaseline] = field(default_factory=dict)
    records: tuple[UsageRecord, ...] = ()


@dataclass(frozen=True)
class MigrationResult:
    config_migrated: bool = False
    usage_migrated: bool = False
    skipped_usage_records: int = 0
    skipped_config_fields: int = 0


def load_config(path: Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        return AppConfig()
    raw = _read_json(path)
    if not isinstance(raw, Mapping):
        raise ValueError("Config must be a JSON object")
    return _config_from_mapping(raw)


def save_config_atomic(
    path: Path, config: AppConfig, *, before_replace: Callable[[], None] | None = None
) -> None:
    if not isinstance(config, AppConfig):
        raise TypeError("config must be an AppConfig")
    expected = _config_to_mapping(_config_from_mapping(_config_to_mapping(config)))
    _atomic_write_json(Path(path), expected, before_replace=before_replace)
    _validate_raw_read_back(Path(path), expected, "Config")


def load_usage_log(path: Path) -> UsageLog:
    path = Path(path)
    if not path.exists():
        return UsageLog()
    raw = _read_json(path)
    return _usage_log_from_mapping(raw)


def _usage_log_from_mapping(raw: Any) -> UsageLog:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 2:
        raise ValueError("Usage log must use schema version 2")
    raw_baselines = raw.get("baselines")
    raw_records = raw.get("records")
    if not isinstance(raw_baselines, Mapping) or not isinstance(raw_records, list):
        raise ValueError("Usage log shape is invalid")
    baselines: dict[str, UsageBaseline] = {}
    for currency, value in raw_baselines.items():
        if not isinstance(currency, str) or not isinstance(value, Mapping):
            raise ValueError("Usage baseline is invalid")
        normalized_currency = _currency(currency)
        if normalized_currency in baselines:
            raise ValueError("Usage baseline currency collision")
        raw_date = value.get("date")
        raw_balance = value.get("total_balance")
        if not isinstance(raw_date, str):
            raise ValueError("Usage baseline is invalid")
        baselines[normalized_currency] = UsageBaseline(
            date.fromisoformat(raw_date), _decimal(raw_balance)
        )
    records: list[UsageRecord] = []
    for value in raw_records:
        record = _usage_record(value)
        if record is None:
            raise ValueError("Usage record is invalid")
        records.append(record)
    return UsageLog(baselines, tuple(records))


def save_usage_log_atomic(
    path: Path, log: UsageLog, *, before_replace: Callable[[], None] | None = None
) -> None:
    if not isinstance(log, UsageLog):
        raise TypeError("log must be a UsageLog")
    expected = _usage_log_to_mapping(log)
    _atomic_write_json(Path(path), expected, before_replace=before_replace)
    _validate_raw_read_back(Path(path), expected, "Usage log")


def migrate_legacy(source_dir: Path, paths: AppPaths) -> MigrationResult:
    source_dir = Path(source_dir)
    config_path = source_dir / "config.json"
    usage_path = source_dir / "usage_log.json"
    migrate_config = not paths.config_path.exists() and config_path.exists()
    migrate_usage = not paths.usage_log_path.exists() and usage_path.exists()
    if not migrate_config and not migrate_usage:
        return MigrationResult()

    source_config = AppConfig()
    skipped_config_fields = 0
    usage_default_currency: str | None = "CNY"
    if config_path.exists() and (migrate_config or migrate_usage):
        raw_config = _read_json(config_path)
        if not isinstance(raw_config, Mapping):
            raise ValueError("Legacy config must be a JSON object")
        source_config, skipped_config_fields, usage_default_currency = _legacy_config(
            raw_config
        )

    migrated_usage = UsageLog()
    skipped_usage_records = 0
    if migrate_usage:
        raw_usage = _read_json(usage_path)
        migrated_usage, skipped_usage_records = _legacy_usage_log(
            raw_usage, usage_default_currency
        )

    if migrate_config:
        save_config_atomic(paths.config_path, source_config)
    if migrate_usage:
        save_usage_log_atomic(paths.usage_log_path, migrated_usage)
    return MigrationResult(
        config_migrated=migrate_config,
        usage_migrated=migrate_usage,
        skipped_usage_records=skipped_usage_records,
        skipped_config_fields=skipped_config_fields,
    )


def _config_from_mapping(raw: Mapping[str, Any]) -> AppConfig:
    if any(key not in _CONFIG_FIELDS for key in raw):
        raise ValueError("Config contains unsupported fields")
    defaults = AppConfig()
    return AppConfig(
        refresh_interval=_bounded_integer(
            raw.get("refresh_interval", defaults.refresh_interval),
            REFRESH_INTERVAL_MIN_SECONDS,
            REFRESH_INTERVAL_MAX_SECONDS,
            "Expected a valid refresh interval",
        ),
        window_x=_optional_position(raw.get("window_x", defaults.window_x)),
        window_y=_optional_position(raw.get("window_y", defaults.window_y)),
        opacity=_opacity(raw.get("opacity", defaults.opacity)),
        low_balance_warning=_warning_decimal_string(
            raw.get("low_balance_warning", defaults.low_balance_warning)
        ),
        currency=_currency(raw.get("currency", defaults.currency)),
        snap_edge=_optional_string(raw.get("snap_edge", defaults.snap_edge)),
        collapsed=_boolean(raw.get("collapsed", defaults.collapsed)),
        auto_start=_boolean(raw.get("auto_start", defaults.auto_start)),
        display_mode=_display_mode(raw.get("display_mode", defaults.display_mode)),
    )


def _legacy_config(raw: Mapping[str, Any]) -> tuple[AppConfig, int, str | None]:
    sanitized: dict[str, Any] = {}
    skipped = 0
    usage_default_currency: str | None = "CNY" if "currency" not in raw else None
    for key in _LEGACY_CONFIG_FIELDS:
        if key not in raw:
            continue
        try:
            validated = _config_from_mapping({key: raw[key]})
        except (TypeError, ValueError, InvalidOperation):
            skipped += 1
            continue
        sanitized[key] = getattr(validated, key)
        if key == "currency":
            usage_default_currency = validated.currency
    sanitized["auto_start"] = False
    return _config_from_mapping(sanitized), skipped, usage_default_currency


def _config_to_mapping(config: AppConfig) -> dict[str, Any]:
    return {key: getattr(config, key) for key in _CONFIG_FIELDS}


def _usage_log_to_mapping(log: UsageLog) -> dict[str, Any]:
    if not isinstance(log.baselines, Mapping) or not isinstance(log.records, tuple):
        raise ValueError("Usage log shape is invalid")
    baselines: dict[str, dict[str, str]] = {}
    for currency, baseline in log.baselines.items():
        if not isinstance(currency, str) or not isinstance(baseline, UsageBaseline):
            raise ValueError("Usage baseline is invalid")
        if type(baseline.date) is not date:
            raise ValueError("Usage baseline is invalid")
        normalized_currency = _currency(currency)
        if normalized_currency in baselines:
            raise ValueError("Usage baseline currency collision")
        baselines[normalized_currency] = {
            "date": baseline.date.isoformat(),
            "total_balance": _decimal_string(baseline.total_balance),
        }
    records: list[dict[str, str]] = []
    for record in log.records:
        if not isinstance(record, UsageRecord):
            raise ValueError("Usage record is invalid")
        serialized = {
            "time": record.time,
            "currency": _currency(record.currency),
            "total_balance": _decimal_string(record.total_balance),
        }
        if _usage_record(serialized) is None:
            raise ValueError("Usage record is invalid")
        records.append(serialized)
    return {
        "schema_version": 2,
        "baselines": baselines,
        "records": records,
    }


def _legacy_usage_log(raw: Any, default_currency: str | None) -> tuple[UsageLog, int]:
    if not isinstance(raw, Mapping):
        return UsageLog(), 0
    baselines: dict[str, UsageBaseline] = {}
    skipped = 0
    raw_baselines = raw.get("baselines", {})
    if isinstance(raw_baselines, Mapping):
        for raw_date, raw_balance in raw_baselines.items():
            try:
                if default_currency is None or not isinstance(raw_date, str):
                    raise ValueError
                candidate = UsageBaseline(
                    date.fromisoformat(raw_date), _decimal(raw_balance)
                )
                current = baselines.get(default_currency)
                if current is None or candidate.date > current.date:
                    baselines[default_currency] = candidate
            except (TypeError, ValueError, InvalidOperation):
                skipped += 1
                continue
    records: list[UsageRecord] = []
    raw_records = raw.get("records", [])
    if isinstance(raw_records, list):
        for raw_record in raw_records:
            record = _usage_record(raw_record, default_currency=default_currency)
            if record is None:
                skipped += 1
                continue
            records.append(record)
    return UsageLog(baselines, tuple(records)), skipped


def _usage_record(raw: Any, *, default_currency: str | None = None) -> UsageRecord | None:
    if not isinstance(raw, Mapping):
        return None
    raw_time = raw.get("time")
    raw_currency = raw.get("currency", default_currency)
    try:
        if not isinstance(raw_time, str) or not raw_time:
            return None
        datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if not isinstance(raw_currency, str):
            return None
        return UsageRecord(raw_time, _currency(raw_currency), _decimal(raw.get("total_balance")))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _atomic_write_json(
    path: Path, value: Mapping[str, Any], *, before_replace: Callable[[], None] | None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temporary_path, path)
        replaced = True
    finally:
        if not replaced and temporary_path.exists():
            temporary_path.unlink()


def _read_json(path: Path) -> Any:
    def reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("JSON object contains duplicate fields")
            parsed[key] = value
        return parsed

    with path.open("r", encoding="utf-8") as source:
        try:
            return json.load(source, object_pairs_hook=reject_duplicate_fields)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Invalid JSON document") from None


def _validate_raw_read_back(path: Path, expected: Mapping[str, Any], label: str) -> None:
    if not _strict_json_equal(_read_json(path), expected):
        raise ValueError(f"{label} read-back validation failed")


def _strict_json_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, Mapping):
        return (
            set(actual) == set(expected)
            and all(_strict_json_equal(actual[key], expected[key]) for key in expected)
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_json_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer")
    return value


def _bounded_integer(value: Any, minimum: int, maximum: int, message: str) -> int:
    parsed = _integer(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(message)
    return parsed


def _optional_position(value: Any) -> int | None:
    if value is None:
        return None
    return _bounded_integer(
        value,
        WINDOW_POSITION_MIN,
        WINDOW_POSITION_MAX,
        "Expected a valid window position",
    )


def _opacity(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected an opacity number")
    try:
        normalized = float(value)
    except OverflowError:
        raise ValueError("Expected a valid opacity number") from None
    if not math.isfinite(normalized) or normalized < OPACITY_MIN or normalized > OPACITY_MAX:
        raise ValueError("Expected a valid opacity number")
    return normalized


def _decimal(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("Expected a finite decimal")
    return parsed


def _decimal_string(value: Any) -> str:
    return str(_decimal(value))


def _warning_decimal_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a valid balance warning")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError("Expected a valid balance warning") from None
    if not parsed.is_finite() or parsed < 0 or parsed > LOW_BALANCE_WARNING_MAX:
        raise ValueError("Expected a valid balance warning")
    return str(parsed)


def _currency(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a currency code")
    stripped = value.strip()
    normalized = stripped.upper()
    if (
        not normalized
        or len(normalized) > CURRENCY_MAX_LENGTH
        or not stripped.isascii()
        or not normalized.isalnum()
    ):
        raise ValueError("Expected a currency code")
    return normalized


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > SNAP_EDGE_MAX_LENGTH
    ):
        raise ValueError("Expected a valid optional string")
    return value


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Expected a boolean")
    return value


def _display_mode(value: Any) -> str:
    if value not in {"panel", "bubble", "taskbar"}:
        raise ValueError("Expected display_mode in panel/bubble/taskbar")
    return str(value)
