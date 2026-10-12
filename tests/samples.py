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


def year_file(folder, year, receipts, name=None, header=HEADER):
    """Write Ano-<year>.csv.zip into folder and return its path."""
    text = io.StringIO(newline="")
    writer = csv.writer(text, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(header)
    for row in receipts:
        row = {"numAno": str(year), **row}
        writer.writerow([row.get(name, "") for name in HEADER])
    path = folder / (name or f"Ano-{year}.csv.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        # The real files start with a UTF-8 byte order mark.
        archive.writestr(f"Ano-{year}.csv", "﻿" + text.getvalue())
    return path
