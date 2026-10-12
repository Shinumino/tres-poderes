import hashlib
import pathlib
import tempfile
import unittest
import zipfile

from tests.samples import HEADER, header_line, raw_year_file, receipt, row_line, year_file
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
        self.assertEqual(cota.total_camara(self.db, 2099), 483796)

    def test_many_small_receipts_do_not_drift(self):
        # Ten times R$ 0,10 is R$ 1,00. In floating point it is 0.9999999999999999.
        path = year_file(self.folder, 2099, [receipt(1001, "0.10", ideDocumento=str(n)) for n in range(10)])

        cota.load_cota(self.db, path)

        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=1001), 100)

    def test_party_leadership_spending_is_kept_but_belongs_to_no_deputy(self):
        # The Camara lists leaderships (LID.GOV-CD and the like) in the same file with no deputy id.
        path = year_file(self.folder, 2099, [
            receipt(1001, "100"),
            receipt(None, "250.50"),
        ])

        cota.load_cota(self.db, path)

        self.assertEqual(cota.total_camara(self.db, 2099), 35050)
        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=1001), 10000)
        self.assertEqual([r.liquido_centavos for r in cota.receipts(self.db, 2099, deputy=1001)], [10000])
        [leadership] = cota.receipts_without_deputy(self.db, 2099)
        self.assertEqual(leadership.liquido_centavos, 25050)
        self.assertEqual(leadership.campos["txNomeParlamentar"], "LID.GOV-CD")
        self.assertIsNone(leadership.deputado)

    def test_the_camaras_total_and_one_deputys_total_cannot_be_taken_for_each_other(self):
        cota.load_cota(self.db, year_file(self.folder, 2099, [receipt(1001, "100"), receipt(None, "250.50")]))
        [leadership] = cota.receipts_without_deputy(self.db, 2099)

        # A leadership receipt has no deputy. Asking for "its deputy's total" used to answer with
        # the whole Camara's, which a page would have printed beside the leadership's name.
        with self.assertRaises(ValueError):
            cota.total_liquido(self.db, 2099, leadership.deputado)
        with self.assertRaises(ValueError):
            cota.receipts(self.db, 2099, leadership.deputado)
        self.assertEqual(cota.total_camara(self.db, 2099), 35050)

    def test_receipts_that_are_alike_except_for_where_a_separator_falls_are_two_receipts(self):
        # Not copies of each other: the same characters, split differently between two fields.
        path = year_file(self.folder, 2099, [
            receipt(1001, "10", txtFornecedor="A\x1fB", txtCNPJCPF="C"),
            receipt(1001, "10", txtFornecedor="A", txtCNPJCPF="B\x1fC"),
        ])

        cota.load_cota(self.db, path)

        self.assertEqual(cota.total_camara(self.db, 2099), 2000)

    def test_values_the_real_files_contain_come_back_exactly(self):
        # Both occur in the real files: a quote inside a supplier's name, a line break inside a field.
        path = year_file(self.folder, 2099, [
            receipt(1001, "10", txtFornecedor='POSTO "ESTRELA" LTDA'),
            receipt(1001, "20", txtTrecho="BSB/GRU\nGRU/BSB"),
        ])

        cota.load_cota(self.db, path)

        first, second = cota.receipts(self.db, 2099, deputy=1001)
        self.assertEqual(first.campos["txtFornecedor"], 'POSTO "ESTRELA" LTDA')
        self.assertEqual(second.campos["txtTrecho"], "BSB/GRU\nGRU/BSB")
        self.assertEqual(cota.total_camara(self.db, 2099), 3000)


class LoadingAgain(CotaTestCase):
    def test_the_same_file_twice_changes_nothing(self):
        rows = [receipt(1001, "100"), receipt(1001, "50")]
        path = year_file(self.folder, 2099, rows)

        cota.load_cota(self.db, path)
        cota.load_cota(self.db, path)
        # The Camara rebuilds every zip each night: the same receipts arrive in a file whose bytes differ.
        cota.load_cota(self.db, year_file(self.folder / "tomorrow", 2099, rows))

        self.assertEqual(cota.total_camara(self.db, 2099), 15000)
        self.assertEqual(len(cota.receipts(self.db, 2099, deputy=1001)), 2)

    def test_a_newer_file_replaces_its_year_and_only_its_year(self):
        cota.load_cota(self.db, year_file(self.folder, 2098, [receipt(1001, "999")]))
        cota.load_cota(self.db, year_file(self.folder / "monday", 2099, [
            receipt(1001, "100", ideDocumento="1"),
            receipt(1001, "50", ideDocumento="2"),
            receipt(2002, "70", ideDocumento="3"),
        ]))

        # Tuesday: receipt 1 was corrected down, receipt 2 was withdrawn, receipt 4 is new.
        cota.load_cota(self.db, year_file(self.folder / "tuesday", 2099, [
            receipt(1001, "80", ideDocumento="1"),
            receipt(2002, "70", ideDocumento="3"),
            receipt(2002, "5.25", ideDocumento="4"),
        ]))

        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=1001), 8000)
        self.assertEqual(cota.total_liquido(self.db, 2099, deputy=2002), 7525)
        self.assertEqual(cota.total_camara(self.db, 2099), 15525)
        self.assertEqual(cota.total_camara(self.db, 2098), 99900)


