"""IMPL-20260929-04: per-cedant separation — adapter-owned rules, tie logging,
AIICO-only calculated claims PPN (flagged), Unitrust Agric bands and TSI/GP
basis setting, per-period adapter settings (UW start-date fallback OFF by
default), FAC never a class."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cre_cleaner.adapters import get_adapter
from cre_cleaner.core.map_columns import (
    PREMIUM_ALIASES, detect_premium_allocation_blocks, map_simple_columns,
)
from cre_cleaner.core.quarterly import _parse_claims_sheet, parse_premium_file

UNITRUST_AGRIC_HEADER = [
    "Policy No ", "Insured ", "Policy Class ", "Risktype ", "Branch ", "Debit Note ",
    "Company's Share ", "MPL % ", "Insurance Period ", "Sum Insured ", "Our share SI ",
    "Gross Premium ", None, "Our Share of G. Prem. ", "NET RETENTION ", None, None,
    "TREATY QUOTA SHARE", None, None,
]


def test_exact_ties_by_alias_order_then_position_and_logged():
    # alias-list order: 'EXPIRY DATE' is listed before 'TO' -> wins although to its left/right
    cm = map_simple_columns(["INSURED", "TO", "EXPIRY DATE"], PREMIUM_ALIASES)
    assert cm.get("period_to") == 2
    assert any("period_to" in t and "alias-list order" in t for t in cm.tie_log), cm.tie_log
    # same alias twice: leftmost column wins, logged as column position
    cm = map_simple_columns(["INSURED", "SUM INSURED", "SUM INSURED"], PREMIUM_ALIASES)
    assert cm.get("sum_insured") == 1
    assert any("sum_insured" in t and "column position" in t for t in cm.tie_log), cm.tie_log


def test_exact_match_does_not_score_by_length():
    # Two exact matches: the alias listed first wins even if shorter.
    tbl = {"f": ["SI", "TOTAL SUM INSURED"]}
    cm = map_simple_columns(["TOTAL SUM INSURED", "SI"], tbl)
    assert cm.get("f") == 1


def test_shared_premium_aliases_have_no_our_share_names():
    names = [a for v in PREMIUM_ALIASES.values() for a in v]
    assert not [a for a in names if "OUR SHARE" in a.upper()]


def test_unitrust_agric_mapping_always_our_share():
    """Bisola 13:37 WAT: Unitrust TSI/GP ALWAYS our-share K / N (no switch);
    RET O/P/Q, TREATY R/S/T."""
    from cre_cleaner.adapters import unitrust
    assert not hasattr(unitrust, "UNITRUST_TSI_GP_BASIS")
    ad = get_adapter("UNITRUST", "AGRIC")
    exc = []
    cm = ad.map_premium_columns(UNITRUST_AGRIC_HEADER, None, path=Path("x.xlsx"), sheet="S",
                                exceptions=exc)
    assert cm.get("sum_insured") == 10 and cm.get("gross_premium") == 13  # cols K / N
    assert (cm.ret_ppn, cm.ret_si, cm.ret_prem) == (14, 15, 16)  # O / P / Q
    assert (cm.sur_ppn, cm.sur_si, cm.sur_prem) == (17, 18, 19)  # R / S / T
    logged = [e for e in exc if e.reason == "tsi_gp_basis"]
    assert logged and "col 11" in logged[0].detail and "col 14" in logged[0].detail
    # the generic core alone never treats NET RETENTION as a share-label band
    generic = detect_premium_allocation_blocks(UNITRUST_AGRIC_HEADER, None)
    assert (generic.ret_ppn, generic.ret_si, generic.ret_prem) != (14, 15, 16)


def test_unitrust_agric_parse_copies_source_figures(tmp_path):
    import openpyxl
    from datetime import datetime
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "AGRIC  NAIRA"
    ws.append(UNITRUST_AGRIC_HEADER)
    ws.append(["P/1", "FARM A", "AGRIC", "CROP", "HQ", "DN1", 100, 100,
               "2024-04-16 To 2025-04-15", 1000000.0, 250000.0, 10000.0, None, 2500.0,
               30, 75000.0, 750.0, 70, 175000.0, 1750.0])
    p = tmp_path / "2ND QTR 2024 AGRIC TREATY.xlsx"
    wb.save(p)
    rows, exc, _aud = parse_premium_file(p, "", adapter=get_adapter("UNITRUST", "AGRIC"))
    assert len(rows) == 1
    r = rows[0]
    assert (r.total_sum_insured, r.gross_premium) == (250000.0, 2500.0)  # our share K / N
    assert (r.ret_ppn, r.ret_si, r.ret_prem) == (30, 75000.0, 750.0)
    assert (r.sur_ppn, r.sur_si, r.sur_prem) == (70, 175000.0, 1750.0)
    assert not [e for e in exc if e.reason == "allocation_not_matching_base"]
    # no UW column -> UW left blank (start-date fallback OFF by default)
    assert r.underwriting_year in (None, "")
    assert not [e for e in exc if e.reason == "uw_year_from_start_date"]
    assert "settings: uw_year_from_start_date=False (adapter), tsi_gp_basis=our_share (adapter)" in _aud[0].notes


def test_uw_year_fallback_off_by_default_for_every_adapter():
    from cre_cleaner.adapters import ADAPTERS
    for key, cls in ADAPTERS.items():
        ad = cls()
        for y, q in ((None, None), (2023, 1), (2024, 2), (2025, 4)):
            ad.set_period(y, q)
            assert ad.setting("uw_year_from_start_date") is False, key


def test_settings_overridable_per_year_and_quarter(monkeypatch):
    ad = get_adapter("UNITRUST", "AGRIC")
    monkeypatch.setattr(type(ad), "settings_by_period", {
        (2024, None): {"uw_year_from_start_date": True},
        (2024, 2): {"tsi_gp_basis": "100"},
    })
    ad.set_period(2024, 1)
    assert ad.setting_source("tsi_gp_basis") == ("our_share", "adapter")
    assert ad.setting_source("uw_year_from_start_date") == (True, "override 2024")
    ad.set_period(2024, 2)
    assert ad.setting_source("tsi_gp_basis") == ("100", "override 2024 Q2")
    cm = ad.map_premium_columns(UNITRUST_AGRIC_HEADER, None)
    assert cm.get("sum_insured") == 9 and cm.get("gross_premium") == 11  # 100%: J / L
    assert "tsi_gp_basis=100 (override 2024 Q2)" in ad.settings_note()
    ad.set_period(2025, 2)
    assert ad.setting_source("uw_year_from_start_date") == (False, "adapter")
    monkeypatch.setattr(type(ad), "settings_by_period", {(2024, 3): {"tsi_gp_basis": "bogus"}})
    ad.set_period(2024, 3)
    with pytest.raises(ValueError):
        ad.setting("tsi_gp_basis")


def test_uw_start_date_fallback_when_setting_on(monkeypatch):
    from datetime import datetime
    ad = get_adapter("UNITRUST", "AGRIC")
    monkeypatch.setattr(type(ad), "settings_by_period", {(2024, 2): {"uw_year_from_start_date": True}})
    ad.set_period(2024, 2)
    hdr = ["Policy No. ", "Insured ", "Start Date ", "End Date ", "Total Claims Paid ", "Claim No. "]
    row = ["P1", "X", datetime(2023, 3, 7), datetime(2024, 3, 6), 100.0, "C1"]
    rows, exc, _ = _parse_claims_sheet(Path("x PAID.xlsx"), "AGRIC PAID", [hdr, row], "paid", adapter=ad)
    assert rows[0].uw_yr == 2023
    assert [e for e in exc if e.reason == "uw_year_from_start_date"]


def _claims_tab():
    return [
        ["S/N", "NAME OF INSURED", "POLICY NO", "CLAIM NO", "DATE OF LOSS", "TOTAL CLAIMS PAID",
         "NET RETENTION", "TREATY RECOVERY"],
        [1, "ACME", "P/1", "CL/1", "01/02/2024", 1000.0, 300.0, 700.0],
    ]


def test_claims_ppn_calculated_only_for_aiico_and_flagged():
    rows, exc, audit = _parse_claims_sheet(
        Path("1ST QTR 2024 CLAIMS.xlsx"), "FIRE PAID CLAIMS", _claims_tab(), "paid",
        adapter=get_adapter("AIICO", "ARK"),
    )
    assert len(rows) == 1 and abs(rows[0].ppn_ret - 0.3) < 1e-12 and abs(rows[0].ppn_treaty - 0.7) < 1e-12
    assert "PPN RET/TREATY/FAC Calculated" in audit.notes and "r2" in audit.notes
    # Any other adapter copies the source only: no PPN columns -> blank, no flag.
    rows2, exc2, audit2 = _parse_claims_sheet(
        Path("1ST QTR 2024 CLAIMS.xlsx"), "FIRE PAID CLAIMS", _claims_tab(), "paid",
        adapter=get_adapter("UNITRUST", "AGRIC"),
    )
    assert rows2[0].ppn_ret is None and rows2[0].ppn_treaty is None
    assert rows2[0].amount_ret == 300.0 and rows2[0].amount_treaty == 700.0
    assert "Calculated" not in audit2.notes


def test_aiico_claims_net_liability_alias_is_adapter_owned():
    from cre_cleaner.core.map_columns import CLAIMS_ALIASES
    hdr = ["NAME OF INSURED", "CLAIM NO", "TOTAL CLAIMS PAID", "AIICO'S NET LIABILITY"]
    ark = get_adapter("AIICO", "ARK")
    assert ark.map_claims_columns(hdr, ark.claims_aliases()).get("amount_ret") == 3
    assert "AIICO S NET LIABILITY" in ark.claims_aliases()["amount_ret"]
    assert "AIICO S NET LIABILITY" not in CLAIMS_ALIASES["amount_ret"]
    assert "AIICO S NET LIABILITY" not in get_adapter("HEIRS", "DIRECT").claims_aliases()["amount_ret"]


def test_uw_details_swap_is_aiico_only():
    assert get_adapter("AIICO", "ARK").fix_claims_uw_details("FIRE AT WAREHOUSE", "2024") == (
        2024, "FIRE AT WAREHOUSE")
    assert get_adapter("LASACO", "FEYBIL").fix_claims_uw_details("FIRE AT WAREHOUSE", "2024") is None


def test_unitrust_claims_bands_copy_source_ppn_and_amount():
    """Unitrust claims: band label col = PPN (copied), next col = amount;
    TREATY = TREATY QUOTA SHARE; first surplus kept as extra layer; UW from start date."""
    from datetime import datetime
    from pathlib import Path
    from cre_cleaner.adapters import get_adapter
    from cre_cleaner.core.quarterly import _parse_claims_sheet
    hdr = ["Policy No. ", "Insured ", "Start Date ", "End Date ", "Date Of Loss ",
           "Total Claims Paid ", "Claim No. ", "Net Retention", None,
           "TREATY FIRST SURPLUS ", None, "TREATY QUOTA SHARE", None, "Facultative", None]
    row = ["P1", "RIPARIAN FARMING LIMITED ", "March 07, 2023 ", "March 06, 2024 ",
           datetime(2024, 2, 6), 180000, "C1", 30, 54000, 0, 5, 70, 126000, 0, 0]
    ad = get_adapter("UNITRUST", "AGRIC")
    rows, exc, _ = _parse_claims_sheet(Path("x PAID.xlsx"), "AGRIC PAID", [hdr, row], "paid", adapter=ad)
    r = rows[0]
    assert (r.ppn_ret, r.amount_ret, r.ppn_treaty, r.amount_treaty) == (30, 54000, 70, 126000)
    assert r.extra_layers == [{"layer": "TREATY FIRST SURPLUS", "amount": 5}]
    assert r.period_from == datetime(2023, 3, 7)
    assert r.uw_yr in (None, "")  # no UW column; fallback OFF by default
    assert not any(e.reason == "uw_year_from_start_date" for e in exc)


def test_unitrust_allocation_check_flags_only_never_changes_value():
    from cre_cleaner.models import PremiumRow
    ad = get_adapter("UNITRUST", "AGRIC")
    base = dict(policy_no="P", name_of_insured="X", total_sum_insured=250000.0, gross_premium=2500.0,
                ret_si=75000.0, ret_prem=750.0, sur_si=175000.0, sur_prem=1751.0)  # within 1 unit
    exc = []
    r = PremiumRow(**base)
    ad.check_premium_row(r, exceptions=exc, path=Path("x.xlsx"), sheet="S", excel_row=2)
    assert not exc
    r2 = PremiumRow(**{**base, "gross_premium": 10000.0})  # the 100% figure
    ad.check_premium_row(r2, exceptions=exc, path=Path("x.xlsx"), sheet="S", excel_row=3)
    assert [e.reason for e in exc] == ["allocation_not_matching_base"] and "GP" in exc[0].detail
    assert r2.gross_premium == 10000.0  # value untouched


def test_claims_ppn_calculation_is_a_per_adapter_setting(monkeypatch):
    """Non-AIICO default: PPN blank (copy source only). Switched on per adapter
    (or per period) it is amount / total and flagged 'Calculated'."""
    heirs = get_adapter("HEIRS", "DIRECT")
    assert heirs.setting("claims_ppn_calculated") is False
    assert get_adapter("AIICO", "ARK").setting("claims_ppn_calculated") is True
    assert get_adapter("AIICO", "AGRIC DIRECT").setting("claims_ppn_calculated") is True
    rows, _e, audit = _parse_claims_sheet(Path("1ST QTR 2024 CLAIMS.xlsx"), "FIRE PAID CLAIMS",
                                          _claims_tab(), "paid", adapter=heirs)
    assert rows[0].ppn_ret is None and "Calculated" not in audit.notes
    monkeypatch.setattr(type(heirs), "settings", {"claims_ppn_calculated": True})
    rows, _e, audit = _parse_claims_sheet(Path("1ST QTR 2024 CLAIMS.xlsx"), "FIRE PAID CLAIMS",
                                          _claims_tab(), "paid", adapter=heirs)
    assert abs(rows[0].ppn_ret - 0.3) < 1e-12 and "PPN RET/TREATY/FAC Calculated" in audit.notes
    assert "claims_ppn_calculated=True (adapter)" in audit.notes
