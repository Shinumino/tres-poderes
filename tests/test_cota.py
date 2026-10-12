import hashlib
import pathlib
import tempfile
import unittest
import zipfile

from tests.samples import HEADER, receipt, year_file
from tres_poderes import cota


class CotaTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = pathlib.Path(folder.name)
        self.db = cota.connect(":memory:")
        self.addCleanup(self.db.close)


class LoadingAYear(CotaTestCase):
    def test_a_loaded_file_gives_the_exact_total_paid(self):
        path = year_file(self.folder, 2099, [
            receipt(1001, "1467"),
            receipt(1001, "3668.93"),
            receipt(1001, "-298.07"),  # a refund
            receipt(2002, "0.10"),
        ])

        cota.load_cota(self.db, path)

        # Worked by hand: 1467.00 + 3668.93 - 298.07 = 4837.86, and 0.10 more for the second deputy.
        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=1001), 483786)
        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=2002), 10)
        self.assertEqual(cota.total_liquido(self.db, 2099), 483796)

    def test_many_small_receipts_do_not_drift(self):
        # Ten times R$ 0,10 is R$ 1,00. In floating point it is 0.9999999999999999.
        path = year_file(self.folder, 2099, [receipt(1001, "0.10") for _ in range(10)])

        cota.load_cota(self.db, path)

        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=1001), 100)

    def test_party_leadership_spending_is_kept_but_belongs_to_no_deputy(self):
        # The Camara lists leaderships (LID.GOV-CD and the like) in the same file with no deputy id.
        path = year_file(self.folder, 2099, [
            receipt(1001, "100"),
            receipt(None, "250.50"),
        ])

        cota.load_cota(self.db, path)

        self.assertEqual(cota.total_liquido(self.db, 2099), 35050)
        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=1001), 10000)
        self.assertEqual([r.liquido_centavos for r in cota.receipts(self.db, 2099, deputy=1001)], [10000])
        [leadership] = cota.receipts_without_deputy(self.db, 2099)
        self.assertEqual(leadership.liquido_centavos, 25050)
        self.assertEqual(leadership.campos["txNomeParlamentar"], "LID.GOV-CD")
        self.assertIsNone(leadership.deputado)


class LoadingAgain(CotaTestCase):
    def test_the_same_file_twice_changes_nothing(self):
        rows = [receipt(1001, "100"), receipt(1001, "50")]
        path = year_file(self.folder, 2099, rows)

        cota.load_cota(self.db, path)
        cota.load_cota(self.db, path)
        # The Camara rebuilds every zip each night: the same receipts arrive in a file whose bytes differ.
        cota.load_cota(self.db, year_file(self.folder, 2099, rows, name="tomorrow.zip"))

        self.assertEqual(cota.total_liquido(self.db, 2099), 15000)
        self.assertEqual(len(cota.receipts(self.db, 2099, deputy=1001)), 2)

    def test_a_newer_file_replaces_its_year_and_only_its_year(self):
        cota.load_cota(self.db, year_file(self.folder, 2098, [receipt(1001, "999")]))
        cota.load_cota(self.db, year_file(self.folder, 2099, [
            receipt(1001, "100", ideDocumento="1"),
            receipt(1001, "50", ideDocumento="2"),
            receipt(2002, "70", ideDocumento="3"),
        ], name="monday.zip"))

        # Tuesday: receipt 1 was corrected down, receipt 2 was withdrawn, receipt 4 is new.
        cota.load_cota(self.db, year_file(self.folder, 2099, [
            receipt(1001, "80", ideDocumento="1"),
            receipt(2002, "70", ideDocumento="3"),
            receipt(2002, "5.25", ideDocumento="4"),
        ], name="tuesday.zip"))

        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=1001), 8000)
        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=2002), 7525)
        self.assertEqual(cota.total_liquido(self.db, 2099), 15525)
        self.assertEqual(cota.total_liquido(self.db, 2098), 99900)


class RefusingABadFile(CotaTestCase):
    """A file we cannot read exactly is refused whole: the year keeps what it had."""

    def setUp(self):
        super().setUp()
        cota.load_cota(self.db, year_file(self.folder, 2099, [receipt(1001, "100")], name="good.zip"))

    def refused(self, rows, **file_options):
        with self.assertRaises(cota.BadFile) as caught:
            cota.load_cota(self.db, year_file(self.folder, 2099, rows, name="bad.zip", **file_options))
        self.assertEqual(cota.total_liquido(self.db, 2099), 10000)
        self.assertEqual(len(cota.receipts(self.db, 2099, deputy=1001)), 1)
        return str(caught.exception)

    def test_an_amount_paid_that_is_not_exact_centavos(self):
        message = self.refused([receipt(1001, "50"), receipt(1001, "18.235")])
        # The header is line 1, so the second receipt is line 3: whoever investigates needs the line.
        self.assertIn("line 3", message)
        self.assertIn("18.235", message)

    def test_a_receipt_with_no_amount_paid(self):
        self.assertIn("line 2", self.refused([receipt(1001, "")]))

    def test_a_receipt_of_another_year_mixed_in(self):
        self.assertIn("line 3", self.refused([receipt(1001, "50"), receipt(1001, "60", numAno="2098")]))

    def test_a_file_whose_columns_changed(self):
        renamed = ["valorLiquido" if name == "vlrLiquido" else name for name in HEADER]
        self.assertIn("vlrLiquido", self.refused([receipt(1001, "50")], header=renamed))

    def test_a_file_with_no_receipts(self):
        self.refused([])


class WhereAReceiptCameFrom(CotaTestCase):
    def test_every_receipt_names_its_file_and_line(self):
        path = year_file(self.folder, 2099, [
            receipt(1001, "100"),
            receipt(2002, "70"),
            receipt(1001, "50"),
        ])
        with zipfile.ZipFile(path) as archive:
            csv_sha256 = hashlib.sha256(archive.read("Ano-2099.csv")).hexdigest()

        cota.load_cota(self.db, path)

        first, second = cota.receipts(self.db, 2099, deputy=1001)
        # Line 1 is the header; the other deputy's receipt sits on line 3.
        self.assertEqual((first.linha, second.linha), (2, 4))
        self.assertEqual((first.arquivo, first.sha256), ("Ano-2099.csv", csv_sha256))

    def test_the_invoice_value_is_shown_as_written_even_when_it_is_not_exact_centavos(self):
        path = year_file(self.folder, 2099, [receipt(1001, "18.23", vlrDocumento="18.235")])

        cota.load_cota(self.db, path)

        [only] = cota.receipts(self.db, 2099, deputy=1001)
        self.assertEqual(only.campos["vlrDocumento"], "18.235")
        self.assertEqual(only.liquido_centavos, 1823)


if __name__ == "__main__":
    unittest.main()