def unclosed_quote(folder):
    """A receipt whose last field never closes its quote, followed by an unquoted receipt.

    A forgiving CSV reader takes the second receipt for the tail of the first one's last field:
    one receipt of R$ 100,00 where the file holds R$ 150,00.
    """
    first = row_line(2099, receipt(1001, "100"))[:-2] + "\n"
    second = row_line(2099, receipt(1001, "50")).replace('"', "")
    return raw_year_file(folder, 2099, header_line() + first + second)


def not_utf8(folder):
    text = header_line() + row_line(2099, receipt(1001, "100", txtFornecedor="POSTO"))
    return raw_year_file(folder, 2099, b"\xef\xbb\xbf" + text.encode("utf-8").replace(b"POSTO", b"POST\xff"))


def damaged_inside(folder):
    """A zip whose list of contents is intact and whose compressed data is not."""
    path = year_file(folder, 2099, [receipt(1001, "1", ideDocumento=str(n)) for n in range(200)])
    data = bytearray(path.read_bytes())
    data[80:120] = bytes(40)
    path.write_bytes(data)
    return path


class RefusingABadFile(CotaTestCase):
    """A file we cannot read exactly is refused whole: the year keeps what it had."""

    def setUp(self):
        super().setUp()
        cota.load_cota(self.db, year_file(self.folder / "good", 2099, [receipt(1001, "100")]))
        self.bad = self.folder / "bad"

    def refused_file(self, path):
        with self.assertRaises(cota.BadFile) as caught:
            cota.load_cota(self.db, path)
        self.assertEqual(cota.total_camara(self.db, 2099), 10000)
        self.assertEqual(len(cota.receipts(self.db, 2099, deputy=1001)), 1)
        return str(caught.exception)

    def refused(self, rows, **file_options):
        return self.refused_file(year_file(self.bad, 2099, rows, **file_options))

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

    def test_a_month_that_does_not_exist(self):
        self.assertIn("line 2", self.refused([receipt(1001, "50", numMes="13")]))
        self.assertIn("line 3", self.refused([receipt(1001, "50"), receipt(1001, "60", numMes="0")]))

    def test_a_receipt_that_is_an_exact_copy_of_another(self):
        # The real files have none in 19 years (counted 2026-10-12), so one would be the Camara
        # sending the same reimbursement twice: it must not be paid into the totals twice.
        message = self.refused([receipt(1001, "50"), receipt(1001, "60"), receipt(1001, "50")])
        self.assertIn("line 4", message)
        self.assertIn("line 2", message)

    def test_a_bad_receipt_after_one_that_spans_lines_is_named_by_the_line_where_it_starts(self):
        message = self.refused([receipt(1001, "20", txtTrecho="BSB/GRU\nGRU/BSB"), receipt(1001, "18.235")])
        self.assertIn("line 4", message)

    def test_numbers_written_any_way_but_plain_digits(self):
        # The real files use plain digits with no sign, space or leading zero (checked 2026-10-12).
        # Anything else is not cleaned up: "007" is not quietly deputy 7.
        cases = {
            "numMes": [" 1", "+1", "01", "1_0", "１"],
            "ideCadastro": ["007", " 1001", "1_001", "0", "１００１", "99999999999999999999"],
            "numAno": ["2099 ", "+2099", "２０９９"],
            "vlrLiquido": ["１２.３４", "99999999999999999999", "+5"],
        }
        for column, values in cases.items():
            for value in values:
                with self.subTest(column=column, value=value):
                    self.refused([receipt(1001, "50", **{column: value})])

    def test_a_line_with_an_unclosed_quote_that_would_swallow_the_next_receipt(self):
        self.refused_file(unclosed_quote(self.bad))

    def test_text_that_is_not_utf8(self):
        self.refused_file(not_utf8(self.bad))

    def test_a_zip_damaged_inside(self):
        self.refused_file(damaged_inside(self.bad))

    def test_a_download_that_is_not_a_zip(self):
        self.bad.mkdir()
        path = self.bad / "Ano-2099.csv.zip"
        path.write_bytes(b"<html><body>503 Service Unavailable</body></html>")

        self.refused_file(path)

    def test_a_zip_that_is_not_one_yearly_file(self):
        self.bad.mkdir()
        for members in (["Ano-2099.csv", "extra.txt"], ["despesas.csv"], []):
            with self.subTest(members=members):
                path = self.bad / "Ano-2099.csv.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    for member in members:
                        archive.writestr(member, "x")

                self.refused_file(path)


