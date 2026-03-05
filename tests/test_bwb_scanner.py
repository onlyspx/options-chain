import unittest

from web.server.main import _compute_bwb_scanner


def _row(
    strike,
    *,
    call_bid=None,
    call_ask=None,
    put_bid=None,
    put_ask=None,
    call_osi=None,
    put_osi=None,
    call_vol=None,
    put_vol=None,
    call_oi=None,
    put_oi=None,
):
    return {
        "strike": strike,
        "call_bid": call_bid,
        "call_ask": call_ask,
        "put_bid": put_bid,
        "put_ask": put_ask,
        "call_osi": call_osi,
        "put_osi": put_osi,
        "call_vol": call_vol,
        "put_vol": put_vol,
        "call_oi": call_oi,
        "put_oi": put_oi,
    }


class BwbScannerTests(unittest.TestCase):
    def _base_chain(self):
        # Spot used by tests is 450.0.
        return {
            425.0: _row(425.0, put_bid=0.50, put_ask=0.70, put_osi="P425"),
            430.0: _row(430.0, put_bid=0.90, put_ask=1.10, put_osi="P430"),
            435.0: _row(435.0, put_bid=2.00, put_ask=2.20, put_osi="P435", put_vol=1200, put_oi=5500),
            440.0: _row(440.0, put_bid=3.00, put_ask=3.20, put_osi="P440"),
            455.0: _row(455.0, call_bid=2.80, call_ask=3.00, call_osi="C455"),
            460.0: _row(460.0, call_bid=2.00, call_ask=2.20, call_osi="C460", call_vol=1300, call_oi=6200),
            465.0: _row(465.0, call_bid=1.60, call_ask=1.80, call_osi="C465"),
            475.0: _row(475.0, call_bid=0.50, call_ask=0.70, call_osi="C475"),
        }

    def _greeks(self):
        return {
            "P435": {"delta": -0.20},
            "C460": {"delta": 0.23},
            "C465": {"delta": 0.18},
        }

    def test_returns_live_put_and_call_candidates_sorted_by_rom(self):
        result = _compute_bwb_scanner(self._base_chain(), 450.0, self._greeks())
        puts = result["put_bwb_credit_spreads"]
        calls = result["call_bwb_credit_spreads"]

        self.assertGreaterEqual(len(puts), 1)
        self.assertGreaterEqual(len(calls), 1)

        for side_rows in (puts, calls):
            for i in range(len(side_rows) - 1):
                self.assertGreaterEqual(side_rows[i]["rom_pct"], side_rows[i + 1]["rom_pct"])

    def test_put_formula_fields_for_known_candidate(self):
        result = _compute_bwb_scanner(self._base_chain(), 450.0, self._greeks())
        puts = result["put_bwb_credit_spreads"]
        target = next(
            (s for s in puts if s["low_strike"] == 425.0 and s["mid_strike"] == 435.0 and s["high_strike"] == 440.0),
            None,
        )
        self.assertIsNotNone(target)
        self.assertEqual(target["mark_credit"], 0.5)
        self.assertEqual(target["narrow_wing_width"], 5.0)
        self.assertEqual(target["broken_wing_width"], 10.0)
        self.assertEqual(target["max_loss"], 4.5)
        self.assertEqual(target["max_profit"], 5.5)
        self.assertEqual(target["rom_pct"], 11.1)
        self.assertEqual(target["breakeven"], 429.5)
        self.assertEqual(target["pop_delta_pct"], 80.0)

    def test_far_otm_filter_excludes_puts_when_any_leg_crosses_spot(self):
        result = _compute_bwb_scanner(self._base_chain(), 438.0, self._greeks())
        self.assertEqual(result["put_bwb_credit_spreads"], [])

    def test_missing_quotes_excludes_candidate(self):
        by_strike = self._base_chain()
        by_strike[475.0]["call_ask"] = None
        result = _compute_bwb_scanner(by_strike, 450.0, self._greeks())
        # Every call candidate requires a valid high wing ask.
        self.assertEqual(result["call_bwb_credit_spreads"], [])


if __name__ == "__main__":
    unittest.main()
