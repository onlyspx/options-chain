import unittest
from datetime import date

from web.server.main import _build_expiry_slots, _resolve_expiration_for_slot


class ExpirySlotResolverTests(unittest.TestCase):
    def test_non_spx_monday_same_day_has_0dte(self):
        today = date(2026, 3, 2)  # Monday
        expiries = [today, date(2026, 3, 4), date(2026, 3, 6)]
        slots = _build_expiry_slots(expiries, today=today, symbol="AAPL")
        self.assertEqual(slots["slot_0dte"], "2026-03-02")
        self.assertEqual(slots["slot_next1"], "2026-03-04")
        self.assertEqual(slots["slot_next2"], "2026-03-06")

    def test_non_spx_tuesday_same_day_does_not_have_0dte(self):
        today = date(2026, 3, 3)  # Tuesday
        expiries = [today, date(2026, 3, 4), date(2026, 3, 6)]
        slots = _build_expiry_slots(expiries, today=today, symbol="AAPL")
        self.assertIsNone(slots["slot_0dte"])
        self.assertEqual(slots["slot_next1"], "2026-03-04")
        self.assertEqual(slots["slot_next2"], "2026-03-06")

    def test_spx_tuesday_same_day_keeps_0dte(self):
        today = date(2026, 3, 3)  # Tuesday
        expiries = [today, date(2026, 3, 4), date(2026, 3, 6)]
        slots = _build_expiry_slots(expiries, today=today, symbol="SPX")
        self.assertEqual(slots["slot_0dte"], "2026-03-03")

    def test_0dte_request_falls_back_to_next1_when_unavailable(self):
        today = date(2026, 3, 3)  # Tuesday
        expiries = [today, date(2026, 3, 4), date(2026, 3, 6)]
        slots = _build_expiry_slots(expiries, today=today, symbol="AAPL")
        resolved_slot, expiration = _resolve_expiration_for_slot(slots, requested_slot="0dte")
        self.assertEqual(resolved_slot, "next1")
        self.assertEqual(expiration, "2026-03-04")

    def test_only_one_future_expiration_yields_null_next2(self):
        today = date(2026, 3, 2)  # Monday
        expiries = [today, date(2026, 3, 6)]
        slots = _build_expiry_slots(expiries, today=today, symbol="AAPL")
        self.assertEqual(slots["slot_next1"], "2026-03-06")
        self.assertIsNone(slots["slot_next2"])


if __name__ == "__main__":
    unittest.main()
