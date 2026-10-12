"""Command line.

    python -m tres_poderes load-cota     load every downloaded yearly quota file of the Camara
    python -m tres_poderes verify-cota   check that the database says what the files say

The files come from tools/download_camara_cota.py. A file that cannot be read exactly is
refused and the command ends with a non-zero code, so a nightly job stops there instead of
publishing from a partial load.
"""
import argparse
import pathlib
import sys

from tres_poderes import cota
from tres_poderes.money import format_brl

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_cota(raw, database, out):
    db = cota.connect(database)
    refused = 0
    try:
        for path in sorted(raw.glob("Ano-*.csv.zip")):
            try:
                year = cota.load_cota(db, path)
            except cota.BadFile as error:
                refused += 1
                print(f"{path.name}: REFUSED, {error}", file=out)
            else:
                print(f"{year}: {format_brl(cota.total_liquido(db, year))}", file=out)
    finally:
        db.close()
    return 1 if refused else 0


def verify_cota(raw, database, out):
    db = cota.connect(database)
    different = 0
    try:
        for path in sorted(raw.glob("Ano-*.csv.zip")):
            year, count, centavos = cota.file_totals(path)
            held_count, held_centavos = cota.receipt_count(db, year), cota.total_liquido(db, year)
            if (held_count, held_centavos) == (count, centavos):
                print(f"{year}: {count} receipts, {format_brl(centavos)}", file=out)
            else:
                different += 1
                print(
                    f"{year}: DIFFERENT, the file has {count} receipts and {format_brl(centavos)}, "
                    f"the database holds {held_count} and {format_brl(held_centavos)}",
                    file=out,
                )
    finally:
        db.close()
    return 1 if different else 0


COMMANDS = {
    "load-cota": (load_cota, "load every downloaded yearly quota file of the Camara"),
    "verify-cota": (verify_cota, "check that the database says what the files say, to the centavo"),
}


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="python -m tres_poderes")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, (_, text) in COMMANDS.items():
        command = commands.add_parser(name, help=text)
        command.add_argument("--raw", type=pathlib.Path, default=ROOT / "data" / "raw" / "camara" / "cota")
        command.add_argument("--db", type=pathlib.Path, default=ROOT / "data" / "tres_poderes.db")
    args = parser.parse_args(argv)
    return COMMANDS[args.command][0](args.raw, args.db, out)


if __name__ == "__main__":
    sys.exit(main())
