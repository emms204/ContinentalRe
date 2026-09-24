"""Minimal unit tests: header detect, negative preserve, monthly merge order."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cre_cleaner.detect import find_header_row, detect_sheet_type
from cre_cleaner.normalize import parse_number, as_text_id, parse_period
from cre_cleaner.config import QUARTER_MONTHS


def test_find_header_row_premium():
    rows = [
        [None, None, "OWN RETENTION", None, None, "1SURP"],
        [None, "INSURED", "POLICY NO", "PERIOD OF INSURANCE", "SUM INSURED", "PREMIUM", "PPN", "SUM INSURED", "Premium"],
        [None, "ACME", "P/1", "01/01/2025 - 31/12/2025", 1000, 50, 10, 100, 5],
    ]
    assert find_header_row(rows, "premium") == 1


def test_detect_paid_vs_outstanding():
    assert detect_sheet_type("MISC. ACC PAID JANUARY", []) == "paid"
    assert detect_sheet_type("MARINE HULL OUTSTANDING CLAIM", []) == "outstanding"
    assert detect_sheet_type("FIRE 2ND", [[None, "INSURED", "POLICY NO", "PREMIUM"]]) == "premium"


def test_preserve_negatives():
    assert parse_number(-10846229.6) == -10846229.6
    assert parse_number("(1,234.50)") == -1234.50
    assert parse_number("NIL") is None


def test_text_ids():
    assert as_text_id("503/00038/19/TQ/HO") == "503/00038/19/TQ/HO"
    assert as_text_id(2025.0) == "2025"


def test_period_split():
    a, b = parse_period("05/10/2024 - 04/04/2025")
    assert a is not None and b is not None
    assert a.day == 5 and a.month == 10 and a.year == 2024
    assert b.day == 4 and b.month == 4 and b.year == 2025



def test_class_label_mapping():
    from cre_cleaner.class_labels import normalize_class_label, class_sheet_title
    assert normalize_class_label("FIRE") == "Fire"
    assert normalize_class_label("GOODS IN TRANSIT") == "General Accident"
    assert normalize_class_label("ALL RISKS") == "General Accident"
    assert normalize_class_label("MARINE HULL") == "Marine Hull"
    assert class_sheet_title("Fire", "CLAIMS") == "Fire - CLAIMS"


def test_quarter_months_order():
    assert QUARTER_MONTHS[1] == [1, 2, 3]


if __name__ == "__main__":
    test_find_header_row_premium()
    test_detect_paid_vs_outstanding()
    test_preserve_negatives()
    test_text_ids()
    test_period_split()
    test_quarter_months_order()
    test_class_label_mapping()
    print("ALL UNIT TESTS PASSED")
