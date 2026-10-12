"""Does the bulk export carry at least what the old API crawl did?

Compares the Camara's yearly bulk files (data/raw/camara/cota) with the despesas_deputados
table of the earlier Super-trunfo database, which was filled by crawling the API deputy by
deputy. Three questions, in order: are the same fields there, are the same years there, and
does each deputy in each year have the same number of receipts for the same money.

    python tools/compare_cota_with_old_db.py <path to the old camara_deputados.db>

The old database is opened read-only and immutable: nothing is written next to it.
"""
import collections
import csv
import io
import pathlib
import sqlite3
import sys
import zipfile

RAW = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw" / "camara" / "cota"

# Old table column (the API's name) -> bulk file column. None = the bulk file has no such field.
API_TO_BULK = {
    "id_deputado": "ideCadastro",
    "ano": "numAno",
    "mes": "numMes",
    "tipoDespesa": "txtDescricao",
    "codDocumento": "ideDocumento",
    "codLote": "numLote",
    "codTipoDocumento": "indTipoDocumento",
    "dataDocumento": "datEmissao",
    "nomeFornecedor": "txtFornecedor",
    "cnpjCpfFornecedor": "txtCNPJCPF",
    "numDocumento": "txtNumero",
    "numRessarcimento": "numRessarcimento",
    "parcela": "numParcela",
    "tipoDocumento": None,  # the label of indTipoDocumento (0 Nota Fiscal, 1 Recibo, ...), derivable
    "urlDocumento": "urlDocumento",
    "valorDocumento": "vlrDocumento",
    "valorGlosa": "vlrGlosa",
    "valorLiquido": "vlrLiquido",
}


def cents(text):
    """Money as integer cents, so sums of millions of rows do not drift."""
    return round(float(text) * 100) if text not in (None, "") else 0


def read_bulk():
    """Aggregate every downloaded year. Returns (header, per deputy-year, per-year extras)."""
    people = collections.defaultdict(lambda: [0, 0, 0])  # (deputy id, year) -> rows, liquido, documento
    extras = collections.defaultdict(lambda: collections.Counter())
    header = None
    for path in sorted(RAW.glob("Ano-*.csv.zip")):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                with archive.open(name) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter=";")
                    header = header or reader.fieldnames
                    for row in reader:
                        year = int(row["numAno"])
                        extra = extras[year]
                        extra["rows"] += 1
                        extra["with_receipt_link"] += bool(row["urlDocumento"])
                        if not row["ideCadastro"]:
                            # Party leaderships and similar: money spent under the quota by no one deputy.
                            extra["rows_without_person"] += 1
                            extra["cents_without_person"] += cents(row["vlrLiquido"])
                            continue
                        bucket = people[(int(row["ideCadastro"]), year)]
                        bucket[0] += 1
                        bucket[1] += cents(row["vlrLiquido"])
                        bucket[2] += cents(row["vlrDocumento"])
    return header, people, extras


def read_old(db_path):
    con = sqlite3.connect(pathlib.Path(db_path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    columns = [c[1] for c in con.execute("PRAGMA table_info(despesas_deputados)")]
    # NOT INDEXED: one pass over the table. Through the (id_deputado, ano) index it would be
    # millions of row lookups on a file that lives on a synced drive.
    rows = con.execute(
        "SELECT id_deputado, ano, COUNT(*), SUM(valorLiquido), SUM(valorDocumento) "
        "FROM despesas_deputados NOT INDEXED GROUP BY id_deputado, ano"
    )
    people = {}
    for deputy, year, n, liquido, documento in rows:
        if year is None:
            continue
        people[(deputy, year)] = [n, round((liquido or 0) * 100), round((documento or 0) * 100)]
    con.close()
    return columns, people


def brl(value_cents):
    return f"{value_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__)
    header, bulk, extras = read_bulk()
    if header is None:
        sys.exit(f"no bulk files in {RAW}: run tools/download_camara_cota.py first")
    old_columns, old = read_old(argv[0])

    print("== 1. Fields ==")
    for api_name in old_columns:
        if api_name == "id_registro":
            continue
        bulk_name = API_TO_BULK.get(api_name, "?? unmapped")
        status = "missing in bulk" if bulk_name is None else ("ok" if bulk_name in header else "NOT IN HEADER")
        print(f"  {api_name:20} -> {str(bulk_name):18} {status}")
    only_bulk = [c for c in header if c not in set(API_TO_BULK.values())]
    print(f"  only in the bulk file: {', '.join(only_bulk)}")

    print("\n== 2. Years (people only; leadership rows shown apart) ==")
    print(f"  {'year':4} {'bulk rows':>10} {'old rows':>10} {'diff':>8}   {'bulk R$':>18} {'old R$':>18} {'diff R$':>16}   {'no person':>9} {'receipt link':>12}")
    years = sorted({y for _, y in bulk} | {y for _, y in old})
    for year in years:
        b = [v for (_, y), v in bulk.items() if y == year]
        o = [v for (_, y), v in old.items() if y == year]
        b_rows, b_cents = sum(v[0] for v in b), sum(v[1] for v in b)
        o_rows, o_cents = sum(v[0] for v in o), sum(v[1] for v in o)
        extra = extras.get(year, {})
        link = f"{100 * extra['with_receipt_link'] / extra['rows']:.0f}%" if extra.get("rows") else "-"
        print(f"  {year:4} {b_rows:>10,} {o_rows:>10,} {b_rows - o_rows:>+8,}   {brl(b_cents):>18} {brl(o_cents):>18} {brl(b_cents - o_cents):>16}   {extra.get('rows_without_person', 0):>9,} {link:>12}")

    print("\n== 3. Deputy by deputy, year by year ==")
    keys = set(bulk) | set(old)
    same = [k for k in keys if k in bulk and k in old and bulk[k][:2] == old[k][:2]]
    differ = [k for k in keys if k in bulk and k in old and bulk[k][:2] != old[k][:2]]
    only_bulk_keys = [k for k in keys if k not in old]
    only_old_keys = [k for k in keys if k not in bulk]
    print(f"  deputy-years: {len(keys):,}   identical: {len(same):,}   different: {len(differ):,}   only bulk: {len(only_bulk_keys):,}   only old: {len(only_old_keys):,}")
    by_year = collections.Counter(y for _, y in differ)
    print("  different, by year: " + ", ".join(f"{y}:{n}" for y, n in sorted(by_year.items())))
    print("  only in bulk, by year: " + ", ".join(f"{y}:{n}" for y, n in sorted(collections.Counter(y for _, y in only_bulk_keys).items())))
    print("  only in old, by year: " + ", ".join(f"{y}:{n}" for y, n in sorted(collections.Counter(y for _, y in only_old_keys).items())))
    print("  ten largest differences (deputy id, year: bulk rows/R$ vs old rows/R$):")
    for key in sorted(differ, key=lambda k: abs(bulk[k][1] - old[k][1]), reverse=True)[:10]:
        print(f"    {key[0]:>7} {key[1]}: {bulk[key][0]:>5} / {brl(bulk[key][1]):>14}   vs {old[key][0]:>5} / {brl(old[key][1]):>14}")


if __name__ == "__main__":
    main(sys.argv[1:])
