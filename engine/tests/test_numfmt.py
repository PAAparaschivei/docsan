"""Chart values formatted as the chart shows them, or left as stored when the format is not understood."""

from __future__ import annotations

import pytest

from docsan.docx.numfmt import format_value

ACCOUNTING = '_-* #,##0.00\\ "€"_-;\\-* #,##0.00\\ "€"_-;_-* "-"??\\ "€"_-;_-@_-'


@pytest.mark.parametrize(
    "raw,code,shown",
    [
        ("0.25", "0%", "25%"),
        ("0.256", "0.0%", "25.6%"),
        ("-0.2", "0%", "-20%"),
        ("1234567.891", "#,##0", "1,234,568"),
        ("1234567.891", "#,##0.00", "1,234,567.89"),
        ("1500000", '#,##0.0,,"M"', "1.5M"),
        ("12", "General", "12"),
        ("12.5", "", "12.5"),
        ("0.1", "0.00", "0.10"),
        ("3", "00", "03"),
        ("1234.5", "#,##0.##", "1,234.5"),
        ("-3.5", "#,##0.0;(#,##0.0)", "(3.5)"),
        ("7", "[Red]0", "7"),
        ("12345", "[$€-x-euro2] #,##0", "€ 12,345"),
        ("12345", '"€"#,##0', "€12,345"),
        ("1234.5", ACCOUNTING, " 1,234.50 € "),
        ("0.000123", "0.00E+00", "1.23E-04"),
        ("5", '0" days"', "5 days"),
        ("45292", "mmm yy", "Jan 24"),
        ("45292", "dd/mm/yyyy", "01/01/2024"),
        ("0.75", "h:mm AM/PM", "6:00 PM"),
        ("abc", "0%", "abc"),
    ],
)
def test_values_are_shown_as_formatted(raw, code, shown):
    assert format_value(raw, code) == shown


def test_unknown_formats_are_reported_not_guessed():
    assert format_value("2", "# ?/?") is None
