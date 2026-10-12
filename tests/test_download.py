import datetime
import io
import pathlib
import tempfile
import unittest

from tests.samples import receipt, year_file
from tres_poderes import cota, download
from tres_poderes.__main__ import main

NOW = datetime.datetime(2099, 3, 1, 3, 0, tzinfo=datetime.timezone.utc)


class FakeCamara:
    """Stands in for the Camara's server: what it would answer for each year, without the network."""

    def __init__(self, folder):
        self.folder = folder
        self.answers = {}
        self.built = 0

    def publishes(self, year, receipts):
        # Each call builds a fresh zip, as the Camara does every night, in a folder of its own.
        self.built += 1
        path = year_file(self.folder / f"server-{self.built}", year, receipts)
        self.answers[year] = download.Answer(200, path.read_bytes(), "Mon, 01 Mar 2099 06:00:00 GMT")

    def answers_with(self, year, status, body=b""):
        self.answers[year] = download.Answer(status, body, None)

    def breaks_for(self, year):
        self.answers[year] = OSError("connection reset")

    def fetch(self, url):
        year = int(url.rsplit("Ano-", 1)[1][:4])
        answer = self.answers.get(year, download.Answer(404, b"", None))
        if isinstance(answer, Exception):
            raise answer
        return answer


class DownloadTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.raw = pathlib.Path(folder.name) / "raw"
        self.camara = FakeCamara(pathlib.Path(folder.name))

    def download(self, *years, keep=10):
        return download.download_cota(self.raw, years, fetch=self.camara.fetch, now=lambda: NOW, keep=keep, pause=0)

    def current_total(self, year):
        return cota.file_totals(self.raw / f"Ano-{year}.csv.zip").centavos

    def kept_totals(self, year):
        return [cota.file_totals(version.path).centavos for version in download.versions(self.raw, year)]


class DownloadingAYear(DownloadTestCase):
    def test_a_downloaded_year_is_there_to_be_loaded(self):
        self.camara.publishes(2099, [receipt(1001, "100"), receipt(1001, "50")])

        [outcome] = self.download(2099)

        self.assertTrue(outcome.ok)
        self.assertEqual(self.current_total(2099), 15000)
        db = cota.connect(":memory:")
        self.addCleanup(db.close)
        self.assertEqual(cota.load_cota(db, self.raw / "Ano-2099.csv.zip"), 2099)

    def test_yesterdays_file_is_kept_when_todays_is_different(self):
        self.camara.publishes(2099, [receipt(1001, "100")])
        self.download(2099)
        self.camara.publishes(2099, [receipt(1001, "80")])  # the Camara corrected the receipt

        self.download(2099)

        self.assertEqual(self.current_total(2099), 8000)
        self.assertEqual(self.kept_totals(2099), [10000, 8000])

    def test_a_kept_version_carries_the_hash_the_database_records(self):
        self.camara.publishes(2099, [receipt(1001, "100")])
        self.download(2099)
        db = cota.connect(":memory:")
        self.addCleanup(db.close)
        cota.load_cota(db, self.raw / "Ano-2099.csv.zip")

        [version] = download.versions(self.raw, 2099)

        # The trail from a number to its file: receipt -> hash in the database -> this kept file.
        self.assertEqual(version.sha256, cota.loaded_sha256(db, 2099))
        self.assertEqual(version.downloaded_at, NOW.isoformat())

    def test_the_same_receipts_in_a_rebuilt_zip_are_not_a_new_version(self):
        rows = [receipt(1001, "100")]
        self.camara.publishes(2099, rows)
        self.download(2099)
        self.camara.publishes(2099, rows)  # other bytes, same receipts

        [outcome] = self.download(2099)

        self.assertTrue(outcome.ok)
        self.assertEqual(self.kept_totals(2099), [10000])

    def test_only_the_most_recent_versions_are_kept(self):
        for day in range(1, 13):
            self.camara.publishes(2099, [receipt(1001, str(day))])
            self.download(2099, keep=10)

        # Twelve days, ten kept: the two oldest are gone, the newest is the one in use.
        self.assertEqual(self.kept_totals(2099), [day * 100 for day in range(3, 13)])
        self.assertEqual(self.current_total(2099), 1200)


class RefusingADownload(DownloadTestCase):
    """Whatever goes wrong, the file we already had stays, and the run is told."""

    def setUp(self):
        super().setUp()
        self.camara.publishes(2099, [receipt(1001, "100")])
        self.download(2099)

    def refused(self, year=2099):
        [outcome] = self.download(year)
        self.assertFalse(outcome.ok)
        self.assertEqual(self.current_total(2099), 10000)
        self.assertEqual(self.kept_totals(2099), [10000])
        return outcome.message

    def test_an_error_page_sent_with_a_success_code(self):
        self.camara.answers_with(2099, 200, b"<html><body>Manutencao</body></html>")
        self.refused()

    def test_another_years_file_sent_under_this_years_address(self):
        other = year_file(self.raw.parent / "elsewhere", 2098, [receipt(1001, "1")])
        self.camara.answers_with(2099, 200, other.read_bytes())
        self.refused()

    def test_a_file_the_loader_would_refuse(self):
        self.camara.publishes(2099, [receipt(1001, "18.235")])
        self.assertIn("18.235", self.refused())

    def test_a_server_error(self):
        self.camara.answers_with(2099, 503)
        self.assertIn("503", self.refused())

    def test_a_broken_connection(self):
        self.camara.breaks_for(2099)
        self.assertIn("connection reset", self.refused())

    def test_a_year_we_hold_that_the_camara_stops_serving(self):
        self.camara.answers_with(2099, 404)
        self.refused()


class ManyYears(DownloadTestCase):
    def test_a_year_that_does_not_exist_yet_is_not_a_failure(self):
        # Early January: the new year's file is not published yet, and we never had one.
        [outcome] = self.download(2100)

        self.assertTrue(outcome.ok)
        self.assertFalse((self.raw / "Ano-2100.csv.zip").exists())

    def test_one_year_failing_does_not_stop_the_others(self):
        self.camara.breaks_for(2098)
        self.camara.publishes(2099, [receipt(1001, "100")])

        first, second = self.download(2098, 2099)

        self.assertFalse(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(self.current_total(2099), 10000)

    def test_the_command_fails_when_any_year_does(self):
        self.camara.publishes(2098, [receipt(1001, "7")])
        self.camara.answers_with(2099, 503)
        out = io.StringIO()
        arguments = ["download-cota", "--raw", str(self.raw), "--pause", "0", "2098", "2099"]

        code = main(arguments, out=out, fetch=self.camara.fetch)

        self.assertEqual(code, 1)
        self.assertEqual(self.current_total(2098), 700)
        [failed] = [line for line in out.getvalue().splitlines() if "FAILED" in line]
        self.assertIn("2099", failed)

        self.camara.publishes(2099, [receipt(1001, "5")])
        self.assertEqual(main(arguments, out=io.StringIO(), fetch=self.camara.fetch), 0)


if __name__ == "__main__":
    unittest.main()
