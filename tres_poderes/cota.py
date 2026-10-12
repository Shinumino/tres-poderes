"""The Camara's quota for parliamentary activity (Cota Parlamentar, CEAP), from its yearly bulk files.

One row of the file is one receipt. Every column is kept exactly as the Camara wrote it, under
the Camara's own column name, so a row here can be laid beside the official file and compared by
eye. The few typed columns next to them (year, month, deputy, centavos) are what the totals use.
"""
import collections
import contextlib
import csv
import dataclasses
import decimal
import hashlib
import io
import pathlib
import re
import sqlite3
import zipfile
import zlib

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
-- Year first: replacing, counting and summing a year all start from "WHERE ano = ?". With the
-- deputy first, each of those read the whole table (0.8 s per year total, measured 2026-10-12).
-- The amount rides along so a total is answered from the index alone.
CREATE INDEX IF NOT EXISTS camara_cota_ano_deputado ON camara_cota (ano, deputado, liquido_centavos);
CREATE TABLE IF NOT EXISTS camara_cota_arquivo (
    ano INTEGER PRIMARY KEY,               -- one current file per year
    -- Of the CSV inside the zip, not of the zip: the Camara rebuilds every zip each night, so the
    -- zip's bytes change even when not one receipt did.
    sha256 TEXT NOT NULL,
    nome TEXT NOT NULL
) STRICT;
"""

# [0-9], not \d: in Python \d also matches the digits of every other script.
_YEAR_ZIP = re.compile(r"Ano-([0-9]{4})\.csv\.zip")  # a yearly download, as the Camara names it
_YEAR_FILE = re.compile(r"Ano-([0-9]{4})\.csv")  # the one file inside every yearly zip, 2008 to 2026
_WHOLE = re.compile(r"[1-9][0-9]{0,8}")
_TYPED = 5  # ano, mes, deputado, liquido_centavos, linha: the columns before the Camara's own
_INSERT = f"INSERT INTO camara_cota VALUES ({', '.join('?' * (_TYPED + len(COLUMNS)))})"


def connect(path):
    """Open (and if new, create) the database."""
    db = sqlite3.connect(path)
    # Write-ahead log: someone reading while the nightly load replaces a year is not locked out,
    # and keeps seeing the year as it was until the load commits. In SQLite's default mode that
    # reader gets "database is locked". It needs a local disk: WAL does not work on synced or
    # network folders.
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(_SCHEMA)
    return db


class BadFile(ValueError):
    """The file cannot be read exactly as the Camara wrote it, so none of it is loaded.

    A year that is nine tenths right is worse than a year that is one day old: nobody can tell
    which tenth is wrong. The message names the line, so the row can be found in the file.
    """


def _whole(name, text):
    """A year, month or deputy id: the plain ten digits, no sign, no space, no leading zero.

    int() would also take " 12", "+12", "1_2", "007" and digits from other scripts. The real files
    use none of those (checked 2026-10-12), so one of them is a file to refuse, not to tidy up:
    "007" is not quietly deputy 7.
    """
    if not _WHOLE.fullmatch(text):
        raise ValueError(f"{name} is {text!r}")
    return int(text)


def _typed(line, row, year):
    try:
        if None in row or None in row.values():
            raise ValueError("wrong number of fields")
        if _whole("numAno", row["numAno"]) != year:
            raise ValueError(f"numAno is {row['numAno']} in the file of {year}")
        liquido = parse_centavos(row["vlrLiquido"])
        if liquido is None:
            raise ValueError("vlrLiquido is empty")
        month = _whole("numMes", row["numMes"])
        if not 1 <= month <= 12:
            raise ValueError(f"numMes is {month}")
        return (
            year,
            month,
            _whole("ideCadastro", row["ideCadastro"]) if row["ideCadastro"] else None,
            liquido,
            line,
            *(row[name] for name in COLUMNS),
        )
    except ValueError as error:
        raise BadFile(f"line {line}: {error}") from error


def _reader(raw):
    # strict: quoting that does not close is an error. The forgiving default reads on to the end
    # of the file and hands back every following receipt as the tail of one field, so a year
    # loads short and still looks whole. The 19 real files all parse strictly.
    return csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter=";", strict=True)


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
    with _year_archive(zip_path) as (archive, member, year):
        with archive.open(member) as raw:
            content = _sha256(raw)
        # "Already loaded" means this content AS THIS YEAR. The same bytes arriving under another
        # year's name are not skipped: they go on to be checked row by row, and refused.
        if db.execute("SELECT 1 FROM camara_cota_arquivo WHERE sha256 = ? AND ano = ?", (content, year)).fetchone():
            return year
        with archive.open(member) as raw:
            reader = _reader(raw)
            found = tuple(reader.fieldnames or ())
            if found != COLUMNS:
                # The Camara renaming or adding a column is exactly when a loader goes quietly wrong.
                missing, new = sorted(set(COLUMNS) - set(found)), sorted(set(found) - set(COLUMNS))
                raise BadFile(f"{member}: columns changed (missing {missing}, new {new}, or their order)")
            # One transaction: readers see the old year or the new year, never half of each.
            with db:
                db.execute("DELETE FROM camara_cota WHERE ano = ?", (year,))
                db.execute("INSERT OR REPLACE INTO camara_cota_arquivo VALUES (?, ?, ?)", (year, content, member))
                if not db.executemany(_INSERT, _rows(reader, year)).rowcount:
                    raise BadFile(f"{member}: no receipts")
            return year


@contextlib.contextmanager
def _year_archive(zip_path):
    """Open a yearly zip and yield (archive, the CSV inside, the year that CSV is named for).

    The year comes from the name the Camara gave the file, and every row must agree with it. Taking
    it from the rows instead let a file named for one year but holding another replace the wrong one.
    """
    name = pathlib.Path(zip_path).name
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as error:
        raise BadFile(f"{name}: not a zip file") from error
    with archive:
        members = archive.namelist()
        named = _YEAR_FILE.fullmatch(members[0]) if len(members) == 1 else None
        # The zip's own name counts too: Ano-2099.csv.zip holding a sound Ano-2098.csv would
        # replace 2098 and leave 2099 as it was, with nothing to say so.
        if not named or year_of(zip_path) != int(named[1]):
            raise BadFile(f"{name}: expected Ano-<year>.csv.zip holding exactly Ano-<year>.csv of the same year, found {members}")
        try:
            yield archive, members[0], int(named[1])
        except BadFile:
            raise
        except (UnicodeDecodeError, csv.Error, zlib.error, EOFError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
            # Damage that only shows while reading: bytes that are not UTF-8, quoting that does not
            # close, compressed data cut short, a member that is encrypted or packed in a way this
            # Python cannot unpack. Each has its own exception; all mean "refuse".
            raise BadFile(f"{name}: cannot be read ({error})") from error


def year_of(zip_path):
    """The year a yearly zip is named for, or None when it is not named Ano-<year>.csv.zip."""
    named = _YEAR_ZIP.fullmatch(pathlib.Path(zip_path).name)
    return int(named[1]) if named else None


def _rows(reader, year):
    lines = {}  # digest of a row -> the line it was first seen on
    end = reader.line_num  # the header
    for row in reader:
        # The line where the receipt STARTS. A few real receipts have a line break inside a field
        # (27 of them, counted 2026-10-12); line_num alone would point at where they end.
        line, end = end + 1, reader.line_num
        typed = _typed(line, row, year)
        # The 19 real files hold 5.2 million receipts and not one exact copy (counted 2026-10-12).
        # A copy therefore means the same reimbursement arrived twice, and adding it would pay it
        # into somebody's total twice. The lengths go in so that the same characters split
        # differently between two fields are not taken for a copy.
        written = typed[_TYPED:]
        digest = hashlib.blake2b((",".join(str(len(value)) for value in written) + "\x1f" + "".join(written)).encode(), digest_size=16).digest()
        if digest in lines:
            raise BadFile(f"line {line}: an exact copy of line {lines[digest]}")
        lines[digest] = line
        yield typed


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
    return _receipts(db, "c.ano = ? AND c.deputado = ?", (year, _a_deputy(deputy)))


def receipts_without_deputy(db, year):
    """Receipts of the year that belong to no deputy: the party leaderships."""
    return _receipts(db, "c.ano = ? AND c.deputado IS NULL", (year,))


@dataclasses.dataclass(frozen=True)
class FileTotals:
    ano: int
    receipts: int
    centavos: int
    por_deputado: dict        # deputy id, or None for the leaderships -> centavos paid
    sha256: str               # of the CSV inside: the hash load_cota records for a loaded year


def file_totals(zip_path):
    """What a yearly file says, worked out a second way.

    On purpose this shares nothing with load_cota beyond opening the CSV: decimal arithmetic
    straight from the text instead of parse_centavos, running sums instead of SQL. If the two
    ever disagree, one of them is wrong, and that is the reason to keep both.
    """
    count, total, by_deputy = 0, decimal.Decimal(0), collections.defaultdict(decimal.Decimal)
    with _year_archive(zip_path) as (archive, member, year):
        with archive.open(member) as raw:
            content = _sha256(raw)
        with archive.open(member) as raw:
            reader = _reader(raw)
            for row in reader:
                try:
                    amount = decimal.Decimal(row["vlrLiquido"])
                    if not amount.is_finite() or int(row["numAno"]) != year:
                        raise ValueError(f"vlrLiquido {row['vlrLiquido']!r}, numAno {row['numAno']!r} in the file of {year}")
                    deputy = int(row["ideCadastro"]) if row["ideCadastro"] else None
                except (KeyError, TypeError, ValueError, decimal.InvalidOperation) as error:
                    raise BadFile(f"{member} line {reader.line_num}: {error!r}") from error
                count += 1
                total += amount
                by_deputy[deputy] += amount
    if not count:
        raise BadFile(f"{member}: no receipts")

    def whole_centavos(amount):
        centavos = amount * 100
        if centavos != centavos.to_integral_value():
            raise BadFile(f"{member}: the amounts paid do not add up to whole centavos ({amount})")
        return int(centavos)

    return FileTotals(year, count, whole_centavos(total), {d: whole_centavos(a) for d, a in by_deputy.items()}, content)


def years(db):
    """The years the database holds a file for."""
    return [row[0] for row in db.execute("SELECT ano FROM camara_cota_arquivo ORDER BY ano")]


def loaded_sha256(db, year):
    """Hash of the file content the year was loaded from, or None when the year was never loaded."""
    row = db.execute("SELECT sha256 FROM camara_cota_arquivo WHERE ano = ?", (year,)).fetchone()
    return row[0] if row else None


_AS_WRITTEN = {name: COLUMNS.index(name) for name in ("numAno", "numMes", "ideCadastro", "vlrLiquido")}


def _csv_line(values):
    # The Camara's format: every field quoted, a quote inside a value doubled, LF at the end.
    return ('"' + '";"'.join(value.replace('"', '""') for value in values) + '"\n').encode("utf-8")


def stored_content(db, year):
    """(sha256, lines) of what the database holds for a year.

    The hash is of the year's CSV rebuilt from the stored columns in the Camara's own format. It
    equals the hash of the official file only if every receipt is there, in order and unchanged,
    which also proves the reading lost nothing: all 19 real files rebuild byte for byte (checked
    2026-10-12). The lines are receipts whose typed columns no longer agree with their own text.
    """
    digest = hashlib.sha256(b"\xef\xbb\xbf" + _csv_line(COLUMNS))
    disagree = []
    starts = 2  # the line the next receipt must start on; the header is line 1
    for row in db.execute("SELECT * FROM camara_cota WHERE ano = ? ORDER BY linha", (year,)):
        (*typed, line), written = row[:_TYPED], row[_TYPED:]
        as_in_file = _csv_line(written)
        digest.update(as_in_file)
        try:
            deputy = written[_AS_WRITTEN["ideCadastro"]]
            said = [
                _whole("numAno", written[_AS_WRITTEN["numAno"]]),
                _whole("numMes", written[_AS_WRITTEN["numMes"]]),
                _whole("ideCadastro", deputy) if deputy else None,
                parse_centavos(written[_AS_WRITTEN["vlrLiquido"]]),
            ]
        except ValueError:
            said = None
        if said != typed or line != starts:
            disagree.append(line)
        starts += as_in_file.count(b"\n")
    return digest.hexdigest(), disagree


def totals_by_deputy(db, year):
    """Centavos paid in the year for each deputy id; the key None holds the party leaderships."""
    return dict(db.execute("SELECT deputado, SUM(liquido_centavos) FROM camara_cota WHERE ano = ? GROUP BY deputado", (year,)))


def receipt_count(db, year):
    """How many receipts the year holds, leaderships included."""
    return db.execute("SELECT COUNT(*) FROM camara_cota WHERE ano = ?", (year,)).fetchone()[0]


def _a_deputy(deputy):
    # None is what a leadership receipt has for a deputy. It used to mean "the whole Camara" in
    # total_liquido and "nobody" in receipts, so a page asking for "this receipt's deputy's total"
    # would have printed the Camara's quarter of a billion beside a leadership's name.
    if deputy is None:
        raise ValueError("no deputy given: use total_camara for the whole Camara, receipts_without_deputy for the leaderships")
    return deputy


def total_liquido(db, year, deputy):
    """Centavos the quota paid for one deputy in a year."""
    query = "SELECT COALESCE(SUM(liquido_centavos), 0) FROM camara_cota WHERE ano = ? AND deputado = ?"
    return db.execute(query, (year, _a_deputy(deputy))).fetchone()[0]


def total_camara(db, year):
    """Centavos the quota paid in a year for the whole Camara: every deputy and the party leaderships."""
    return db.execute("SELECT COALESCE(SUM(liquido_centavos), 0) FROM camara_cota WHERE ano = ?", (year,)).fetchone()[0]
