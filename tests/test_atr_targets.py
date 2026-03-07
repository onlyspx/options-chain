import unittest
from datetime import date, datetime, timedelta

import web.server.main as server_main


class AtrTargetsTests(unittest.TestCase):
    def setUp(self):
        self.orig_fetch_daily_history_rows = server_main._fetch_daily_history_rows
        self.orig_supabase_get_recent_atr_rows = server_main._supabase_get_recent_atr_rows
        self.orig_supabase_get_cached_atr_row = server_main._supabase_get_cached_atr_row
        self.orig_supabase_upsert_atr_row = server_main._supabase_upsert_atr_row
        self.orig_atr_cache = dict(server_main._atr_cache_by_symbol)
        server_main._atr_cache_by_symbol.clear()

    def tearDown(self):
        server_main._fetch_daily_history_rows = self.orig_fetch_daily_history_rows
        server_main._supabase_get_recent_atr_rows = self.orig_supabase_get_recent_atr_rows
        server_main._supabase_get_cached_atr_row = self.orig_supabase_get_cached_atr_row
        server_main._supabase_upsert_atr_row = self.orig_supabase_upsert_atr_row
        server_main._atr_cache_by_symbol.clear()
        server_main._atr_cache_by_symbol.update(self.orig_atr_cache)

    def test_wilder_atr14_math_on_synthetic_rows(self):
        rows = []
        base = date(2026, 1, 1)
        close = 100.0
        for i in range(20):
            rows.append(
                {
                    "date": base + timedelta(days=i),
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                }
            )
            close += 1.0

        atr14, asof = server_main._compute_wilder_atr_from_rows(rows, period=14)
        self.assertEqual(round(atr14, 4), 2.0)
        self.assertEqual(asof, "2026-01-20")

    def test_compute_atr_analysis_levels_from_previous_close(self):
        now = datetime(2026, 3, 6, 12, 0, 0)

        def fake_history(*_args, **_kwargs):
            rows = []
            base = date(2026, 1, 1)
            close = 100.0
            for i in range(20):
                rows.append(
                    {
                        "date": base + timedelta(days=i),
                        "high": close + 1.0,
                        "low": close - 1.0,
                        "close": close,
                    }
                )
                close += 1.0
            return rows

        server_main._fetch_daily_history_rows = fake_history
        server_main._supabase_get_recent_atr_rows = lambda **_kwargs: []
        server_main._supabase_get_cached_atr_row = lambda **_kwargs: None
        server_main._supabase_upsert_atr_row = lambda *_args, **_kwargs: True

        analysis = server_main._compute_atr_analysis("SPY", {"close": 100.0}, now)
        self.assertEqual(analysis["status"], "ok")
        self.assertEqual(analysis["atr14"], 2.0)
        self.assertEqual(analysis["plus_1atr_level"], 102.0)
        self.assertEqual(analysis["minus_1atr_level"], 98.0)
        self.assertEqual(analysis["plus_2atr_level"], 104.0)
        self.assertEqual(analysis["minus_2atr_level"], 96.0)

    def test_pick_atr_target_spread_prefers_nearest_short_strike(self):
        spreads = [
            {"short_strike": 101.0, "long_strike": 106.0, "mark_credit": 0.4, "distance_from_spx": 10.0},
            {"short_strike": 99.0, "long_strike": 94.0, "mark_credit": 0.5, "distance_from_spx": 20.0},
            {"short_strike": 102.0, "long_strike": 107.0, "mark_credit": 0.3, "distance_from_spx": 12.0},
        ]
        selected = server_main._pick_atr_target_spread(spreads, target_level=100.4)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["short_strike"], 101.0)
        self.assertEqual(selected["atr_target_level"], 100.4)
        self.assertEqual(selected["atr_gap"], 0.6)

    def test_compute_atr_analysis_uses_supabase_cache_before_history(self):
        now = datetime(2026, 3, 6, 12, 0, 0)
        history_calls = {"count": 0}

        def fake_history(*_args, **_kwargs):
            history_calls["count"] += 1
            return []

        server_main._fetch_daily_history_rows = fake_history
        server_main._supabase_get_recent_atr_rows = lambda **_kwargs: [
            {
                "symbol": "SPY",
                "session_date": "2026-03-05",
                "source_symbol": "SPY",
                "previous_close": 100.0,
                "atr14": 2.25,
                "plus_1atr_level": 102.25,
                "minus_1atr_level": 97.75,
            }
        ]
        server_main._supabase_get_cached_atr_row = lambda **_kwargs: None
        server_main._supabase_upsert_atr_row = lambda *_args, **_kwargs: True

        analysis = server_main._compute_atr_analysis("SPY", {"close": 100.0}, now)
        self.assertEqual(analysis["status"], "ok")
        self.assertEqual(analysis["atr14"], 2.25)
        self.assertEqual(analysis["plus_2atr_level"], 104.5)
        self.assertEqual(analysis["minus_2atr_level"], 95.5)
        self.assertEqual(history_calls["count"], 0)

    def test_compute_atr_analysis_unavailable_when_history_missing(self):
        now = datetime(2026, 3, 6, 12, 0, 0)
        server_main._fetch_daily_history_rows = lambda *_args, **_kwargs: []
        server_main._supabase_get_recent_atr_rows = lambda **_kwargs: []
        server_main._supabase_get_cached_atr_row = lambda **_kwargs: None
        server_main._supabase_upsert_atr_row = lambda *_args, **_kwargs: True

        analysis = server_main._compute_atr_analysis("SPY", {"close": 100.0}, now)
        self.assertEqual(analysis["status"], "unavailable")
        self.assertIsNone(analysis["atr14"])
        self.assertIsNone(analysis["plus_1atr_level"])
        self.assertIsNone(analysis["minus_1atr_level"])
        self.assertIsNone(analysis["plus_2atr_level"])
        self.assertIsNone(analysis["minus_2atr_level"])


if __name__ == "__main__":
    unittest.main()
