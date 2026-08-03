from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deepseek_balance_monitor.storage import (
    AppConfig,
    AppPaths,
    CURRENCY_MAX_LENGTH,
    LOW_BALANCE_WARNING_MAX,
    MigrationResult,
    OPACITY_MAX,
    OPACITY_MIN,
    REFRESH_INTERVAL_MAX_SECONDS,
    REFRESH_INTERVAL_MIN_SECONDS,
    SNAP_EDGE_MAX_LENGTH,
    UsageBaseline,
    UsageLog,
    UsageRecord,
    WINDOW_POSITION_MAX,
    WINDOW_POSITION_MIN,
    load_config,
    load_usage_log,
    migrate_legacy,
    save_config_atomic,
    save_usage_log_atomic,
)


class AppPathsTests(unittest.TestCase):
    def test_from_environment_requires_localappdata_and_uses_private_root(self) -> None:
        paths = AppPaths.from_environment({"LOCALAPPDATA": r"C:\\Users\\Test\\AppData\\Local"})

        self.assertEqual(
            paths.config_path,
            Path(r"C:\\Users\\Test\\AppData\\Local") / "DeepSeekBalanceMonitor" / "config.json",
        )
        self.assertEqual(paths.usage_log_path.name, "usage_log.json")
        with self.assertRaises(ValueError):
            AppPaths.from_environment({})
        with self.assertRaises(ValueError):
            AppPaths.from_environment({"LOCALAPPDATA": "   "})


