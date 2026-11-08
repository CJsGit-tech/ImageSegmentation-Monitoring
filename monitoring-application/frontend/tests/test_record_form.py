import pytest

from core import record_form


@pytest.mark.parametrize(
    ("text", "expected"),
    [("5", 5.0), (" 5.5 ", 5.5), ("-2", -2.0), ("+.5", 0.5), ("5.", 5.0), ("1e3", 1000.0)],
)
def test_parse_value_reads_a_number(text, expected):
    assert record_form.parse_value(text) == expected


@pytest.mark.parametrize(
    "text",
    [None, "", "   ", "abc", "1_000", "１２", "nan", "inf", "-inf", "1e999", "1,000", "1 2"],
)
def test_parse_value_rejects_anything_else(text):
    assert record_form.parse_value(text) is None


@pytest.mark.parametrize("text", [None, "", "   "])
def test_value_error_for_a_blank_field(text):
    assert record_form.value_error(text) == record_form.VALUE_REQUIRED


def test_value_error_for_a_non_number():
    assert record_form.value_error("abc") == record_form.VALUE_NOT_NUMBER


def test_value_error_accepts_zero():
    assert record_form.value_error("0") is None


def test_range_text_with_range_and_unit():
    assert record_form.range_text({"min": 1.0, "max": 5.5}, "°C") == "範圍 1 – 5.5 °C（邊界含在異常內）"


def test_range_text_with_range_only():
    assert record_form.range_text({"min": 1.0, "max": 5.5}, None) == "範圍 1 – 5.5（邊界含在異常內）"


def test_range_text_with_unit_only():
    assert record_form.range_text(None, "°C") == "單位：°C"


def test_range_text_with_neither():
    assert record_form.range_text(None, None) is None
