from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from deepseek_balance_monitor.peak import is_peak_hour, peak_label, to_shanghai


class PeakScheduleTests(unittest.TestCase):
    def test_exact_beijing_boundaries(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        cases = {
            datetime(2026, 8, 5, 8, 59, 59, tzinfo=tz): False,
            datetime(2026, 8, 5, 9, 0, 0, tzinfo=tz): True,
            datetime(2026, 8, 5, 11, 59, 59, tzinfo=tz): True,
            datetime(2026, 8, 5, 12, 0, 0, tzinfo=tz): False,
            datetime(2026, 8, 5, 13, 59, 59, tzinfo=tz): False,
            datetime(2026, 8, 5, 14, 0, 0, tzinfo=tz): True,
            datetime(2026, 8, 5, 17, 59, 59, tzinfo=tz): True,
            datetime(2026, 8, 5, 18, 0, 0, tzinfo=tz): False,
        }
        for moment, expected in cases.items():
            with self.subTest(moment=moment):
                self.assertIs(is_peak_hour(moment), expected)

    def test_foreign_timezone_is_converted_to_beijing(self) -> None:
        self.assertTrue(is_peak_hour(datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)))
        converted = to_shanghai(datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(converted.hour, 9)

    def test_naive_injected_time_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            is_peak_hour(datetime(2026, 8, 5, 9, 0))

    def test_weekends_are_always_off_peak(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        self.assertFalse(is_peak_hour(datetime(2026, 8, 8, 9, 30, tzinfo=tz)))  # 周六高峰段
        self.assertFalse(is_peak_hour(datetime(2026, 8, 8, 14, 30, tzinfo=tz)))  # 周六高峰段
        self.assertFalse(is_peak_hour(datetime(2026, 8, 9, 9, 0, tzinfo=tz)))  # 周日高峰起点
        self.assertTrue(is_peak_hour(datetime(2026, 8, 7, 9, 30, tzinfo=tz)))  # 周五高峰段仍生效

    def test_labels_are_exact(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        self.assertEqual(
            peak_label(datetime(2026, 8, 5, 9, 0, tzinfo=tz)),
            ("▲ 高峰期 · 双倍消耗", True),
        )
        self.assertEqual(
            peak_label(datetime(2026, 8, 5, 12, 0, tzinfo=tz)),
            ("● 非高峰 · 正常计费", False),
        )


if __name__ == "__main__":
    unittest.main()