class ConfigStorageTests(unittest.TestCase):
    def test_config_validation_limits_are_explicit(self) -> None:
        self.assertEqual(REFRESH_INTERVAL_MIN_SECONDS, 10)
        self.assertEqual(REFRESH_INTERVAL_MAX_SECONDS, 86_400)
        self.assertEqual((OPACITY_MIN, OPACITY_MAX), (0.2, 1.0))
        self.assertEqual((WINDOW_POSITION_MIN, WINDOW_POSITION_MAX), (-100_000, 100_000))
        self.assertEqual(LOW_BALANCE_WARNING_MAX, Decimal("1e12"))
        self.assertEqual(CURRENCY_MAX_LENGTH, 12)
        self.assertEqual(SNAP_EDGE_MAX_LENGTH, 32)

    def test_load_config_returns_safe_defaults_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(Path(directory) / "missing.json")

        self.assertEqual(config.refresh_interval, 60)
        self.assertIsNone(config.window_x)
        self.assertIsNone(config.window_y)
        self.assertEqual(config.opacity, 0.9)
        self.assertEqual(config.low_balance_warning, "10.00")
        self.assertEqual(config.currency, "CNY")
        self.assertIsNone(config.snap_edge)
        self.assertFalse(config.collapsed)
        self.assertFalse(config.auto_start)

    def test_load_config_rejects_exact_duplicate_fields_without_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                '{"currency":"USD","currency":"synthetic-duplicate-secret"}',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as captured:
                load_config(path)

        self.assertEqual(str(captured.exception), "JSON object contains duplicate fields")
        self.assertNotIn("currency", str(captured.exception))
        self.assertNotIn("synthetic-duplicate-secret", str(captured.exception))

    def test_load_config_rejects_unknown_fields_without_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = '{"refresh_interval":60,"api_key":"synthetic-secret"}'
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(ValueError) as captured:
                load_config(path)

            self.assertEqual(path.read_text(encoding="utf-8"), original)

        self.assertEqual(str(captured.exception), "Config contains unsupported fields")
        self.assertNotIn("api_key", str(captured.exception))
        self.assertNotIn("synthetic-secret", str(captured.exception))

    def test_config_validation_accepts_documented_boundaries(self) -> None:
        configs = (
            AppConfig(
                refresh_interval=REFRESH_INTERVAL_MIN_SECONDS,
                window_x=WINDOW_POSITION_MIN,
                window_y=WINDOW_POSITION_MIN,
                opacity=OPACITY_MIN,
                low_balance_warning="0",
                currency="a",
                snap_edge="x",
            ),
            AppConfig(
                refresh_interval=REFRESH_INTERVAL_MAX_SECONDS,
                window_x=WINDOW_POSITION_MAX,
                window_y=WINDOW_POSITION_MAX,
                opacity=OPACITY_MAX,
                low_balance_warning=str(LOW_BALANCE_WARNING_MAX),
                currency="a1b2c3d4e5f6",
                snap_edge="x" * SNAP_EDGE_MAX_LENGTH,
                collapsed=True,
                auto_start=True,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for config in configs:
                with self.subTest(config=config):
                    save_config_atomic(path, config)
                    self.assertEqual(
                        load_config(path),
                        dataclasses.replace(config, currency=config.currency.upper()),
                    )

    def test_load_config_rejects_invalid_field_values_without_disclosure(self) -> None:
        invalid_fields = (
            ("refresh_interval", REFRESH_INTERVAL_MIN_SECONDS - 1),
            ("refresh_interval", REFRESH_INTERVAL_MAX_SECONDS + 1),
            ("window_x", WINDOW_POSITION_MIN - 1),
            ("window_y", WINDOW_POSITION_MAX + 1),
            ("opacity", OPACITY_MIN - 0.01),
            ("opacity", OPACITY_MAX + 0.01),
            ("low_balance_warning", -1),
            ("low_balance_warning", "-0.01"),
            ("low_balance_warning", "NaN"),
            ("low_balance_warning", "1000000000000.01"),
            ("currency", "a1b2c3d4e5f6x"),
            ("currency", "C-NY"),
            ("currency", "人民币"),
            ("currency", "ß"),
            ("snap_edge", ""),
            ("snap_edge", "x" * (SNAP_EDGE_MAX_LENGTH + 1)),
            ("collapsed", 1),
            ("auto_start", 0),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for field_name, invalid_value in invalid_fields:
                with self.subTest(field=field_name, value_type=type(invalid_value).__name__):
                    path.write_text(json.dumps({field_name: invalid_value}), encoding="utf-8")
                    with self.assertRaises(ValueError) as captured:
                        load_config(path)
                    if str(invalid_value):
                        self.assertNotIn(str(invalid_value), str(captured.exception))

    def test_invalid_config_boundaries_never_replace_existing_destination(self) -> None:
        invalid_configs = (
            AppConfig(refresh_interval=REFRESH_INTERVAL_MIN_SECONDS - 1),
            AppConfig(refresh_interval=REFRESH_INTERVAL_MAX_SECONDS + 1),
            AppConfig(window_x=WINDOW_POSITION_MIN - 1),
            AppConfig(window_y=WINDOW_POSITION_MAX + 1),
            AppConfig(opacity=OPACITY_MIN - 0.01),
            AppConfig(opacity=OPACITY_MAX + 0.01),
            AppConfig(low_balance_warning=-1),
            AppConfig(low_balance_warning="-0.01"),
            AppConfig(low_balance_warning="1000000000000.01"),
            AppConfig(currency="x" * (CURRENCY_MAX_LENGTH + 1)),
            AppConfig(currency="C-NY"),
            AppConfig(currency="ß"),
            AppConfig(snap_edge=""),
            AppConfig(snap_edge="x" * (SNAP_EDGE_MAX_LENGTH + 1)),
            AppConfig(collapsed=1),
            AppConfig(auto_start=0),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config_atomic(path, AppConfig(refresh_interval=45))
            old = path.read_text(encoding="utf-8")

            for invalid in invalid_configs:
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        save_config_atomic(path, invalid)
                    self.assertEqual(path.read_text(encoding="utf-8"), old)
                    self.assertEqual(list(Path(directory).glob(".config.json.*.tmp")), [])

    def test_save_config_persists_only_allowlisted_non_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config_atomic(
                path,
                AppConfig(
                    refresh_interval=30,
                    window_x=10,
                    window_y=20,
                    opacity=0.8,
                    low_balance_warning="7.50",
                    currency="USD",
                    snap_edge="right",
                    collapsed=True,
                    auto_start=True,
                ),
            )
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            saved,
            {
                "refresh_interval": 30,
                "window_x": 10,
                "window_y": 20,
                "opacity": 0.8,
                "low_balance_warning": "7.50",
                "currency": "USD",
                "snap_edge": "right",
                "collapsed": True,
                "auto_start": True,
            },
        )
        self.assertNotIn("api_key", saved)
        self.assertEqual(len(saved), 9)

    def test_pre_replace_crash_preserves_old_config_and_removes_own_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"refresh_interval": 60}', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "injected crash"):
                save_config_atomic(
                    path,
                    AppConfig(refresh_interval=15),
                    before_replace=lambda: (_ for _ in ()).throw(RuntimeError("injected crash")),
                )

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"refresh_interval": 60})
            self.assertEqual(list(Path(directory).glob(".config.json.*.tmp")), [])

    def test_save_config_rejects_failed_read_back_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = AppConfig(refresh_interval=15)

            with patch("deepseek_balance_monitor.storage._read_json", side_effect=ValueError("bad read")):
                with self.assertRaisesRegex(ValueError, "bad read"):
                    save_config_atomic(path, config)

            self.assertTrue(path.exists())

    def test_invalid_config_never_replaces_existing_destination(self) -> None:
        invalid_configs = (
            AppConfig(refresh_interval=True),
            AppConfig(opacity=float("nan")),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for invalid in invalid_configs:
                with self.subTest(invalid=invalid):
                    save_config_atomic(path, AppConfig(refresh_interval=45))
                    old = path.read_text(encoding="utf-8")

                    with self.assertRaises(ValueError):
                        save_config_atomic(path, invalid)

                    self.assertEqual(path.read_text(encoding="utf-8"), old)
                    self.assertEqual(list(Path(directory).glob(".config.json.*.tmp")), [])

    def test_config_read_back_rejects_unexpected_raw_field(self) -> None:
        real_replace = __import__("os").replace

        def inject_forbidden_field(source: str, destination: str) -> None:
            real_replace(source, destination)
            path = Path(destination)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["api_key"] = "injected-not-a-secret"
            path.write_text(json.dumps(payload), encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch("deepseek_balance_monitor.storage.os.replace", side_effect=inject_forbidden_field):
                with self.assertRaisesRegex(ValueError, "read-back validation"):
                    save_config_atomic(path, AppConfig(refresh_interval=15))

            self.assertIn("api_key", json.loads(path.read_text(encoding="utf-8")))


class UsageLogStorageTests(unittest.TestCase):
    def test_usage_log_round_trip_uses_schema_v2_decimal_strings_and_currency(self) -> None:
        log = UsageLog(
            baselines={"CNY": UsageBaseline(date(2026, 8, 1), Decimal("12.30"))},
            records=(UsageRecord("2026-08-01T12:00:00+00:00", "CNY", Decimal("11.80")),),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage_log.json"
            save_usage_log_atomic(path, log)
            saved = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_usage_log(path)

        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["baselines"]["CNY"], {"date": "2026-08-01", "total_balance": "12.30"})
        self.assertEqual(saved["records"][0], {"time": "2026-08-01T12:00:00+00:00", "currency": "CNY", "total_balance": "11.80"})
        self.assertEqual(loaded, log)

    def test_pre_replace_crash_preserves_old_usage_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage_log.json"
            old = '{"schema_version": 2, "baselines": {}, "records": []}'
            path.write_text(old, encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "injected crash"):
                save_usage_log_atomic(
                    path,
                    UsageLog(),
                    before_replace=lambda: (_ for _ in ()).throw(RuntimeError("injected crash")),
                )

            self.assertEqual(path.read_text(encoding="utf-8"), old)
            self.assertEqual(list(Path(directory).glob(".usage_log.json.*.tmp")), [])

    def test_invalid_usage_log_never_replaces_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage_log.json"
            save_usage_log_atomic(path, UsageLog())
            old = path.read_text(encoding="utf-8")
            invalid = UsageLog(
                records=(UsageRecord("not-an-iso-timestamp", "CNY", Decimal("1.00")),),
            )

            with self.assertRaises(ValueError):
                save_usage_log_atomic(path, invalid)

            self.assertEqual(path.read_text(encoding="utf-8"), old)
            self.assertEqual(list(Path(directory).glob(".usage_log.json.*.tmp")), [])

    def test_normalized_baseline_currency_collision_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage_log.json"
            save_usage_log_atomic(path, UsageLog())
            old = path.read_text(encoding="utf-8")
            colliding = UsageLog(
                baselines={
                    "usd": UsageBaseline(date(2026, 8, 1), Decimal("123.45")),
                    "USD": UsageBaseline(date(2026, 8, 2), Decimal("678.90")),
                },
            )

            with self.assertRaises(ValueError) as captured:
                save_usage_log_atomic(path, colliding)

            self.assertEqual(str(captured.exception), "Usage baseline currency collision")
            self.assertNotIn("123.45", str(captured.exception))
            self.assertNotIn("678.90", str(captured.exception))
            self.assertEqual(path.read_text(encoding="utf-8"), old)
            self.assertEqual(list(Path(directory).glob(".usage_log.json.*.tmp")), [])

    def test_load_rejects_normalized_baseline_currency_collision_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage_log.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "baselines": {
                            "usd": {"date": "2026-08-01", "total_balance": "123.45"},
                            "USD": {"date": "2026-08-02", "total_balance": "678.90"},
                        },
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as captured:
                load_usage_log(path)

        self.assertEqual(str(captured.exception), "Usage baseline currency collision")
        self.assertNotIn("123.45", str(captured.exception))
        self.assertNotIn("678.90", str(captured.exception))

    def test_load_rejects_nested_duplicate_usage_fields_without_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage_log.json"
            path.write_text(
                """{
                    "schema_version": 2,
                    "baselines": {},
                    "records": [{
                        "time": "2026-08-01T01:00:00+00:00",
                        "currency": "CNY",
                        "total_balance": "1.00",
                        "total_balance": "synthetic-duplicate-secret"
                    }]
                }""",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as captured:
                load_usage_log(path)

        self.assertEqual(str(captured.exception), "JSON object contains duplicate fields")
        self.assertNotIn("total_balance", str(captured.exception))
        self.assertNotIn("synthetic-duplicate-secret", str(captured.exception))


class LegacyMigrationTests(unittest.TestCase):
    def test_migration_result_keeps_backward_compatible_defaults(self) -> None:
        self.assertEqual(MigrationResult().skipped_config_fields, 0)

    def test_legacy_config_skips_invalid_fields_independently(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            legacy_config = {
                "refresh_interval": 30,
                "window_x": WINDOW_POSITION_MIN - 1,
                "window_y": 99,
                "opacity": "synthetic-invalid-opacity",
                "low_balance_warning": "-0.01",
                "currency": "synthetic-invalid-currency!",
                "snap_edge": "left",
                "collapsed": 1,
                "auto_start": True,
            }
            source_text = json.dumps(legacy_config)
            (source / "config.json").write_text(source_text, encoding="utf-8")

            result = migrate_legacy(source, paths)
            migrated = load_config(paths.config_path)

            self.assertEqual((source / "config.json").read_text(encoding="utf-8"), source_text)

        self.assertTrue(result.config_migrated)
        self.assertFalse(result.usage_migrated)
        self.assertEqual(result.skipped_config_fields, 5)
        self.assertEqual(migrated.refresh_interval, 30)
        self.assertIsNone(migrated.window_x)
        self.assertEqual(migrated.window_y, 99)
        self.assertEqual(migrated.opacity, AppConfig().opacity)
        self.assertEqual(migrated.low_balance_warning, AppConfig().low_balance_warning)
        self.assertEqual(migrated.currency, "CNY")
        self.assertEqual(migrated.snap_edge, "left")
        self.assertFalse(migrated.collapsed)
        self.assertFalse(migrated.auto_start)
        self.assertNotIn("synthetic-invalid", repr(result))

    def test_legacy_config_skips_oversized_integer_opacity_independently(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            (source / "config.json").write_text(
                json.dumps(
                    {
                        "refresh_interval": 30,
                        "window_x": 42,
                        "opacity": int("9" * 400),
                        "currency": "usd",
                    }
                ),
                encoding="utf-8",
            )

            try:
                result = migrate_legacy(source, paths)
            except OverflowError:
                self.fail("legacy migration must skip an oversized opacity")
            migrated = load_config(paths.config_path)

        self.assertTrue(result.config_migrated)
        self.assertEqual(result.skipped_config_fields, 1)
        self.assertEqual(migrated.refresh_interval, 30)
        self.assertEqual(migrated.window_x, 42)
        self.assertEqual(migrated.opacity, AppConfig().opacity)
        self.assertEqual(migrated.currency, "USD")

    def test_non_object_legacy_config_fails_safely_without_targets(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            source_text = '["synthetic-secret"]'
            (source / "config.json").write_text(source_text, encoding="utf-8")
            (source / "usage_log.json").write_text('{"records":[]}', encoding="utf-8")

            with self.assertRaises(ValueError) as captured:
                migrate_legacy(source, paths)

            self.assertFalse(paths.config_path.exists())
            self.assertFalse(paths.usage_log_path.exists())
            self.assertEqual((source / "config.json").read_text(encoding="utf-8"), source_text)

        self.assertEqual(str(captured.exception), "Legacy config must be a JSON object")
        self.assertNotIn("synthetic-secret", str(captured.exception))

    def test_invalid_legacy_currency_does_not_label_currencyless_usage_as_cny(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            (source / "config.json").write_text(
                '{"currency":"synthetic-invalid-currency!"}', encoding="utf-8"
            )
            (source / "usage_log.json").write_text(
                json.dumps(
                    {
                        "baselines": {"2026-08-01": "10.00"},
                        "records": [
                            {"time": "2026-08-01T01:00:00+00:00", "total_balance": "9.00"},
                            {
                                "time": "2026-08-01T02:00:00+00:00",
                                "currency": "usd",
                                "total_balance": "8.00",
                            },
                            {
                                "time": "2026-08-01T03:00:00+00:00",
                                "currency": "synthetic-invalid-record!",
                                "total_balance": "7.00",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = migrate_legacy(source, paths)
            migrated_config = load_config(paths.config_path)
            migrated_usage = load_usage_log(paths.usage_log_path)

        self.assertEqual(result.skipped_config_fields, 1)
        self.assertEqual(result.skipped_usage_records, 3)
        self.assertEqual(migrated_config.currency, "CNY")
        self.assertEqual(migrated_usage.baselines, {})
        self.assertEqual(len(migrated_usage.records), 1)
        self.assertEqual(migrated_usage.records[0].currency, "USD")
        self.assertNotIn("synthetic-invalid", repr(result))

    def test_missing_legacy_currency_uses_known_cny_default(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            (source / "config.json").write_text("{}", encoding="utf-8")
            (source / "usage_log.json").write_text(
                json.dumps(
                    {
                        "baselines": {"2026-08-01": "10.00"},
                        "records": [
                            {"time": "2026-08-01T01:00:00+00:00", "total_balance": "9.00"}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = migrate_legacy(source, paths)
            migrated = load_usage_log(paths.usage_log_path)

        self.assertEqual(result.skipped_config_fields, 0)
        self.assertEqual(result.skipped_usage_records, 0)
        self.assertIn("CNY", migrated.baselines)
        self.assertEqual(migrated.records[0].currency, "CNY")

    def test_legacy_baseline_uses_newest_valid_date_independent_of_json_order(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            (source / "config.json").write_text('{"currency":"usd"}', encoding="utf-8")
            (source / "usage_log.json").write_text(
                json.dumps(
                    {
                        "baselines": {
                            "2026-08-03": "7.00",
                            "not-a-date": "synthetic-invalid-balance",
                            "2026-08-01": "9.00",
                            "2026-08-02": "8.00",
                        },
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )

            result = migrate_legacy(source, paths)
            migrated = load_usage_log(paths.usage_log_path)

        self.assertEqual(result.skipped_usage_records, 1)
        self.assertEqual(migrated.baselines["USD"].date, date(2026, 8, 3))
        self.assertEqual(migrated.baselines["USD"].total_balance, Decimal("7.00"))

    def test_malformed_legacy_usage_is_generic_and_does_not_create_half_migration(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            config_text = '{"currency":"USD"}'
            usage_text = '{"records":["synthetic-secret"'
            (source / "config.json").write_text(config_text, encoding="utf-8")
            (source / "usage_log.json").write_text(usage_text, encoding="utf-8")

            with self.assertRaises(ValueError) as captured:
                migrate_legacy(source, paths)

            self.assertFalse(paths.config_path.exists())
            self.assertFalse(paths.usage_log_path.exists())
            self.assertEqual((source / "config.json").read_text(encoding="utf-8"), config_text)
            self.assertEqual((source / "usage_log.json").read_text(encoding="utf-8"), usage_text)

        self.assertEqual(str(captured.exception), "Invalid JSON document")
        self.assertNotIn("synthetic-secret", str(captured.exception))

    def test_duplicate_legacy_field_preserves_existing_target_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            source_text = '{"currency":"USD","currency":"synthetic-duplicate-secret"}'
            (source / "config.json").write_text(source_text, encoding="utf-8")
            (source / "usage_log.json").write_text('{"records":[]}', encoding="utf-8")
            save_config_atomic(paths.config_path, AppConfig(refresh_interval=45))
            old_target = paths.config_path.read_text(encoding="utf-8")

            with self.assertRaises(ValueError) as captured:
                migrate_legacy(source, paths)

            self.assertEqual(paths.config_path.read_text(encoding="utf-8"), old_target)
            self.assertFalse(paths.usage_log_path.exists())
            self.assertEqual((source / "config.json").read_text(encoding="utf-8"), source_text)

        self.assertEqual(str(captured.exception), "JSON object contains duplicate fields")
        self.assertNotIn("currency", str(captured.exception))
        self.assertNotIn("synthetic-duplicate-secret", str(captured.exception))

    def test_migration_is_idempotent_sanitizes_config_and_converts_legacy_usage_per_currency(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            legacy_config = {
                "refresh_interval": 25,
                "window_x": 1,
                "window_y": 2,
                "opacity": 0.75,
                "low_balance_warning": "3.40",
                "currency": "usd",
                "snap_edge": "left",
                "collapsed": True,
                "api_key": "synthetic-secret-never-copy",
                "unknown": "discard-me",
                "auto_start": True,
            }
            legacy_usage = {
                "baselines": {"2026-08-01": 12.5},
                "records": [
                    {"time": "2026-08-01T01:00:00+00:00", "total_balance": 12.5},
                    {"time": "bad-time", "total_balance": "not-a-decimal"},
                ],
            }
            (source / "config.json").write_text(json.dumps(legacy_config), encoding="utf-8")
            (source / "usage_log.json").write_text(json.dumps(legacy_usage), encoding="utf-8")

            first = migrate_legacy(source, paths)
            second = migrate_legacy(source, paths)
            migrated_config = json.loads(paths.config_path.read_text(encoding="utf-8"))
            migrated_usage = json.loads(paths.usage_log_path.read_text(encoding="utf-8"))
            migrated_config_text = paths.config_path.read_text(encoding="utf-8")

        self.assertTrue(first.config_migrated)
        self.assertTrue(first.usage_migrated)
        self.assertFalse(second.config_migrated)
        self.assertFalse(second.usage_migrated)
        self.assertEqual(first.skipped_usage_records, 1)
        self.assertEqual(migrated_config["currency"], "USD")
        self.assertFalse(migrated_config["auto_start"])
        self.assertEqual(set(migrated_config), {
            "refresh_interval", "window_x", "window_y", "opacity", "low_balance_warning",
            "currency", "snap_edge", "collapsed", "auto_start",
        })
        self.assertNotIn("synthetic-secret-never-copy", migrated_config_text)
        self.assertEqual(migrated_usage, {
            "schema_version": 2,
            "baselines": {"USD": {"date": "2026-08-01", "total_balance": "12.5"}},
            "records": [{"time": "2026-08-01T01:00:00+00:00", "currency": "USD", "total_balance": "12.5"}],
        })
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.config_migrated = False

    def test_migration_uses_source_config_currency_when_target_config_exists(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            paths = AppPaths(Path(target_directory))
            (source / "config.json").write_text('{"currency": "usd"}', encoding="utf-8")
            (source / "usage_log.json").write_text(
                '{"records": [{"time": "2026-08-01T01:00:00+00:00", "total_balance": "2.50"}]}',
                encoding="utf-8",
            )
            save_config_atomic(paths.config_path, AppConfig(currency="CNY"))

            result = migrate_legacy(source, paths)
            migrated = load_usage_log(paths.usage_log_path)

        self.assertFalse(result.config_migrated)
        self.assertTrue(result.usage_migrated)
        self.assertEqual(migrated.records[0].currency, "USD")


if __name__ == "__main__":
    unittest.main()
