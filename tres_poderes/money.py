"""Money as whole centavos.

Every amount on the site is an integer number of centavos from the moment it is read. Floating
point cannot hold 0.10 exactly, and over five million receipts that turns into totals that
disagree with the Camara's own by a few centavos: small, and enough to be called wrong.
"""

import re

# An allow-list, not a clean-up: digits, at most two decimals, optional minus. Whatever does not
# fit is refused, never rounded, because "18.235" read loosely becomes R$ 20,35 without a word.
# The Camara's vlrDocumento column has 5,487 such values (counted 2026-10-12); vlrLiquido has none.
_PLAIN_AMOUNT = re.compile(r"-?\d+(\.\d{1,2})?")


def parse_centavos(text):
    """'3668.93' -> 366893. An empty cell is None: no value was given, which is not zero."""
    if text == "":
        return None
    if not _PLAIN_AMOUNT.fullmatch(text):
        raise ValueError(f"not an exact amount in reais and centavos: {text!r}")
    sign = -1 if text.startswith("-") else 1
    reais, _, fraction = text.lstrip("-").partition(".")
    return sign * (int(reais) * 100 + int(fraction.ljust(2, "0")))


def format_brl(centavos):
    """146700 -> 'R$ 1.467,00'. Whole-number arithmetic only, so what is shown is what is stored."""
    reais, rest = divmod(abs(centavos), 100)
    return f"{'-' if centavos < 0 else ''}R$ {reais:,}".replace(",", ".") + f",{rest:02d}"
