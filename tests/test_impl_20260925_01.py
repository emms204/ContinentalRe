"""IMPL-20260925-01 regression tests: approved class mappings, all claims header
blocks parsed, narrative CLAIM NOTIFICATION rows kept, TOTAL LOSS/SPILLAGE
narratives on real transactions kept (not treated as total rows)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cre_cleaner.adapters.aiico_ark import AiicoArkAdapter
from cre_cleaner.core.class_labels import is_unresolved_class
from cre_cleaner.core.class_labels import normalize_class_label as _normalize

# IMPL-20260929-04: CAS / CASUALTY / HOUSEHOLDERS are AIICO adapter class keys.
_AIICO = AiicoArkAdapter()


def normalize_class_label(raw):
    return _normalize(raw, _AIICO.class_map())
from cre_cleaner.core.filters import has_transaction_identifiers, looks_like_total_row
from cre_cleaner.core.quarterly import (
    _has_claims_header_labels,
    _is_claims_header_block_start,
    _parse_claims_sheet,
)


def test_approved_general_accident_mappings():
    for raw in ("CAS", "Cas", "CASUALTY", "Casualty", "HOUSEHOLDERS", "Householders"):
        assert normalize_class_label(raw) == "General Accident", raw


def test_cas_is_exact_match_only():
    # 'CAS' must not match inside other words through the substring fallback.
    assert normalize_class_label("CASH") == "Cash"
    assert normalize_class_label("MCASX") == "Mcasx"


def test_marine_2nd_surplus_stays_unresolved():
    # Pending Bisola: MARINE 2ND SURPLUS / MARINE 2ND SURP go to exceptions.
    assert is_unresolved_class("MARINE 2ND SURPLUS")
    assert is_unresolved_class("MARINE 2ND SURP")
    assert normalize_class_label("MARINE HULL") == "Marine Hull"


def test_total_narrative_on_real_transaction_is_not_a_total_row():
    # insured, policy, claim no, narrative, amount (Matrix Energy CL/045993)
    key = [0, 1, 2]
    row = ["MATRIX ENERGY LIMITED", "302/00037/18/TQ/HO", "CL/045993/302/21/TQ/HO",
           "TOTAL SPILLAGE OF PRODUCT", 2000000]
    assert has_transaction_identifiers(row, key)
    assert not looks_like_total_row(row, key)
    row2 = ["MATRIX ENERGY LIMITED", "302/00037/18/TQ/HO", "CL/053442/302/23/TQ/HO",
            "TOTAL LOSS", 12870000]
    assert not looks_like_total_row(row2, key)


def test_real_total_rows_still_skipped():
    key = [0, 1, 2]
    assert looks_like_total_row(["TOTAL", None, None, None, 5000], key)
    assert looks_like_total_row([None, None, None, "GRAND TOTAL", 5000], key)
    assert looks_like_total_row(["SUB TOTAL", "", "", None, 5000], key)
    # a numeric 'policy' cell is not an identifier
    assert looks_like_total_row(["TOTAL", 12, None, None, 5000], key)


def test_claim_notification_narrative_is_not_a_header():
    hdr = ["S/NO", "INSURED", "POLICY NO", "CLAIM NO", "DATE OF LOSS", "AMOUNT"]
    assert _has_claims_header_labels(hdr)
    assert _is_claims_header_block_start(hdr)
    narrative = ["1", "APM TERMINALS APAPA LTD", "503/0098/11/HO", "CL/051017/503/22/TQ/HO",
                 "CLAIM NOTIFICATION - DAMAGE TO CRANE UNDER THE POLICY", 150000]
    assert not _has_claims_header_labels(narrative)


def _two_block_tab():
    hdr_small = ["S/NO", "INSURED", "POLICY NO", "CLAIM NO", "DATE OF LOSS", "TOTAL CLAIMS"]
    hdr_big = ["S/NO", "INSURED", "POLICY NO", "CLAIM NO", "DATE OF LOSS",
               "PERIOD FROM", "PERIOD TO", "SUM INSURED", "TOTAL CLAIMS",
               "RETENTION", "TREATY"]
    return [
        ["FIRE PAID CLAIM"],
        [None],
        hdr_small,
        [1, "ACME LTD", "212/00005/17/TQ/HO", "CL/040439/212/20/TQ/HO", "24/12/2020", 1815767.53],
        [None],
        ["ENGINEERING PAID CLAIM"],
        hdr_big,
        [1, "BETA LTD", "207/00001/20/TQ/OW", "CL/041714/212/21/TQ/OW", "04/01/2021",
         "01/01/2021", "31/12/2021", 5000000, 1092372.69, 65589.33, 1026783.36],
    ]


def test_header_block_above_best_header_is_parsed():
    rows, _exc, audit = _parse_claims_sheet(
        Path("2nd Qtr. 2021 - Claims Bord.xls"), "2ND SURPLUS TREATY", _two_block_tab(), "paid",
        adapter=_AIICO,
    )
    claim_nos = sorted(str(r.claim_no) for r in rows)
    assert "CL/040439/212/20/TQ/HO" in claim_nos, claim_nos  # block above the best header
    assert "CL/041714/212/21/TQ/OW" in claim_nos, claim_nos


def test_cas_mapping_is_aiico_only():
    """IMPL-20260929-04: generic class map has no AIICO keys."""
    assert _normalize("CAS") == "Cas"
    assert normalize_class_label("CAS") == "General Accident"
