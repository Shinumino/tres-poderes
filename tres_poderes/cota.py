"""The Camara's quota for parliamentary activity (Cota Parlamentar, CEAP), from its yearly bulk files.

One row of the file is one receipt. Every column is kept exactly as the Camara wrote it, under
the Camara's own column name, so a row here can be laid beside the official file and compared by
eye. The few typed columns next to them (year, month, deputy, centavos) are what the totals use.
"""
import csv
import dataclasses
import decimal
import hashlib
import io
import itertools
import sqlite3
import zipfile

from tres_poderes.money import parse_centavos

# The header of every yearly file from 2008 to 2026 (identical in all 19, checked 2026-10-12).
COLUMNS = (
    "txNomeParlamentar", "cpf", "ideCadastro", "nuCarteiraParlamentar", "nuLegislatura", "sgUF", "sgPartido",
    "codLegislatura", "numSubCota", "txtDescricao", "numEspecificacaoSubCota", "txtDescricaoEspecificacao",
    "txtFornecedor", "txtCNPJCPF", "txtNumero", "indTipoDocumento", "datEmissao", "vlrDocumento", "vlrGlosa",
    "vlrLiquido", "numMes", "numAno", "numParcela", "txtPassageiro", "txtTrecho", "numLote", "numRessarcimento",
    "datPagamentoRestituicao", "vlrRestituicao", "nuDeputadoId", "ideDocumento", "urlDocumento",
)

# STRICT: SQLite refuses a value of the wrong type instead of storing it anyway. A total can
# only be summed from integers because nothing else can get into the column.
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS camara_cota (
    ano INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    deputado INTEGER,                      -- ideCadastro; NULL when the money was spent by no one deputy
    liquido_centavos INTEGER NOT NULL,     -- vlrLiquido: what the quota actually paid
    linha INTEGER NOT NULL,                -- line of this receipt in the year's file (the header is line 1)
    {", ".join(f'"{name}" TEXT NOT NULL' for name in COLUMNS)}
) STRICT;
CREATE INDEX IF NOT EXISTS camara_cota_deputado_ano ON camara_cota (deputado, ano);
CREATE TABLE IF NOT EXISTS camara_cota_arquivo (
    ano INTEGER PRIMARY KEY,               -- one current file per year
    -- Of the CSV inside the zip, not of the zip: the Camara rebuilds every zip each night, so the
    -- zip's bytes change even when not one receipt did.
    sha256 TEXT NOT NULL,
    nome TEXT NOT NULL
) STRICT;
"""

_TYPED = 5  # ano, mes, deputado, liquido_centavos, linha: the columns before the Camara's own
_INSERT = f"INSERT INTO camara_cota VALUES ({', '.join('?' * (_TYPED + len(COLUMNS)))})"


def connect(path):
    """Open (and if new, create) the database."""
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
    return db


class BadFile(ValueError):
    """The file cannot be read exactly as the Camara wrote it, so none of it is loaded.

    A year that is nine tenths right is worse than a year that is one day old: nobody can tell
    which tenth is wrong. The message names the line, so the row can be found in the file.
    """


def _typed(line, row, year):
    try:
        if None in row or None in row.values():
            raise ValueError("wrong number of fields")
        if int(row["numAno"]) != year:
            raise ValueError(f"numAno is {row['numAno']} in the file of {year}")
        liquido = parse_centavos(row["vlrLiquido"])
        if liquido is None:
            raise ValueError("vlrLiquido is empty")
        return (
            year,
            int(row["numMes"]),
            int(row["ideCadastro"]) if row["ideCadastro"] else None,
            liquido,
            line,
            *(row[name] for name in COLUMNS),
        )
    except ValueError as error:
        raise BadFile(f"line {line}: {error}") from error


def _sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()


def load_cota(db, zip_path):
    """Load one yearly file (Ano-<year>.csv.zip) as downloaded.

    The file IS the year: its rows replace whatever the year held, so a receipt the Camara
    corrected shows the new value and one it withdrew is gone. Content already loaded is left alone.
    Returns the year.
    """
    with zipfile.ZipFile(zip_path) as archive:
        (member,) = archive.namelist()
        with archive.open(member) as raw:
            content = _sha256(raw)
        already = db.execute("SELECT ano FROM camara_cota_arquivo WHERE sha256 = ?", (content,)).fetchone()
        if already:
            return already[0]
        with archive.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter=";")
            found = tuple(reader.fieldnames or ())
            if found != COLUMNS:
                # The Camara renaming or adding a column is exactly when a loader goes quietly wrong.
                missing, new = sorted(set(COLUMNS) - set(found)), sorted(set(found) - set(COLUMNS))
                raise BadFile(f"{member}: columns changed (missing {missing}, new {new}, or their order)")
            numbered = ((reader.line_num, row) for row in reader)
            first = next(numbered, None)
            if first is None:
                raise BadFile(f"{member}: no receipts")
            try:
                year = int(first[1]["numAno"])
            except (TypeError, ValueError) as error:
                raise BadFile(f"line {first[0]}: numAno is {first[1]['numAno']!r}") from error
            # One transaction: readers see the old year or the new year, never half of each.
            with db:
                db.execute("DELETE FROM camara_cota WHERE ano = ?", (year,))
                db.execute("INSERT OR REPLACE INTO camara_cota_arquivo VALUES (?, ?, ?)", (year, content, member))
                db.executemany(_INSERT, (_typed(line, row, year) for line, row in itertools.chain([first], numbered)))
            return year


@dataclasses.dataclass(frozen=True)
class Receipt:
    ano: int
    mes: int
    deputado: int | None      # None: spent under the quota by a party leadership, not by a deputy
    liquido_centavos: int
    linha: int                # where to find it: this line ...
    arquivo: str              # ... of this file ...
    sha256: str               # ... whose content has this hash
    campos: dict              # every column of the file, by the Camara's name, exactly as written


def _receipts(db, where, args):
    rows = db.execute(
        "SELECT c.*, a.nome, a.sha256 FROM camara_cota c JOIN camara_cota_arquivo a ON a.ano = c.ano "
        f"WHERE {where} ORDER BY c.mes, c.linha",
        args,
    )
    return [Receipt(*row[:_TYPED], *row[-2:], campos=dict(zip(COLUMNS, row[_TYPED:-2]))) for row in rows]


def receipts(db, year, deputy):
    """One deputy's receipts in a year, in month order."""
    return _receipts(db, "c.ano = ? AND c.deputado = ?", (year, deputy))


