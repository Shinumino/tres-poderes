import io
import pathlib
import tempfile
import unittest

from tests.samples import receipt, year_file
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
        return cota.total_liquido(db, year)

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


if __name__ == "__main__":
    unittest.main()
