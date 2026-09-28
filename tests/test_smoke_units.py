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


def test_parse_period_single_date_not_copied_to_both():
    from datetime import datetime
    a, b = parse_period(datetime(2024, 6, 15))
    assert a is not None and a.day == 15 and a.month == 6
    assert b is None
    a, b = parse_period("15/06/2024")
    assert a is not None and b is None


def test_unlabelled_allocation_first_retention_second_treaty():
    from cre_cleaner.map_columns import detect_premium_allocation_blocks
    # No group labels; two PPN/SI/Prem triples → first RETENTION, second TREATY
    header = [
        "INSURED", "POLICY",
        "PPN", "SUM INSURED", "PREMIUM",
        "PPN", "SUM INSURED", "PREMIUM",
    ]
    cm = detect_premium_allocation_blocks(header, group_row=None)
    assert cm.ret_ppn == 2 and cm.sur_ppn == 5
    assert cm.ret_si == 3 and cm.sur_si == 6
    assert cm.ret_prem == 4 and cm.sur_prem == 7
    assert cm.sur_label == "TREATY"


def test_single_unlabelled_block_is_treaty_not_retention():
    from cre_cleaner.map_columns import detect_premium_allocation_blocks
    header = ["INSURED", "PPN", "SUM INSURED", "PREMIUM"]
    cm = detect_premium_allocation_blocks(header, group_row=None)
    assert cm.ret_ppn is None and cm.ret_si is None and cm.ret_prem is None
    assert cm.sur_ppn == 1 and cm.sur_si == 2 and cm.sur_prem == 3
    assert cm.sur_label == "TREATY"


def test_duplicate_claims_differ_by_period_not_flagged():
    from datetime import datetime
    from cre_cleaner.models import ClaimsRow
    from cre_cleaner.reconcile import flag_duplicate_claims
    rows = [
        ClaimsRow(claim_no="C1", policy_no="P1", date_of_loss=datetime(2024, 1, 1),
                  period_from=datetime(2024, 1, 1), period_to=datetime(2024, 3, 31),
                  total_claims=100),
        ClaimsRow(claim_no="C1", policy_no="P1", date_of_loss=datetime(2024, 1, 1),
                  period_from=datetime(2024, 4, 1), period_to=datetime(2024, 6, 30),
                  total_claims=100),
    ]
    assert flag_duplicate_claims(rows, "CLAIMS") == []
    same = [
        rows[0],
        ClaimsRow(claim_no="C1", policy_no="P1", date_of_loss=datetime(2024, 1, 1),
                  period_from=datetime(2024, 1, 1), period_to=datetime(2024, 3, 31),
                  total_claims=100),
    ]
    assert len(flag_duplicate_claims(same, "CLAIMS")) == 1


def test_blank_from_to_flagged():
    from cre_cleaner.models import ClaimsRow, AuditMeta
    from cre_cleaner.reconcile import check_row_dates
    row = ClaimsRow(
        claim_no="C1", policy_no="P1", insured="A",
        date_of_loss=__import__("datetime").datetime(2024, 2, 1),
        period_from=None, period_to=None, total_claims=10,
        audit=AuditMeta(source_filename="x.xlsx", source_sheet="S", source_row=2),
    )
    exc, _counts = check_row_dates([], [row], [], year=2024, quarter=1)
    reasons = {e.reason for e in exc}
    assert "period_from_missing" in reasons
    assert "period_to_missing" in reasons


def test_discovery_qtr1_bord_jan_and_monthly_claims():
    import tempfile
    from cre_cleaner.adapters.base import quarters_in_text
    from cre_cleaner.adapters.aiico_ark import AiicoArkAdapter
    assert 1 in quarters_in_text("QTR1")
    assert 1 in quarters_in_text("QTR1 RETURNS.xlsx")
    ad = AiicoArkAdapter()
    month, why = ad.classify_premium_candidate("BORD JAN 2024.xlsx", 2024)
    assert month == 1 and why == "ok"
    month, why = ad.classify_premium_candidate("QTR1.xlsx", 2024)
    assert month is None and "quarterly" in why
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "QTR1.xlsx").write_bytes(b"PK")  # name-only discovery
        (root / "BORD JAN 2024.xlsx").write_bytes(b"PK")
        (root / "JANUARY CLAIMS 2024.xls").write_bytes(b"PK")
        q = ad.discover_quarterly_premium_files(root, 2024, 1)
        assert any(p.name == "QTR1.xlsx" for p in q)
        months = ad.discover_premium_files(root, 2024, 1)
        assert any(m == 1 and p.name.startswith("BORD") for m, p in months)
        claims = ad.discover_claims_files(root, 2024, 1)
        assert any("JANUARY CLAIMS" in p.name for p in claims)


def test_api_safe_filename_and_labels():
    from fastapi import HTTPException
    from cre_cleaner.api import safe_upload_basename, sanitize_label
    assert safe_upload_basename("../../x.xlsx") == "x.xlsx"
    assert safe_upload_basename("foo/bar/BORD JAN.xlsx") == "BORD_JAN.xlsx"
    try:
        safe_upload_basename("../../etc/passwd")
        assert False, "expected reject"
    except HTTPException as e:
        assert e.status_code == 400
    assert sanitize_label("aiico", "cedant") == "AIICO"
    try:
        sanitize_label("../evil", "cedant")
        assert False, "expected reject"
    except HTTPException as e:
        assert e.status_code == 400
    try:
        sanitize_label("a/b", "broker")
        assert False, "expected reject"
    except HTTPException as e:
        assert e.status_code == 400


