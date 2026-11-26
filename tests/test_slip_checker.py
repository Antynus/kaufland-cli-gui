from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from slip_checker import cli, fiscal, ledger
from slip_checker.config import SlipError
from slip_checker.providers import lidl
from slip_checker.providers.kaufland import constants as kaufland_constants
from slip_checker.providers.kaufland import receipts as kaufland_receipts

JIR = "12345678-1234-1234-1234-123456789ABC"
ZKI = "0123456789abcdef0123456789ABCDEF"


class IsolatedDataDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        env = {"SLIP_DATA_DIR": str(self.tmp / "data"), "XDG_CONFIG_HOME": str(self.tmp / "xdg")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        for key in ("KAUFLAND_DATA_DIR", "LIDL_COUNTRY", "LIDL_LANGUAGE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()


class ParseQrTest(unittest.TestCase):
    def test_decimal_comma_amount(self) -> None:
        slip = fiscal.parse_qr(f"https://porezna.gov.hr/rn?jir={JIR}&datv=20250905_1230&izn=12,34")
        self.assertEqual(slip.jir, JIR.lower())
        self.assertEqual(slip.issued_at, datetime(2025, 9, 5, 12, 30))
        self.assertEqual(slip.amount, Decimal("12.34"))
        self.assertEqual(slip.currency, "EUR")

    def test_whole_cents_amount_and_zki(self) -> None:
        slip = fiscal.parse_qr(f"https://porezna.gov.hr/rn?zki={ZKI}&datv=20220105_0901&izn=001234")
        self.assertEqual(slip.amount, Decimal("12.34"))
        self.assertEqual(slip.zki, ZKI.lower())
        self.assertEqual(slip.currency, "HRK")

    def test_dot_and_encoded_comma(self) -> None:
        self.assertEqual(fiscal.parse_amount("7.5"), Decimal("7.50"))
        self.assertEqual(fiscal.parse_amount("1000000%2C00"), Decimal("1000000.00"))
        self.assertEqual(fiscal.parse_amount("-3,10"), Decimal("-3.10"))

    def test_bare_query_is_accepted(self) -> None:
        slip = fiscal.parse_qr(f"?jir={JIR}&datv=20250101_0000&izn=100")
        self.assertEqual(slip.amount, Decimal("1.00"))

    def test_rejects_bad_input(self) -> None:
        for text in (
            "https://porezna.gov.hr/rn?jir=nope&datv=20250905_1230&izn=1,00",
            f"https://porezna.gov.hr/rn?jir={JIR}&datv=2025-09-05&izn=1,00",
            f"https://porezna.gov.hr/rn?jir={JIR}&datv=20250905_1230&izn=1,234",
            f"https://porezna.gov.hr/rn?jir={JIR}&izn=1,00",
            "https://porezna.gov.hr/rn?datv=20250905_1230&izn=1,00",
        ):
            with self.subTest(text=text), self.assertRaises(SlipError):
                fiscal.parse_qr(text)

    def test_verify_url_round_trips(self) -> None:
        slip = fiscal.parse_qr(f"https://porezna.gov.hr/rn?jir={JIR}&datv=20250905_1230&izn=1234")
        self.assertEqual(
            slip.verify_url,
            f"https://porezna.gov.hr/rn?jir={JIR.lower()}&datv=20250905_1230&izn=12,34",
        )
        self.assertEqual(fiscal.parse_qr(slip.verify_url), slip)

    def test_typed_fields_are_euros(self) -> None:
        slip = fiscal.from_fields(jir=JIR, zki=None, date="2025-09-05", time="12:30", amount="12")
        self.assertEqual(slip.amount, Decimal("12.00"))


class LedgerTest(IsolatedDataDir):
    def test_add_dedupe_find_remove(self) -> None:
        slip = fiscal.parse_qr(f"jir={JIR}&datv=20250905_1230&izn=12,34")
        entry, new = ledger.add("spar", slip, "groceries")
        self.assertTrue(new)
        self.assertEqual(entry["id"], "12345678")
        _, new_again = ledger.add("plodine", slip)
        self.assertFalse(new_again)
        self.assertEqual(len(ledger.load()), 1)
        self.assertEqual(ledger.find("12345678")["store"], "spar")
        self.assertEqual(ledger.rows("plodine"), [])
        self.assertEqual(oct(ledger.path().stat().st_mode & 0o777), "0o600")
        ledger.remove("12345678")
        self.assertEqual(ledger.load(), [])
        with self.assertRaises(SlipError):
            ledger.find("12345678")

    def test_cli_add_and_list(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(
                ["add", "studenac", f"https://porezna.gov.hr/rn?jir={JIR}&datv=20250905_1230&izn=5,00"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["store"], "studenac")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["slips", "--output", "csv"])
        self.assertIn("studenac", out.getvalue())

    def test_cli_add_needs_input(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["add", "spar"]), 1)
        self.assertIn("QR code", err.getvalue())


class KauflandTest(IsolatedDataDir):
    def test_rows(self) -> None:
        receipt = {
            "id": "r1",
            "timestamp": "2025-01-01T10:00:00",
            "store": {"name": "Kaufland Zagreb", "city": "Zagreb"},
            "sum": 1250,
            "saving": 100,
            "currency": "EUR",
            "positions": [{"name": "Milk", "quantity": 2, "unitPrice": 125, "total": 250, "gtin": "1"}],
            "refundPositions": [{"name": "Bottle", "unitPrice": 10, "total": 10}],
        }
        summary = kaufland_receipts.summaries([receipt])[0]
        self.assertEqual((summary["chain"], summary["total"], summary["items"]), ("kaufland", 12.5, 1))
        items = kaufland_receipts.line_items([receipt])
        self.assertEqual([i["total"] for i in items], [2.5, -0.1])
        self.assertEqual(list(items[0]), list(lidl.LidlProvider().line_items([_lidl_ticket()])[0]))

    def test_legacy_token_moves(self) -> None:
        legacy = self.tmp / "xdg" / "kaufland-cli" / "tokens.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("{}")
        path = kaufland_constants.token_path()
        self.assertEqual(path, self.tmp / "data" / "kaufland" / "tokens.json")
        self.assertTrue(path.exists())
        self.assertFalse(legacy.exists())


def _lidl_ticket() -> dict:
    return {
        "id": "t1",
        "date": "2025-02-01T09:00:00",
        "totalAmount": 4.38,
        "currency": {"code": "EUR"},
        "store": {"name": "Lidl Split", "locality": "Split"},
        "itemsLine": [
            {
                "name": "Kruh",
                "currentUnitPrice": "2,19",
                "quantity": "2",
                "isWeight": False,
                "originalAmount": "4,38",
                "codeInput": "4023",
                "discounts": [{"description": "Kupon", "amount": "0,21"}],
            }
        ],
    }


class LidlTest(IsolatedDataDir):
    def test_rows(self) -> None:
        provider = lidl.LidlProvider()
        summary = provider.summaries([_lidl_ticket()])[0]
        self.assertEqual(
            (summary["chain"], summary["total"], summary["saving"], summary["city"]),
            ("lidl", 4.38, 0.21, "Split"),
        )
        item = provider.line_items([_lidl_ticket()])[0]
        self.assertEqual((item["quantity"], item["unit_price"], item["discount"]), (2.0, 2.19, 0.21))

    def test_fetch_pages_and_details(self) -> None:
        pages = {
            1: {"tickets": [{"id": "a"}, {"id": "b"}], "size": 2, "totalCount": 3},
            2: {"tickets": [{"id": "c"}], "size": 2, "totalCount": 3},
        }
        calls = []

        def fake_get(path, params=None):
            calls.append(path)
            if path == "tickets":
                return pages[params["pageNumber"]]
            return {"itemsLine": [], "detail": path}

        with mock.patch.object(lidl, "_get", fake_get):
            all_tickets = lidl.LidlProvider().fetch(20, fetch_all=True, details=False)
            calls.clear()
            first = lidl.LidlProvider().fetch(1)
        self.assertEqual([t["id"] for t in all_tickets], ["a", "b", "c"])
        self.assertEqual(first, [{"id": "a", "itemsLine": [], "detail": "tickets/a"}])
        self.assertEqual(calls, ["tickets", "tickets/a"])

    def test_login_stores_refresh_token(self) -> None:
        payload = {"access_token": "acc", "refresh_token": "new", "expires_in": 600}
        with mock.patch.object(lidl, "_token_request", return_value=payload) as req:
            message = lidl.LidlProvider().login(mock.Mock(refresh_token="old"))
        req.assert_called_once_with({"grant_type": "refresh_token", "refresh_token": "old"})
        self.assertIn("HR", message)
        saved = json.loads(lidl.token_path().read_text())
        self.assertEqual((saved["refresh_token"], saved["country"]), ("new", "HR"))
        self.assertEqual(lidl.access_token(), "acc")


if __name__ == "__main__":
    unittest.main()
