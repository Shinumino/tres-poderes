"""Command line.

    python -m tres_poderes download-cota   download the Camara's yearly quota files
    python -m tres_poderes load-cota       load every downloaded file into the database
    python -m tres_poderes verify-cota     check that the database says what the files say
A file that cannot be read exactly is
refused and the command ends with a non-zero code, so a nightly job stops there instead of
publishing from a partial load.
"""
import argparse
import datetime
import pathlib
import sys

from tres_poderes import cota, download
from tres_poderes.money import format_brl

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _year_files(raw, out):
    """The yearly zips in the folder. None at all is a failure, never "nothing to do".

    A mistyped folder used to load nothing, verify nothing and report success, over a database
    that was by then days old.
    """
    paths = sorted(raw.glob("Ano-*.csv.zip"))
    if not paths:
        print(f"no yearly files (Ano-<year>.csv.zip) in {raw}", file=out)
    return paths


def _open(database):
    database.parent.mkdir(parents=True, exist_ok=True)
    return cota.connect(database)


def load_cota(raw, database, out):
    paths = _year_files(raw, out)
    if not paths:
        return 1
    db = _open(database)
    refused = 0
    try:
        for path in paths:
            try:
                year = cota.load_cota(db, path)
            except cota.BadFile as error:
                refused += 1
                print(f"{path.name}: REFUSED, {error}", file=out)
            else:
                print(f"{year}: {format_brl(cota.total_camara(db, year))}", file=out)
    finally:
        db.close()
    return 1 if refused else 0


def _difference(db, said):
    """Why the database does not say what the file says, or None when it does.

    Each check catches what the one before cannot: the count and total of the year; the total of
    every deputy (a receipt credited to the wrong person changes neither of the first two); the
    typed columns against the receipt's own text; and last the year rebuilt from the database,
    which must hash to the official file (a month or a supplier changing moves no total at all).
    """
    held = cota.receipt_count(db, said.ano), cota.total_camara(db, said.ano)
    if held != (said.receipts, said.centavos):
        return (
            f"the file has {said.receipts} receipts and {format_brl(said.centavos)}, "
            f"the database holds {held[0]} and {format_brl(held[1])}"
        )
    by_deputy = cota.totals_by_deputy(db, said.ano)
    moved = sorted((d for d in by_deputy.keys() | said.por_deputado.keys() if by_deputy.get(d) != said.por_deputado.get(d)), key=str)
    if moved:
        def amount(centavos):
            return "nothing" if centavos is None else format_brl(centavos)

        shown = "; ".join(
            f"{'leaderships' if d is None else f'deputy {d}'}: file {amount(said.por_deputado.get(d))}, database {amount(by_deputy.get(d))}"
            for d in moved[:5]
        )
        return f"same count and total, but {len(moved)} differ by deputy ({shown})"
    stored_sha256, disagree = cota.stored_content(db, said.ano)
    if disagree:
        shown = ", ".join(f"line {line}" for line in disagree[:5])
        return f"{len(disagree)} stored receipts disagree with their own text ({shown})"
    if stored_sha256 != said.sha256 or cota.loaded_sha256(db, said.ano) != said.sha256:
        return "every total matches, but the receipts stored are not exactly the receipts in the file"
    return None


def verify_cota(raw, database, out):
    paths = _year_files(raw, out)
    if not paths:
        return 1
    db = _open(database)
    problems = 0
    try:
        for path in paths:
            try:
                said = cota.file_totals(path)
            except cota.BadFile as error:
                problems += 1
                print(f"{path.name}: UNREADABLE, {error}", file=out)
                continue
            difference = _difference(db, said)
            if difference:
                problems += 1
                print(f"{said.ano}: DIFFERENT, {difference}", file=out)
            else:
                print(f"{said.ano}: {said.receipts} receipts, {format_brl(said.centavos)}", file=out)
        named = {cota.year_of(path) for path in paths}
        for year in cota.years(db):
            if year not in named:
                problems += 1
                print(f"{year}: in the database, but there is no file for it in {raw}", file=out)
    finally:
        db.close()
    return 1 if problems else 0


def download_cota(raw, years, keep, pause, fetch, out):
    years = years or range(download.FIRST_YEAR, datetime.date.today().year + 1)
    failed = 0
    for outcome in download.download_cota(raw, years, fetch=fetch, keep=keep, pause=pause):
        failed += not outcome.ok
        print(f"{outcome.year}: {'' if outcome.ok else 'FAILED, '}{outcome.message}", file=out)
    return 1 if failed else 0


COMMANDS = {
    "load-cota": (load_cota, "load every downloaded yearly quota file of the Camara"),
    "verify-cota": (verify_cota, "check that the database says what the files say, to the centavo"),
}


def main(argv=None, out=sys.stdout, fetch=download.fetch):
    raw = ROOT / "data" / "raw" / "camara" / "cota"
    parser = argparse.ArgumentParser(prog="python -m tres_poderes")
    commands = parser.add_subparsers(dest="command", required=True)
    get = commands.add_parser("download-cota", help="download the Camara's yearly quota files, keeping the versions replaced")
    get.add_argument("years", nargs="*", type=int, help="only these years (default: every year from 2008)")
    get.add_argument("--raw", type=pathlib.Path, default=raw)
    get.add_argument("--keep", type=int, default=download.KEEP, help="versions kept per year")
    get.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    for name, (_, text) in COMMANDS.items():
        command = commands.add_parser(name, help=text)
        command.add_argument("--raw", type=pathlib.Path, default=raw)
        command.add_argument("--db", type=pathlib.Path, default=ROOT / "data" / "tres_poderes.db")
    args = parser.parse_args(argv)
    if args.command == "download-cota":
        return download_cota(args.raw, args.years, args.keep, args.pause, fetch, out)
    return COMMANDS[args.command][0](args.raw, args.db, out)


if __name__ == "__main__":
    sys.exit(main())
