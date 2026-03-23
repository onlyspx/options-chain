import io
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts import spx_volume_daemon as daemon


ET = ZoneInfo("America/New_York")


class DaemonArgTests(unittest.TestCase):
    def test_default_interval_is_five_minutes(self):
        args = daemon.parse_args([])
        self.assertEqual(args.interval_min, 5)
        self.assertEqual(daemon.interval_seconds(args.interval_min), 300)
        self.assertEqual(args.top, 5)

    def test_five_minute_interval_maps_to_300_seconds(self):
        args = daemon.parse_args(["--interval-min", "5"])
        self.assertEqual(args.interval_min, 5)
        self.assertEqual(daemon.interval_seconds(args.interval_min), 300)

    def test_one_minute_override_still_maps_to_60_seconds(self):
        args = daemon.parse_args(["--interval-min", "1", "--top", "10"])
        self.assertEqual(args.interval_min, 1)
        self.assertEqual(daemon.interval_seconds(args.interval_min), 60)
        self.assertEqual(args.top, 10)

    def test_interval_zero_is_rejected(self):
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                daemon.parse_args(["--interval-min", "0"])


class MarketHoursTests(unittest.TestCase):
    def test_weekday_929_et_is_closed(self):
        self.assertFalse(daemon.is_market_hours(datetime(2026, 3, 2, 9, 29, tzinfo=ET)))

    def test_weekday_930_et_is_open(self):
        self.assertTrue(daemon.is_market_hours(datetime(2026, 3, 2, 9, 30, tzinfo=ET)))

    def test_weekday_1559_et_is_open(self):
        self.assertTrue(daemon.is_market_hours(datetime(2026, 3, 2, 15, 59, tzinfo=ET)))

    def test_weekday_1600_et_is_closed(self):
        self.assertFalse(daemon.is_market_hours(datetime(2026, 3, 2, 16, 0, tzinfo=ET)))

    def test_saturday_noon_is_closed(self):
        self.assertFalse(daemon.is_market_hours(datetime(2026, 3, 7, 12, 0, tzinfo=ET)))


