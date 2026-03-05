import unittest
from datetime import datetime, timedelta

import web.server.main as server_main


class _MockGreeks:
    def __init__(self, delta=None, implied_volatility=None):
        self.delta = delta
        self.implied_volatility = implied_volatility


class _MockGreekData:
    def __init__(self, osi_symbol, delta=None, implied_volatility=None):
        self.osi_symbol = osi_symbol
        self.greeks = _MockGreeks(delta=delta, implied_volatility=implied_volatility)


class _MockGreeksResponse:
    def __init__(self, greeks):
        self.greeks = greeks


class _ChunkedGreeksClient:
    def __init__(self):
        self.calls = []

    def get_option_greeks(self, osi_symbols):
        symbols = list(osi_symbols)
        self.calls.append(symbols)
        if symbols == ["A", "B"]:
            return _MockGreeksResponse(
                [
                    _MockGreekData("A", delta=0.10, implied_volatility=0.20),
                    _MockGreekData("B", delta=0.20, implied_volatility=0.21),
                ]
            )
        if symbols == ["C", "D"]:
            raise RuntimeError("Chunk failed")
        return _MockGreeksResponse([])


class PopDeltaReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.orig_chunk_size = server_main.GREEKS_FETCH_CHUNK_SIZE
        self.orig_cache = dict(server_main._greeks_cache_by_symbol_exp)
        server_main._greeks_cache_by_symbol_exp.clear()

    def tearDown(self):
        server_main.GREEKS_FETCH_CHUNK_SIZE = self.orig_chunk_size
        server_main._greeks_cache_by_symbol_exp.clear()
        server_main._greeks_cache_by_symbol_exp.update(self.orig_cache)

    def test_chunked_greeks_fetch_uses_cache_on_failed_chunk(self):
        now = datetime(2026, 3, 5, 12, 0, 0)
        server_main.GREEKS_FETCH_CHUNK_SIZE = 2
        cache_key = ("SPY", "2026-03-05")
        server_main._greeks_cache_by_symbol_exp[cache_key] = {
            "fetched_at": now - timedelta(minutes=2),
            "by_osi": {
                "C": {"delta": 0.33, "implied_volatility": 0.19},
            },
            "timestamp": "2026-03-05T11:58:00Z",
        }
        client = _ChunkedGreeksClient()
        result = server_main._get_option_greeks_map(
            client=client,
            now_utc=now,
            symbol="SPY",
            expiration="2026-03-05",
            osi_symbols=["A", "B", "C", "D"],
        )

        self.assertEqual(client.calls, [["A", "B"], ["C", "D"]])
        self.assertEqual(result["A"]["delta"], 0.10)
        self.assertEqual(result["B"]["delta"], 0.20)
        self.assertEqual(result["C"]["delta"], 0.33)
        self.assertEqual(result["D"], {})

    def test_collect_spread_osi_symbols_deduplicates_vertical_and_bwb(self):
        by_strike = {
            100.0: {"call_osi": "C100", "put_osi": "P100"},
            105.0: {"call_osi": "C105", "put_osi": "P105"},
            110.0: {"call_osi": "C110", "put_osi": "P110"},
            115.0: {"call_osi": "C115", "put_osi": "P115"},
        }
        spread_scanner = {
            "call_credit_spreads": [{"short_strike": 110.0}],
            "put_credit_spreads": [{"short_strike": 105.0}],
            "call_bwb_credit_spreads": [{"mid_strike": 110.0}],
            "put_bwb_credit_spreads": [{"mid_strike": 105.0}, {"mid_strike": 105.0}],
        }

        symbols = server_main._collect_spread_osi_symbols(spread_scanner, by_strike)
        self.assertEqual(symbols, ["C110", "P105"])

    def test_attach_pop_to_bwbs_sets_delta_fields_when_available(self):
        by_strike = {
            105.0: {"call_osi": "C105", "put_osi": "P105"},
            110.0: {"call_osi": "C110", "put_osi": "P110"},
        }
        greeks_by_osi = {
            "C105": {"delta": 0.25},
            "P110": {"delta": -0.15},
        }

        call_spreads = [{"mid_strike": 105.0}, {"mid_strike": 110.0}]
        put_spreads = [{"mid_strike": 110.0}]
        server_main._attach_pop_to_bwbs(call_spreads, "call", by_strike, greeks_by_osi)
        server_main._attach_pop_to_bwbs(put_spreads, "put", by_strike, greeks_by_osi)

        self.assertEqual(call_spreads[0]["body_delta"], 0.25)
        self.assertEqual(call_spreads[0]["pop_delta_pct"], 75.0)
        self.assertEqual(call_spreads[0]["pop_delta_method"], "delta_direct")
        self.assertIsNone(call_spreads[1]["body_delta"])
        self.assertIsNone(call_spreads[1]["pop_delta_pct"])
        self.assertEqual(call_spreads[1]["pop_delta_method"], "unavailable")
        self.assertEqual(put_spreads[0]["body_delta"], -0.15)
        self.assertEqual(put_spreads[0]["pop_delta_pct"], 85.0)


if __name__ == "__main__":
    unittest.main()
