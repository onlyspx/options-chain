import unittest
from datetime import date

import web.server.main as server_main


class StraddleMonitorHelperTests(unittest.TestCase):
    def test_monitor_expirations_keep_same_day_then_future_order(self):
        today = date(2026, 3, 20)  # Friday
        expirations = [
            date(2026, 3, 20),
            date(2026, 3, 21),
            date(2026, 3, 24),
            date(2026, 3, 27),
        ]

        ordered = server_main._monitor_expirations_from_dates(expirations, today=today, symbol="SPX", row_limit=3)
        self.assertEqual(ordered, ["2026-03-20", "2026-03-21", "2026-03-24"])

    def test_build_straddle_monitor_row_uses_strike_mids_and_iv_ratio(self):
        by_strike = {
            6100.0: {
                "strike": 6100.0,
                "call_bid": 20.0,
                "call_ask": 22.0,
                "put_bid": 19.0,
                "put_ask": 21.0,
                "call_osi": "CALL6100",
                "put_osi": "PUT6100",
            }
        }
        greeks = {
            "CALL6100": {"implied_volatility": 0.18},
            "PUT6100": {"implied_volatility": 0.21},
        }

        row = server_main._build_straddle_monitor_row(
            by_strike=by_strike,
            greeks_by_osi=greeks,
            symbol_price=6104.0,
            expiration="2026-03-20",
        )

        self.assertEqual(row["strike"], 6100.0)
        self.assertEqual(row["call_mid"], 21.0)
        self.assertEqual(row["put_mid"], 20.0)
        self.assertEqual(row["straddle_mid"], 41.0)
        self.assertAlmostEqual(row["implied_move_pct"], round(41.0 / 6104.0, 6))
        self.assertEqual(row["iv"], 0.195)
        self.assertEqual(row["put_call_skew"], 1.1667)

    def test_shape_straddle_history_groups_only_front_dtes(self):
        rows = [
            {
                "days_to_expiry": 0,
                "bucket_ts": "2026-03-20T13:30:00+00:00",
                "straddle_mid": 50.12,
                "spot": 6100.5,
                "strike": 6100.0,
                "expiration": "2026-03-20",
            },
            {
                "days_to_expiry": 1,
                "bucket_ts": "2026-03-20T13:31:00+00:00",
                "straddle_mid": 62.34,
                "spot": 6102.0,
                "strike": 6100.0,
                "expiration": "2026-03-21",
            },
            {
                "days_to_expiry": 3,
                "bucket_ts": "2026-03-20T13:31:00+00:00",
                "straddle_mid": 80.0,
                "spot": 6102.0,
                "strike": 6100.0,
                "expiration": "2026-03-24",
            },
        ]

        shaped = server_main._shape_straddle_history(rows)
        self.assertEqual(len(shaped["0dte"]), 1)
        self.assertEqual(len(shaped["1dte"]), 1)
        self.assertEqual(shaped["0dte"][0]["value"], 50.12)
        self.assertEqual(shaped["1dte"][0]["expiration"], "2026-03-21")


class StraddleMonitorEndpointTests(unittest.TestCase):
    def setUp(self):
        self.orig_fetch_straddle_monitor = server_main._fetch_straddle_monitor

    def tearDown(self):
        server_main._fetch_straddle_monitor = self.orig_fetch_straddle_monitor

    def test_endpoint_passes_rows_through_to_fetcher(self):
        calls = []

        def fake_fetch_straddle_monitor(row_limit=None):
            calls.append(row_limit)
            return {
                "symbol": "SPX",
                "rows": [],
                "history": {"0dte": [], "1dte": []},
            }

        server_main._fetch_straddle_monitor = fake_fetch_straddle_monitor

        server_main.get_straddle_monitor()
        server_main.get_straddle_monitor(rows=6)

        self.assertEqual(calls, [None, 6])


if __name__ == "__main__":
    unittest.main()