def test_quarter_months_order():
    assert QUARTER_MONTHS[1] == [1, 2, 3]


def test_class_label_mapping():
    from cre_cleaner.class_labels import normalize_class_label, class_sheet_title
    assert normalize_class_label("FIRE") == "Fire"
    assert normalize_class_label("GOODS IN TRANSIT") == "General Accident"
    assert normalize_class_label("ALL RISKS") == "General Accident"
    assert normalize_class_label("MARINE HULL") == "Marine Hull"
    assert class_sheet_title("Fire", "CLAIMS") == "Fire - CLAIMS"


def test_banner_class_and_typos():
    from cre_cleaner.class_labels import banner_class_label, normalize_class_label, is_unresolved_class
    assert banner_class_label("FIRE PAID CLAIM") == "Fire"
    assert banner_class_label("ENGINERRING") == "Engineering"
    assert banner_class_label("ENGINEERING PAID CLAIM") == "Engineering"
    # Bare MARINE is too broad for Hull vs Cargo — banner still matches, rows go to exceptions.
    assert banner_class_label("MARINE PAID CLAIM") == "MARINE"
    assert is_unresolved_class("MARINE")
    assert banner_class_label("MARINE CARGO") == "Marine Cargo"
    assert banner_class_label("MOTOR") == "Motor"
    assert banner_class_label("2ND SURP PAID CLAIM") == ""
    assert banner_class_label("558047.14") == ""
    assert normalize_class_label("GEN ACCIENT") == "General Accident"
    assert normalize_class_label("GEN. ACCIDENT") == "General Accident"
    assert normalize_class_label("GEN ACC") == "General Accident"
    assert normalize_class_label("MOTOR") == "Motor"
    assert normalize_class_label("CASUALTY") == "Casualty"  # not mapped (pending Bisola)


def test_ppn_share_bisola_style():
    from cre_cleaner.quarterly import _ppn_share
    # UBA OST gold: 7644.71 / 32490 ≈ 0.235294...
    assert abs(_ppn_share(7644.71, 32490) - 0.2352942444) < 1e-9
    assert abs(_ppn_share(24845.29, 32490) - 0.7647057556) < 1e-9
    assert _ppn_share(None, 32490) == 0.0
    assert _ppn_share(100, 0) is None
    assert _ppn_share(100, None) is None


def test_currency_and_split_helpers():
    from cre_cleaner.normalize import currency_codes, currency_code
    from cre_cleaner.reconcile import check_row_splits
    from cre_cleaner.models import PremiumRow, AuditMeta
    assert currency_codes("Q2 2025 DOLLAR BORDEREAU") == {"USD"}
    assert currency_code("Naira") == "NGN"
    row = PremiumRow(
        policy_no="P1", name_of_insured="A",
        gross_premium=100, ret_prem=40, sur_prem=60, fac_prem=0,
        ret_ppn=40, sur_ppn=60, fac_ppn=0,
        audit=AuditMeta(currency="NGN"),
    )
    exc, counts = check_row_splits([row], [], [])
    assert counts["premium"]["ok"] == 1
    assert not exc


def test_adapter_registry_no_fallback():
    from cre_cleaner.adapters import get_adapter, list_adapters
    from cre_cleaner.adapters.base import UnsupportedCedantError
    assert get_adapter("AIICO", "ARK").verified is True
    assert get_adapter("CUSTODIAN", "SCIB").verified is False
    assert len(list_adapters()) >= 6
    try:
        get_adapter("UNKNOWN", "X")
        assert False, "expected UnsupportedCedantError"
    except UnsupportedCedantError:
        pass


def test_claims_leading_blank_default():
    from openpyxl import Workbook
    from cre_cleaner.io_excel import claims_col_map, write_claims_rows
    from cre_cleaner.models import ClaimsRow
    assert min(claims_col_map().values()) == 2
    assert min(claims_col_map(False).values()) == 1
    ws = Workbook().active
    write_claims_rows(ws, [ClaimsRow(insured="X", policy_no="P1", claim_no="C1", total_claims=1.0)],
                      title="T")
    assert ws["B1"].value == "T"
    assert all(c.value is None for c in ws["A"])
    assert ws.max_column == 19


if __name__ == "__main__":
    test_find_header_row_premium()
    test_detect_paid_vs_outstanding()
    test_preserve_negatives()
    test_text_ids()
    test_period_split()
    test_parse_period_single_date_not_copied_to_both()
    test_unlabelled_allocation_first_retention_second_treaty()
    test_single_unlabelled_block_is_treaty_not_retention()
    test_duplicate_claims_differ_by_period_not_flagged()
    test_blank_from_to_flagged()
    test_discovery_qtr1_bord_jan_and_monthly_claims()
    test_api_safe_filename_and_labels()
    test_quarter_months_order()
    test_class_label_mapping()
    test_banner_class_and_typos()
    test_ppn_share_bisola_style()
    test_currency_and_split_helpers()
    test_adapter_registry_no_fallback()
    test_claims_leading_blank_default()
    print("ALL UNIT TESTS PASSED")