class DeltaAndMessageTests(unittest.TestCase):
    def test_delta_rows_are_non_negative_and_sorted(self):
        current_rows = [
            {"osi": "SPX_A", "side": "CALL", "strike": 6000.0, "volume": 120},
            {"osi": "SPX_B", "side": "PUT", "strike": 5995.0, "volume": 60},
            {"osi": "SPX_C", "side": "CALL", "strike": 6010.0, "volume": None},
        ]
        previous = {"SPX_A": 100, "SPX_B": 70, "SPX_C": 5}

        delta_rows = daemon.compute_delta_rows(current_rows, previous)
        by_osi = {row["osi"]: row for row in delta_rows}

        self.assertEqual(delta_rows[0]["osi"], "SPX_A")
        self.assertEqual(by_osi["SPX_A"]["delta"], 20)
        self.assertEqual(by_osi["SPX_B"]["delta"], 0)
        self.assertEqual(by_osi["SPX_C"]["delta"], 0)
        self.assertTrue(all(row["delta"] >= 0 for row in delta_rows))

    def test_header_is_price_first_and_fallback_for_missing_quote(self):
        message = daemon.format_message(
            delta_rows=[],
            current_rows=[],
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=False,
            interval_min=5,
            top=5,
            spx_price=None,
        )
        first_line = message.splitlines()[0]
        self.assertTrue(first_line.startswith("SPX -- | 2026-03-02 10:01:00 EST |"))
        self.assertIn("exp 2026-03-02 (nearest fallback)", first_line)
        self.assertIn("window 5m", first_line)

    def test_no_change_message_has_two_sections_and_placeholders(self):
        message = daemon.format_message(
            delta_rows=[],
            current_rows=[],
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=False,
            interval_min=1,
            top=5,
            spx_price=None,
        )
        self.assertIn("📈 CALLS (Δ5m, strike ↑)", message)
        self.assertIn("📉 PUTS (Δ5m, strike ↓)", message)
        self.assertNotIn("📊 CALLS", message)
        self.assertNotIn("📊 PUTS", message)
        self.assertIn("• ⚪ No call delta > 0 in last 1 minute(s).", message)
        self.assertIn("• ⚪ No put delta > 0 in last 1 minute(s).", message)

    def test_rows_include_delta_and_volume(self):
        rows = [
            {"osi": "C1", "side": "CALL", "strike": 6800.0, "delta": 400, "volume": 5000},
            {"osi": "P1", "side": "PUT", "strike": 6790.0, "delta": 500, "volume": 6000},
        ]
        message = daemon.format_message(
            delta_rows=rows,
            current_rows=rows,
            timestamp_et=datetime(2026, 3, 3, 14, 47, 20, tzinfo=ET),
            expiration="2026-03-03",
            is_today=True,
            interval_min=5,
            top=5,
            spx_price=6837.22,
        )
        first_line = message.splitlines()[0]
        self.assertTrue(first_line.startswith("SPX 6,837.22 | 2026-03-03 14:47:20 EST |"))
        self.assertIn("• ⭐ 🟢 6800 Δ400 | Vol 5,000", message)
        self.assertIn("• ⭐ 🔴 6790 Δ500 | Vol 6,000", message)

    def test_star_marks_membership_in_overall_top_five_by_volume(self):
        delta_rows = [
            {"osi": "C1", "side": "CALL", "strike": 6800.0, "delta": 80, "volume": 50},
            {"osi": "P1", "side": "PUT", "strike": 6790.0, "delta": 90, "volume": 60},
        ]
        current_rows = [
            {"osi": "C1", "side": "CALL", "strike": 6800.0, "volume": 9999},
            {"osi": "P1", "side": "PUT", "strike": 6790.0, "volume": 8888},
        ]
        message = daemon.format_message(
            delta_rows=delta_rows,
            current_rows=current_rows,
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=5,
            spx_price=6800,
        )
        self.assertIn("• ⭐ 🟢 6800 Δ80 | Vol 50", message)
        self.assertIn("• ⭐ 🔴 6790 Δ90 | Vol 60", message)

    def test_no_star_when_delta_row_not_in_overall_top_five(self):
        delta_rows = [
            {"osi": "C_DELTA", "side": "CALL", "strike": 6800.0, "delta": 50, "volume": 10},
            {"osi": "P_DELTA", "side": "PUT", "strike": 6790.0, "delta": 60, "volume": 10},
        ]
        current_rows = [
            {"osi": "C_A", "side": "CALL", "strike": 6810.0, "volume": 1000},
            {"osi": "C_B", "side": "CALL", "strike": 6820.0, "volume": 900},
            {"osi": "C_C", "side": "CALL", "strike": 6830.0, "volume": 800},
            {"osi": "C_D", "side": "CALL", "strike": 6840.0, "volume": 700},
            {"osi": "C_E", "side": "CALL", "strike": 6850.0, "volume": 600},
            {"osi": "C_DELTA", "side": "CALL", "strike": 6800.0, "volume": 10},
            {"osi": "P_A", "side": "PUT", "strike": 6780.0, "volume": 1000},
            {"osi": "P_B", "side": "PUT", "strike": 6770.0, "volume": 900},
            {"osi": "P_C", "side": "PUT", "strike": 6760.0, "volume": 800},
            {"osi": "P_D", "side": "PUT", "strike": 6750.0, "volume": 700},
            {"osi": "P_E", "side": "PUT", "strike": 6740.0, "volume": 600},
            {"osi": "P_DELTA", "side": "PUT", "strike": 6790.0, "volume": 10},
        ]
        message = daemon.format_message(
            delta_rows=delta_rows,
            current_rows=current_rows,
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=5,
            spx_price=6800,
        )
        self.assertIn("• 🟢 6800 Δ50 | Vol 10", message)
        self.assertIn("• 🔴 6790 Δ60 | Vol 10", message)
        self.assertNotIn("• ⭐ 🟢 6800 Δ50 | Vol 10", message)
        self.assertNotIn("• ⭐ 🔴 6790 Δ60 | Vol 10", message)

    def test_calls_sort_ascending_and_puts_sort_descending_by_strike(self):
        delta_rows = [
            {"osi": "C3", "side": "CALL", "strike": 6810.0, "delta": 50, "volume": 100},
            {"osi": "C2", "side": "CALL", "strike": 6800.0, "delta": 100, "volume": 200},
            {"osi": "C1", "side": "CALL", "strike": 6790.0, "delta": 30, "volume": 300},
            {"osi": "P1", "side": "PUT", "strike": 6770.0, "delta": 10, "volume": 100},
            {"osi": "P2", "side": "PUT", "strike": 6780.0, "delta": 40, "volume": 200},
            {"osi": "P3", "side": "PUT", "strike": 6795.0, "delta": 120, "volume": 300},
        ]
        message = daemon.format_message(
            delta_rows=delta_rows,
            current_rows=delta_rows,
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=10,
            spx_price=6800,
        )
        self.assertLess(message.index("• ⭐ 🟢 6790 Δ30 | Vol 300"), message.index("• ⭐ 🟢 6800 Δ100 | Vol 200"))
        self.assertLess(message.index("• ⭐ 🟢 6800 Δ100 | Vol 200"), message.index("• ⭐ 🟢 6810 Δ50 | Vol 100"))
        self.assertLess(message.index("• ⭐ 🔴 6795 Δ120 | Vol 300"), message.index("• ⭐ 🔴 6780 Δ40 | Vol 200"))
        self.assertLess(message.index("• ⭐ 🔴 6780 Δ40 | Vol 200"), message.index("• ⭐ 🔴 6770 Δ10 | Vol 100"))

    def test_top_limit_applies_per_side(self):
        delta_rows = [
            {"osi": "C1", "side": "CALL", "strike": 6800.0, "delta": 100, "volume": 100},
            {"osi": "C2", "side": "CALL", "strike": 6810.0, "delta": 90, "volume": 90},
            {"osi": "C3", "side": "CALL", "strike": 6820.0, "delta": 80, "volume": 80},
            {"osi": "P1", "side": "PUT", "strike": 6790.0, "delta": 100, "volume": 100},
            {"osi": "P2", "side": "PUT", "strike": 6780.0, "delta": 90, "volume": 90},
            {"osi": "P3", "side": "PUT", "strike": 6770.0, "delta": 80, "volume": 80},
        ]
        message = daemon.format_message(
            delta_rows=delta_rows,
            current_rows=delta_rows,
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=2,
            spx_price=6800,
        )
        lines = message.splitlines()
        call_rows = [line for line in lines if line.startswith("• ") and "🟢" in line]
        put_rows = [line for line in lines if line.startswith("• ") and "🔴" in line]
        self.assertEqual(len(call_rows), 2)
        self.assertEqual(len(put_rows), 2)

    def test_zero_delta_rows_are_hidden(self):
        message = daemon.format_message(
            delta_rows=[
                {"osi": "C0", "side": "CALL", "strike": 6800.0, "delta": 0, "volume": 999},
                {"osi": "C1", "side": "CALL", "strike": 6810.0, "delta": 20, "volume": 500},
            ],
            current_rows=[
                {"osi": "C0", "side": "CALL", "strike": 6800.0, "volume": 999},
                {"osi": "C1", "side": "CALL", "strike": 6810.0, "volume": 500},
            ],
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=5,
            spx_price=6800,
        )
        self.assertIn("• ⭐ 🟢 6810 Δ20 | Vol 500", message)
        self.assertNotIn("6800 Δ0", message)

    def test_unknown_strike_is_placed_last_within_section(self):
        delta_rows = [
            {"osi": "C1", "side": "CALL", "strike": None, "delta": 110, "volume": 900},
            {"osi": "C2", "side": "CALL", "strike": 6800.0, "delta": 100, "volume": 800},
            {"osi": "P1", "side": "PUT", "strike": None, "delta": 120, "volume": 900},
            {"osi": "P2", "side": "PUT", "strike": 6790.0, "delta": 110, "volume": 800},
        ]
        message = daemon.format_message(
            delta_rows=delta_rows,
            current_rows=delta_rows,
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=5,
            spx_price=6800,
        )
        self.assertLess(
            message.index("• ⭐ 🟢 6800 Δ100 | Vol 800"),
            message.index("• 🟢 -- Δ110 | Vol 900"),
        )
        self.assertLess(
            message.index("• ⭐ 🔴 6790 Δ110 | Vol 800"),
            message.index("• 🔴 -- Δ120 | Vol 900"),
        )

    def test_empty_side_placeholders_show_per_side(self):
        only_puts = daemon.format_message(
            delta_rows=[{"osi": "P1", "side": "PUT", "strike": 6790.0, "delta": 100, "volume": 1000}],
            current_rows=[{"osi": "P1", "side": "PUT", "strike": 6790.0, "volume": 1000}],
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=10,
            spx_price=6800,
        )
        only_calls = daemon.format_message(
            delta_rows=[{"osi": "C1", "side": "CALL", "strike": 6800.0, "delta": 100, "volume": 1000}],
            current_rows=[{"osi": "C1", "side": "CALL", "strike": 6800.0, "volume": 1000}],
            timestamp_et=datetime(2026, 3, 2, 10, 1, tzinfo=ET),
            expiration="2026-03-02",
            is_today=True,
            interval_min=5,
            top=10,
            spx_price=6800,
        )
        self.assertIn("• ⚪ No call delta > 0 in last 5 minute(s).", only_puts)
        self.assertIn("• ⚪ No put delta > 0 in last 5 minute(s).", only_calls)

    def test_webhook_payload_has_content_and_length_guard(self):
        payload = daemon.build_webhook_payload("x" * 2500)
        self.assertIn("content", payload)
        self.assertLessEqual(len(payload["content"]), daemon.MAX_DISCORD_CONTENT)
        self.assertTrue(payload["content"].endswith("... (truncated)"))


if __name__ == "__main__":
    unittest.main()