def receipts_without_deputy(db, year):
    """Receipts of the year that belong to no deputy: the party leaderships."""
    return _receipts(db, "c.ano = ? AND c.deputado IS NULL", (year,))


def file_totals(zip_path):
    """(year, receipts, centavos paid) of a yearly file, worked out a second way.

    On purpose this shares nothing with load_cota beyond reading the CSV: decimal arithmetic
    straight from the text instead of parse_centavos, a running sum instead of SQL. If the two
    ever disagree, one of them is wrong, and that is the reason to keep both.
    """
    years, count, total = set(), 0, decimal.Decimal(0)
    with zipfile.ZipFile(zip_path) as archive:
        (member,) = archive.namelist()
        with archive.open(member) as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter=";"):
                years.add(row["numAno"])
                count += 1
                total += decimal.Decimal(row["vlrLiquido"])
    (year,) = years
    centavos = total * 100
    if centavos != centavos.to_integral_value():
        raise BadFile(f"{member}: the amounts paid do not add up to whole centavos ({total})")
    return int(year), count, int(centavos)


def receipt_count(db, year):
    """How many receipts the year holds, leaderships included."""
    return db.execute("SELECT COUNT(*) FROM camara_cota WHERE ano = ?", (year,)).fetchone()[0]


def total_liquido(db, year, deputy=None):
    """Centavos the quota paid in a year: for one deputy, or for the whole Camara when deputy is None."""
    if deputy is None:
        query, args = "SELECT COALESCE(SUM(liquido_centavos), 0) FROM camara_cota WHERE ano = ?", (year,)
    else:
        query, args = "SELECT COALESCE(SUM(liquido_centavos), 0) FROM camara_cota WHERE ano = ? AND deputado = ?", (year, deputy)
    return db.execute(query, args).fetchone()[0]