class RefusingAFileWhoseNamesLie(CotaTestCase):
    """The year is said three times: by the zip's name, by the CSV's name, by every row. All must agree."""

    def setUp(self):
        super().setUp()
        self.rows_of_2098 = [receipt(1001, "7")]
        cota.load_cota(self.db, year_file(self.folder / "older", 2098, self.rows_of_2098))

    def refused(self, path):
        with self.assertRaises(cota.BadFile):
            cota.load_cota(self.db, path)
        self.assertEqual(cota.total_camara(self.db, 2098), 700)
        self.assertEqual(cota.total_camara(self.db, 2099), 0)

    def test_the_csv_is_named_for_one_year_and_its_rows_say_another(self):
        # Ano-2099.csv inside, every row saying 2098: it must not replace 2098.
        self.refused(year_file(self.folder / "bad", 2099, [receipt(1001, "1", numAno="2098")]))

    def test_the_zip_is_named_for_one_year_and_holds_another(self):
        # Ano-2099.csv.zip holding a perfectly good Ano-2098.csv: it must not replace 2098 either.
        self.refused(year_file(self.folder / "bad", 2098, [receipt(1001, "1")], zip_year=2099))

    def test_a_name_written_with_digits_other_than_the_plain_ten_is_no_yearly_file(self):
        self.assertEqual(cota.year_of("Ano-2099.csv.zip"), 2099)
        self.assertIsNone(cota.year_of("Ano-２０９９.csv.zip"))

    def test_content_already_loaded_as_one_year_arrives_named_as_another(self):
        # The very bytes loaded as 2098, repackaged as Ano-2099.csv: "already loaded" must not wave it through.
        self.refused(year_file(self.folder / "bad", 2098, self.rows_of_2098, zip_year=2099, member_year=2099))


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

    def test_a_receipt_that_spans_lines_is_found_on_the_line_where_it_starts(self):
        # 27 real receipts have a line break inside a field (counted 2026-10-12).
        path = year_file(self.folder, 2099, [
            receipt(1001, "10"),
            receipt(1001, "20", txtTrecho="BSB/GRU\nGRU/BSB"),
            receipt(1001, "30"),
        ])

        cota.load_cota(self.db, path)

        self.assertEqual([r.linha for r in cota.receipts(self.db, 2099, deputy=1001)], [2, 3, 5])

    def test_the_invoice_value_is_shown_as_written_even_when_it_is_not_exact_centavos(self):
        path = year_file(self.folder, 2099, [receipt(1001, "18.23", vlrDocumento="18.235")])

        cota.load_cota(self.db, path)

        [only] = cota.receipts(self.db, 2099, deputy=1001)
        self.assertEqual(only.campos["vlrDocumento"], "18.235")
        self.assertEqual(only.liquido_centavos, 1823)


class ReadingWhileALoadIsWriting(CotaTestCase):
    def test_a_reader_sees_the_whole_old_year_until_the_new_one_is_complete(self):
        path = self.folder / "site.db"
        writer, reader = cota.connect(path), cota.connect(path)
        self.addCleanup(writer.close)
        self.addCleanup(reader.close)
        cota.load_cota(writer, year_file(self.folder / "monday", 2099, [receipt(1001, "100"), receipt(1001, "50")]))

        # SQLite calls this again and again while the writer works, so the reader looks in at
        # every stage of the replacement: after the old year is deleted, between the inserts.
        seen = set()
        writer.set_progress_handler(lambda: seen.add((cota.receipt_count(reader, 2099), cota.total_camara(reader, 2099))), 1)
        cota.load_cota(writer, year_file(self.folder / "tuesday", 2099, [
            receipt(1001, "80"), receipt(2002, "5.25"), receipt(2002, "1"),
        ]))
        writer.set_progress_handler(None, 1)

        # It looked in during the old year, and never saw anything but a whole year: no empty
        # year after the delete, no year with one or two of the three new receipts.
        whole_old, whole_new = (2, 15000), (3, 8625)
        self.assertIn(whole_old, seen)
        self.assertLessEqual(seen, {whole_old, whole_new})
        self.assertEqual((cota.receipt_count(reader, 2099), cota.total_camara(reader, 2099)), whole_new)

    def test_a_reader_is_not_locked_out_while_a_writer_holds_the_database(self):
        path = self.folder / "site.db"
        writer = cota.connect(path)
        self.addCleanup(writer.close)
        cota.load_cota(writer, year_file(self.folder, 2099, [receipt(1001, "100")]))
        # Without the write-ahead log this lock makes the reader fail with "database is locked".
        writer.execute("BEGIN EXCLUSIVE")
        self.addCleanup(writer.rollback)

        reader = cota.connect(path)
        self.addCleanup(reader.close)

        self.assertEqual(cota.total_camara(reader, 2099), 10000)


class WhatAFileSays(CotaTestCase):
    """file_totals is the second opinion on a file, so it must be as hard to fool as the loader."""

    def test_it_refuses_what_the_loader_refuses(self):
        builders = {
            "unclosed quote": unclosed_quote,
            "not utf-8": not_utf8,
            "damaged inside": damaged_inside,
            "zip named for another year": lambda folder: year_file(folder, 2098, [receipt(1001, "1")], zip_year=2099),
            "rows of another year": lambda folder: year_file(folder, 2099, [receipt(1001, "1", numAno="2098")]),
        }
        for name, build in builders.items():
            with self.subTest(name), self.assertRaises(cota.BadFile):
                cota.file_totals(build(self.folder / name))


if __name__ == "__main__":
    unittest.main()
