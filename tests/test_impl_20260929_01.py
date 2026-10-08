"""IMPL-20260929-01: premium treaty block columns (PPN\\d, RTNTN PPN, TREATY PPN\\d,
SUM INSURED\\d, TREATY SUM INSURED ?\\d, PREMIUM\\d, TREATY PREMIUM\\d). Each band's
SI/premium come only from its own columns, never from retention."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cre_cleaner.adapters.aiico_ark import AiicoArkAdapter
from cre_cleaner.core.map_columns import detect_premium_allocation_blocks as generic_detect

# IMPL-20260929-04: the numbered / RTNTN band rules are AIICO adapter rules.
detect_premium_allocation_blocks = AiicoArkAdapter().detect_premium_allocation_blocks
from cre_cleaner.core.reconcile import check_row_splits
from cre_cleaner.models import PremiumRow


def _bands(cm):
    return ((cm.ret_ppn, cm.ret_si, cm.ret_prem), (cm.sur_ppn, cm.sur_si, cm.sur_prem))


def test_2nd_surplus_ppn2_block_uses_own_columns():
    # 2020 Q2 '2nd Surplus' (April Premium s.xlsx) layout
    h = [None, "INSURED", "POLICY NO", "DEBIT NOTE", "PERIOD OF INSURANCE", "SUM INSURED",
         "PREMIUM", "PPN", "SUM INSURED", "PREMIUM", "PPN2", "SUM INSURED2", "PREMIUM2"]
    g = [None] * 7 + ["OWN RETENTION", None, None, "TREATY", None, None]
    cm = detect_premium_allocation_blocks(h, g)
    assert _bands(cm) == ((7, 8, 9), (10, 11, 12))
    assert cm.borrowed_blocks == []


def test_rtntn_treaty_ppn_layout_2022_onwards():
    h = ["INSURED", "POLICY NO", "DEBIT NOTE", "RISK COV FROM", "RISK COV TO", "SUM INSURED",
         "PREMIUM", "RTNTN PPN", "RTNTN SUM INSURED", "RTNTN PREMIUM", "TREATY PPN1",
         "TREATY SUM INSURED", "TREATY PREMIUM1"]
    cm = detect_premium_allocation_blocks(h, None)
    assert _bands(cm) == ((7, 8, 9), (10, 11, 12))
    assert cm.get("period_from") == 3 and cm.get("period_to") == 4
    h2 = h[:10] + ["TREATY PPN2", "TREATY SUM INSURED 1", "TREATY PREMIUM2"]
    cm2 = detect_premium_allocation_blocks(h2, None)
    assert _bands(cm2) == ((7, 8, 9), (10, 11, 12))
    assert cm2.get("gross_premium") == 6 and cm2.get("sum_insured") == 5


def test_quota_share_block_still_treaty():
    h = ["INSURED", "POLICY NO", "DEBIT NOTE", "RISK COV FROM", "RISK COV TO", "SUM INSURED",
         "PREMIUM", "RTNTN PPN", "RTNTN SUM INSURED", "RTNTN PREMIUM", "QUOTA SHARE PPN",
         "QUOTA SHARE SUM INSURED", "QUOTA SHARE PREMIUM"]
    cm = detect_premium_allocation_blocks(h, None)
    assert _bands(cm) == ((7, 8, 9), (10, 11, 12))
    assert cm.get("period_from") == 3 and cm.get("period_to") == 4


def test_two_treaty_layers_first_is_treaty_band_second_extra():
    h = [None, "INSURED", "POLICY NO", "DEBIT NOTE", "PERIOD OF INSURANCE", "SUM INSURED",
         "PREMIUM", "PPN", "SUM INSURED", "PREMIUM", "PPN1", "SUM INSURED", "PREMIUM1",
         "PPN2", "SUM INSURED2", "PREMIUM2"]
    g = [None] * 7 + ["OWN RETENTION", None, None, "TREATY"] + [None] * 5
    cm = detect_premium_allocation_blocks(h, g)
    assert _bands(cm) == ((7, 8, 9), (10, 11, 12))
    assert [b[1:] for b in cm.extra_treaty_blocks] == [(13, 14, 15)]


def test_misaligned_band_row_ppn2_is_treaty_not_retention():
    # July 2021 'ENG 2ND SURP': TREATY label sits one column right of PPN2
    h = [None, "INSURED", "POLICY NO", "DEBIT NOTE", "PERIOD OF INSURANCE", "UW YEAR",
         "SUM INSURED", "PREMIUM", "PPN", "SUM INSURED", "PREMIUM", "PPN2", "SUM INSURED2",
         "PREMIUM2"]
    g = [None] * 8 + ["OWN RETENTION", None, None, None, "TREATY", None]
    cm = detect_premium_allocation_blocks(h, g)
    assert _bands(cm) == ((8, 9, 10), (11, 12, 13))


def test_treaty_never_borrows_retention_columns():
    h = [None, "INSURED", "POLICY NO", "SUM INSURED", "PREMIUM", "PPN", "SUM INSURED",
         "PREMIUM", "PPN2"]
    g = [None] * 5 + ["OWN RETENTION", None, None, "TREATY"]
    cm = detect_premium_allocation_blocks(h, g)
    # no SI/premium of its own -> block not mapped (nothing copied from retention)
    assert (cm.sur_ppn, cm.sur_si, cm.sur_prem) == (None, None, None)
    assert (cm.ret_si, cm.ret_prem) == (6, 7)
    assert cm.borrowed_blocks  # logged -> treaty_columns_not_own exception


def test_fac_out_ppn_without_own_columns_not_mapped():
    # 2024 MAY PREMIUM loc.xls '2nd Fire': trailing 'FAC OUT PPN' with no FAC SI/premium
    h = ["INSURED", "POLICY NO", "DEBIT NOTE", "COVER START", "COVER END", "RISK COV FROM",
         "RISK COV TO", "SUM INSURED", "PREMIUM", "RTNTN PPN", "RTNTN SUM INSURED",
         "RTNTN PREMIUM", "TREATY PPN2", "TREATY SUM INSURED 1", "TREATY PREMIUM2", "FAC OUT PPN"]
    cm = detect_premium_allocation_blocks(h, None)
    assert _bands(cm) == ((9, 10, 11), (12, 13, 14))
    assert (cm.fac_ppn, cm.fac_si, cm.fac_prem) == (None, None, None)
    assert cm.borrowed_blocks


def test_treaty_equals_retention_row_flagged():
    r = PremiumRow(policy_no="P1", gross_premium=200.0, ret_ppn=50.0, ret_si=1000.0,
                   ret_prem=100.0, sur_ppn=50.0, sur_si=1000.0, sur_prem=100.0)
    ok = PremiumRow(policy_no="P2", gross_premium=200.0, ret_ppn=25.0, ret_si=500.0,
                    ret_prem=50.0, sur_ppn=75.0, sur_si=1500.0, sur_prem=150.0)
    exc, counts = check_row_splits([r, ok], [], [])
    # Gold keeps equal-split rows; no WARN — SUMMARY still tallies ok/mismatch.
    assert exc == []
    assert counts["premium"]["ok"] == 2


def test_share_sum_not_100_is_flagged_not_altered():
    r = PremiumRow(policy_no="P3", gross_premium=100.0, ret_ppn=30.0, ret_si=1.0,
                   ret_prem=30.0, sur_ppn=60.0, sur_si=2.0, sur_prem=70.0,
                   fac_ppn=0.0, fac_si=0.0, fac_prem=0.0)
    exc, counts = check_row_splits([r], [], [])
    assert exc == []
    assert counts["premium"]["mismatch"] == 1
    assert r.sur_ppn == 60.0 and r.ret_ppn == 30.0


def test_numbered_ppn_rules_are_aiico_only():
    """IMPL-20260929-04: the shared core does not know AIICO's PPN2 / RTNTN headers."""
    h = [None, "INSURED", "POLICY NO", "DEBIT NOTE", "PERIOD OF INSURANCE", "SUM INSURED",
         "PREMIUM", "PPN", "SUM INSURED", "PREMIUM", "PPN2", "SUM INSURED2", "PREMIUM2"]
    g = [None] * 7 + ["OWN RETENTION", None, None, "TREATY", None, None]
    assert _bands(detect_premium_allocation_blocks(h, g)) == ((7, 8, 9), (10, 11, 12))
    assert _bands(generic_detect(h, g))[1] != (10, 11, 12)
