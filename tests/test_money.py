import unittest

from tres_poderes.money import format_brl, parse_centavos


class MoneyTextBecomesExactCentavos(unittest.TestCase):
    def test_values_as_the_camara_writes_them(self):
        self.assertEqual(parse_centavos("1467"), 146700)
        self.assertEqual(parse_centavos("3668.93"), 366893)
        self.assertEqual(parse_centavos("124.5"), 12450)
        self.assertEqual(parse_centavos("0.01"), 1)

    def test_a_refund_is_negative(self):
        self.assertEqual(parse_centavos("-298.07"), -29807)

    def test_an_empty_cell_is_no_value_not_zero(self):
        self.assertIsNone(parse_centavos(""))


class WhatCannotBeExactIsRefused(unittest.TestCase):
    def test_fractions_of_a_centavo_are_never_rounded(self):
        # Real values from the Camara's vlrDocumento column: 5,487 rows look like these.
        for text in ("18.235", "0.180092", "15.2939"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_centavos(text)

    def test_digits_other_than_the_plain_ten_are_refused(self):
        for text in ("１２.３４", "١٢.٣٤"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_centavos(text)

    def test_an_amount_too_long_to_be_money_is_refused(self):
        # Thirteen digits of reais is trillions, far beyond any receipt; twenty would not fit the database.
        self.assertEqual(parse_centavos("9999999999999.99"), 999999999999999)
        with self.assertRaises(ValueError):
            parse_centavos("99999999999999999999")

    def test_anything_that_is_not_a_plain_amount_is_refused(self):
        for text in ("1,5", "R$ 10", "abc", "1e3", " 12", "12 ", "--5", "5.", ".5", "+5"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_centavos(text)


class CentavosAreShownTheBrazilianWay(unittest.TestCase):
    def test_reais_with_thousands_dots_and_a_decimal_comma(self):
        self.assertEqual(format_brl(146700), "R$ 1.467,00")
        self.assertEqual(format_brl(24936621147), "R$ 249.366.211,47")
        self.assertEqual(format_brl(99999), "R$ 999,99")
        self.assertEqual(format_brl(100000), "R$ 1.000,00")

    def test_amounts_under_one_real(self):
        self.assertEqual(format_brl(0), "R$ 0,00")
        self.assertEqual(format_brl(1), "R$ 0,01")
        self.assertEqual(format_brl(10), "R$ 0,10")

    def test_a_refund_keeps_its_minus_sign(self):
        self.assertEqual(format_brl(-29807), "-R$ 298,07")
        self.assertEqual(format_brl(-5), "-R$ 0,05")


if __name__ == "__main__":
    unittest.main()
