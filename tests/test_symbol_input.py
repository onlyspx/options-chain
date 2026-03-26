import unittest

from fastapi import HTTPException

import web.server.main as server_main


class SymbolInputTests(unittest.TestCase):
    def test_normalize_symbol_uppercases_and_trims(self):
        self.assertEqual(server_main._normalize_symbol("  tlt "), "TLT")
        self.assertEqual(server_main._normalize_symbol("brk.b"), "BRK.B")

    def test_normalize_symbol_rejects_invalid_characters(self):
        with self.assertRaises(HTTPException) as ctx:
            server_main._normalize_symbol("spy$")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid symbol=", str(ctx.exception.detail))

    def test_instrument_type_for_symbol_keeps_known_indexes(self):
        self.assertEqual(server_main._instrument_type_for_symbol("SPX"), server_main.InstrumentType.INDEX)
        self.assertEqual(server_main._instrument_type_for_symbol("TLT"), server_main.InstrumentType.EQUITY)


if __name__ == "__main__":
    unittest.main()
