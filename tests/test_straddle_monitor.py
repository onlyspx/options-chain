import unittest
from datetime import date, datetime, timezone

import web.server.main as server_main


class StraddleMonitorHelperTests(unittest.TestCase):
    def setUp(self):
        self.orig_response_cache = dict(server_main._straddle_monitor_response_cache)
        server_main._straddle_monitor_response_cache.clear()

    def tearDown(self):
        server_main._straddle_monitor_response_cache.clear()
        server_main._straddle_monitor_response_cache.update(self.orig_response_cache)

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

    def test_build_straddle_daily_close_write_payload_keeps_close_metrics(self):
        row = {
            "days_to_expiry": 0,
            "expiration": "2026-03-20",
            "strike": 6100.0,
            "straddle_mid": 41.0,
            "implied_move_pct": 0.006717,
            "put_call_skew": 1.1667,
            "iv": 0.195,
        }

        payload = server_main._build_straddle_daily_close_write_payload(
            row=row,
            symbol="SPX",
            spot=6104.0,
            session_date=date(2026, 3, 20),
            captured_at=datetime(2026, 3, 20, 20, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["symbol"], "SPX")
        self.assertEqual(payload["session_date"], "2026-03-20")
        self.assertEqual(payload["expiration"], "2026-03-20")
        self.assertEqual(payload["strike"], 6100.0)
        self.assertEqual(payload["spot"], 6104.0)
        self.assertEqual(payload["straddle_mid"], 41.0)
        self.assertEqual(payload["put_call_skew"], 1.1667)
        self.assertEqual(payload["iv"], 0.195)

    def test_shape_straddle_daily_close_history_limits_recent_sessions(self):
        rows = [
            {
                "session_date": "2026-03-21",
                "captured_at": "2026-03-21T20:01:00+00:00",
                "expiration": "2026-03-21",
                "days_to_expiry": 0,
                "strike": 6120.0,
                "spot": 6118.0,
                "straddle_mid": 32.0,
            },
            {
                "session_date": "2026-03-21",
                "captured_at": "2026-03-21T20:01:00+00:00",
                "expiration": "2026-03-24",
                "days_to_expiry": 3,
                "strike": 6120.0,
                "spot": 6118.0,
                "straddle_mid": 55.0,
            },
            {
                "session_date": "2026-03-20",
                "captured_at": "2026-03-20T20:01:00+00:00",
                "expiration": "2026-03-20",
                "days_to_expiry": 0,
                "strike": 6100.0,
                "spot": 6104.0,
                "straddle_mid": 41.0,
            },
            {
                "session_date": "2026-03-19",
                "captured_at": "2026-03-19T20:01:00+00:00",
                "expiration": "2026-03-19",
                "days_to_expiry": 0,
                "strike": 6080.0,
                "spot": 6077.0,
                "straddle_mid": 39.0,
            },
        ]

        shaped = server_main._shape_straddle_daily_close_history(rows, session_limit=2)
        self.assertEqual(len(shaped), 3)
        self.assertEqual(shaped[0]["session_date"], "2026-03-21")
        self.assertEqual(shaped[-1]["session_date"], "2026-03-20")
        self.assertNotIn("2026-03-19", [row["session_date"] for row in shaped])

    def test_straddle_monitor_cache_returns_deep_copy_within_ttl(self):
        now_utc = datetime(2026, 3, 23, 14, 0, tzinfo=timezone.utc)
        payload = {"rows": [{"expiration": "2026-03-23"}], "daily_closes": []}

        server_main._straddle_monitor_cache_set(row_limit=8, now_utc=now_utc, payload=payload)
        cached = server_main._straddle_monitor_cache_get(row_limit=8, now_utc=now_utc)

        self.assertEqual(cached, payload)
        self.assertIsNot(cached, payload)
        cached["rows"][0]["expiration"] = "mutated"
        fresh = server_main._straddle_monitor_cache_get(row_limit=8, now_utc=now_utc)
        self.assertEqual(fresh["rows"][0]["expiration"], "2026-03-23")

    def test_straddle_close_capture_window_accepts_close_minute_only(self):
        friday_close = datetime(2026, 3, 20, 20, 0, tzinfo=timezone.utc)
        friday_late = datetime(2026, 3, 20, 20, 16, tzinfo=timezone.utc)
        saturday_close = datetime(2026, 3, 21, 20, 0, tzinfo=timezone.utc)

        self.assertTrue(server_main._is_straddle_close_capture_window(friday_close))
        self.assertFalse(server_main._is_straddle_close_capture_window(friday_late))
        self.assertFalse(server_main._is_straddle_close_capture_window(saturday_close))

    def test_straddle_monitor_cache_expires_after_ttl(self):
        now_utc = datetime(2026, 3, 23, 14, 0, tzinfo=timezone.utc)
        payload = {"rows": [{"expiration": "2026-03-23"}], "daily_closes": []}

        server_main._straddle_monitor_cache_set(row_limit=8, now_utc=now_utc, payload=payload)
        later = now_utc.replace(second=server_main.STRADDLE_MONITOR_RESPONSE_CACHE_SECONDS + 1)
        cached = server_main._straddle_monitor_cache_get(row_limit=8, now_utc=later)

        self.assertIsNone(cached)


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
