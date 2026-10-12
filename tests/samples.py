"""Small yearly quota files for tests, in the Camara's own shape.

The header is copied from the real Ano-2026.csv (2026-10-12): 32 columns, semicolons, every
field quoted. Tests name only the columns they care about; the rest get harmless defaults.
"""
import csv
import io
import zipfile

HEADER = [
    "txNomeParlamentar", "cpf", "ideCadastro", "nuCarteiraParlamentar", "nuLegislatura", "sgUF", "sgPartido",
    "codLegislatura", "numSubCota", "txtDescricao", "numEspecificacaoSubCota", "txtDescricaoEspecificacao",
    "txtFornecedor", "txtCNPJCPF", "txtNumero", "indTipoDocumento", "datEmissao", "vlrDocumento", "vlrGlosa",
    "vlrLiquido", "numMes", "numAno", "numParcela", "txtPassageiro", "txtTrecho", "numLote", "numRessarcimento",
    "datPagamentoRestituicao", "vlrRestituicao", "nuDeputadoId", "ideDocumento", "urlDocumento",
]


def receipt(deputy, liquido, **columns):
    """One row. deputy=None is a party leadership: the Camara leaves ideCadastro empty for those."""
    row = {
        "txNomeParlamentar": f"DEPUTADO {deputy}" if deputy else "LID.GOV-CD",
        "ideCadastro": str(deputy) if deputy else "",
        "sgUF": "SP" if deputy else "NA",
        "sgPartido": "ABC" if deputy else "",
        "txtDescricao": "COMBUSTÍVEIS E LUBRIFICANTES.",
        "txtFornecedor": "POSTO EXEMPLO LTDA",
        "txtCNPJCPF": "000.000.000/0001-00",
        "vlrDocumento": liquido,
        "vlrGlosa": "0",
        "vlrLiquido": liquido,
        "numMes": "1",
    }
    row.update(columns)
    return row


def year_file(folder, year, receipts, header=HEADER, zip_year=None, member_year=None):
    """Write Ano-<year>.csv.zip into folder (created if missing) and return its path.

    Two versions of the same year go in two folders, as two days' downloads would. zip_year and
    member_year exist only to build files whose names lie about what is inside.
    """
    text = io.StringIO(newline="")
    # The real files: every field quoted, LF line ends, a final newline (measured 2026-10-12).
    writer = csv.writer(text, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(header)
    for row in receipts:
        row = {"numAno": str(year), **row}
        writer.writerow([row.get(name, "") for name in HEADER])
    return raw_year_file(folder, year, text.getvalue(), zip_year=zip_year, member_year=member_year)


def raw_year_file(folder, year, text, zip_year=None, member_year=None):
    """The same, from the CSV text itself, for files no writer would produce."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Ano-{zip_year or year}.csv.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        # The real files start with a UTF-8 byte order mark.
        archive.writestr(f"Ano-{member_year or year}.csv", ("﻿" + text).encode("utf-8") if isinstance(text, str) else text)
    return path


def header_line():
    return ";".join(f'"{name}"' for name in HEADER) + "\n"


def row_line(year, row):
    """One receipt as the Camara writes it: every field quoted, a quote inside a value doubled."""
    row = {"numAno": str(year), **row}
    return ";".join('"' + row.get(name, "").replace('"', '""') + '"' for name in HEADER) + "\n"
