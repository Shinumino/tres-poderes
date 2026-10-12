import io
import pathlib
import sqlite3
import tempfile
import unittest

from tests.samples import header_line, raw_year_file, receipt, year_file
from tres_poderes import cota
from tres_poderes.__main__ import main


class LoadCotaCommand(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.raw = pathlib.Path(folder.name)
        self.database = self.raw / "out.db"
        self.out = io.StringIO()

    def run_command(self):
        return main(["load-cota", "--raw", str(self.raw), "--db", str(self.database)], out=self.out)

    def total(self, year):
        db = cota.connect(self.database)
        self.addCleanup(db.close)
        return cota.total_camara(db, year)

    def test_it_loads_every_year_file_in_the_folder_and_says_what_each_year_cost(self):
        year_file(self.raw, 2098, [receipt(1001, "10")])
        year_file(self.raw, 2099, [receipt(1001, "1467"), receipt(None, "5.50")])

        code = self.run_command()

        self.assertEqual(code, 0)
        self.assertEqual(self.total(2098), 1000)
        self.assertEqual(self.total(2099), 147250)
        self.assertIn("2099", self.out.getvalue())
        self.assertIn("R$ 1.472,50", self.out.getvalue())

    def test_a_bad_year_is_reported_and_fails_the_run_while_good_years_still_load(self):
        year_file(self.raw, 2098, [receipt(1001, "10")])
        year_file(self.raw, 2099, [receipt(1001, "18.235")])

        code = self.run_command()

        # Not zero: the nightly job must go red, so nothing is published from a partial load.
        self.assertEqual(code, 1)
        self.assertEqual(self.total(2098), 1000)
        self.assertEqual(self.total(2099), 0)
        self.assertIn("Ano-2099.csv.zip", self.out.getvalue())
        self.assertIn("18.235", self.out.getvalue())

    def test_a_folder_with_no_yearly_files_fails_instead_of_passing_quietly(self):
        # A mistyped --raw used to "load" nothing and report success over a stale database.
        code = self.run_command()

        self.assertEqual(code, 1)
        self.assertIn(str(self.raw), self.out.getvalue())

    def test_a_corrupt_download_is_refused_and_the_other_years_still_load(self):
        year_file(self.raw, 2098, [receipt(1001, "10")])
        (self.raw / "Ano-2099.csv.zip").write_bytes(b"<html><body>503 Service Unavailable</body></html>")

        code = self.run_command()

        self.assertEqual(code, 1)
        self.assertEqual(self.total(2098), 1000)
        self.assertIn("Ano-2099.csv.zip", self.out.getvalue())

    def test_the_database_folder_is_created_on_a_fresh_checkout(self):
        year_file(self.raw, 2099, [receipt(1001, "10")])
        self.database = self.raw / "not" / "there" / "yet" / "out.db"

        self.assertEqual(self.run_command(), 0)
        self.assertEqual(self.total(2099), 1000)


class VerifyCotaCommand(unittest.TestCase):
    """Does the database say what the files on disk say, to the centavo and to the row?"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.raw = pathlib.Path(folder.name)
        self.arguments = ["--raw", str(self.raw), "--db", str(self.raw / "out.db")]
        year_file(self.raw, 2098, [receipt(1001, "10")])
        year_file(self.raw, 2099, [receipt(1001, "1467"), receipt(1001, "-298.07"), receipt(None, "5.50")])
        main(["load-cota", *self.arguments], out=io.StringIO())
        self.out = io.StringIO()

    def test_a_database_that_matches_its_files_passes(self):
        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 0)
        self.assertIn("2099: 3 receipts, R$ 1.174,43", self.out.getvalue())

    def test_a_file_that_changed_since_it_was_loaded_fails_and_names_the_year(self):
        # A new download arrived (one receipt more) and nobody loaded it.
        year_file(self.raw, 2099, [receipt(1001, "1467"), receipt(1001, "-298.07"), receipt(None, "5.50"), receipt(2002, "0.01")])

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        [line] = [line for line in self.out.getvalue().splitlines() if "DIFFERENT" in line]
        self.assertIn("2099", line)
        self.assertIn("R$ 1.174,44", line)  # what the file says now
        self.assertIn("R$ 1.174,43", line)  # what the database holds

    def test_a_year_on_disk_that_was_never_loaded_fails(self):
        year_file(self.raw, 2097, [receipt(1001, "1")])

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        self.assertIn("2097", self.out.getvalue())

    def different_lines(self):
        return [line for line in self.out.getvalue().splitlines() if "DIFFERENT" in line]

    def test_a_receipt_moved_to_another_deputy_fails_though_count_and_total_match(self):
        # Same three receipts, same R$ 1.174,43: but the R$ 1.467,00 now belongs to deputy 2002.
        year_file(self.raw, 2099, [receipt(2002, "1467"), receipt(1001, "-298.07"), receipt(None, "5.50")])

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        [line] = self.different_lines()
        self.assertIn("2099", line)
        self.assertIn("2002", line)

    def test_a_receipt_that_changed_without_changing_any_total_fails(self):
        # Only the month moved: every sum is the same, the content is not.
        year_file(self.raw, 2099, [receipt(1001, "1467", numMes="2"), receipt(1001, "-298.07"), receipt(None, "5.50")])

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        [line] = self.different_lines()
        self.assertIn("2099", line)

    def test_a_folder_with_no_yearly_files_fails_instead_of_passing_quietly(self):
        for path in self.raw.glob("Ano-*.csv.zip"):
            path.unlink()

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        self.assertIn(str(self.raw), self.out.getvalue())

    def test_a_year_in_the_database_whose_file_is_gone_fails(self):
        (self.raw / "Ano-2098.csv.zip").unlink()

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        [line] = [line for line in self.out.getvalue().splitlines() if "2098" in line]
        self.assertIn("no file", line)

    def test_a_file_that_cannot_be_read_is_reported_and_the_rest_still_checked(self):
        unreadable = {
            "not a zip": lambda: (self.raw / "Ano-2098.csv.zip").write_bytes(b"not a zip at all"),
            "not utf-8": lambda: raw_year_file(self.raw, 2098, b"\xef\xbb\xbf" + header_line().encode() + b'"\xff"\n'),
        }
        for name, spoil in unreadable.items():
            with self.subTest(name):
                self.out = io.StringIO()
                spoil()

                code = main(["verify-cota", *self.arguments], out=self.out)

                self.assertEqual(code, 1)
                [line] = [line for line in self.out.getvalue().splitlines() if "UNREADABLE" in line]
                self.assertIn("Ano-2098.csv.zip", line)
                self.assertIn("2099: 3 receipts, R$ 1.174,43", self.out.getvalue())

    def tamper(self, sql):
        db = sqlite3.connect(self.raw / "out.db")
        with db:
            db.execute(sql)
        db.close()

    def test_stored_receipts_changed_behind_the_loaders_back_fail(self):
        # No total moves and the file is untouched: only the stored receipts are not the file's any more.
        self.tamper("UPDATE camara_cota SET mes = 12, numMes = '12' WHERE ano = 2099")

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        [line] = self.different_lines()
        self.assertIn("2099", line)

    def test_stored_line_numbers_that_no_longer_point_at_the_receipts_fail(self):
        self.tamper("UPDATE camara_cota SET linha = linha + 100 WHERE ano = 2099")

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        [line] = self.different_lines()
        self.assertIn("2099", line)

    def test_a_stored_month_that_disagrees_with_the_receipts_own_text_fails(self):
        self.tamper("UPDATE camara_cota SET mes = 12 WHERE ano = 2099 AND liquido_centavos = 146700")

        code = main(["verify-cota", *self.arguments], out=self.out)

        self.assertEqual(code, 1)
        [line] = self.different_lines()
        self.assertIn("line 2", line)


if __name__ == "__main__":
    unittest.main()
