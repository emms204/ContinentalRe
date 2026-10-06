"""IMPL-20260929-03: claims discovery matches the quarter only as a whole token."""
from pathlib import Path

import pytest

from cre_cleaner.adapters import get_adapter
from cre_cleaner.adapters.base import quarters_in_text


@pytest.mark.parametrize("name,expected", [
    ("Q2 2021 claims.xls", {2}),
    ("QTR 2 2021 claims.xls", {2}),
    ("QTR. 2 2021 claims.xls", {2}),
    ("2ND QTR 2021 claims.xls", {2}),
    ("2nd Qtr. 2020 - Claims Bord.xlsx", {2}),
    ("SECOND QUARTER 2021 claims.xls", {2}),
    ("4TH Qtr 2021 Claims Bord.xls", {4}),
    ("1ST QTR 2020 - CLAIMS BORD.xlsx", {1}),
    ("1st Qtr 2025 Claims Bord.xlsx", {1}),
    ("Qtr 2021 claims.xls", set()),
    ("2021 QTR claims.xls", set()),
    ("Q 2021 claims.xls", set()),
    ("QTR2021 claims.xls", set()),
])
def test_quarters_in_text_whole_token(name, expected):
    assert quarters_in_text(name) == expected


_AIICO_2025 = [
    "1st Qtr 2025 Claims Bord.xlsx", "2nd Qtr 2025 Claims Paid Bord.xlsx",
    "3rd Qtr 2025 Claims Bord.xls", "4th Qtr 2025 Claims Bord.xls",
]
_AIICO_2020 = [
    "1ST QTR 2020 - CLAIMS BORD.xlsx", "2nd Qtr. 2020 - Claims Bord.xlsx",
    "3rd Qtr. 2020 - Claims Paid.xlsx", "4th Qtr. 2020 - Claims Paid.xls",
]
_AIICO_2021 = [
    "1st Qtr. 2021 - Claims Bord.xls", "2nd Qtr. 2021 - Claims Bord.xls",
    "3RD Qtr. 2021 - Claims Bord.xls", "4TH Qtr 2021 Claims Bord.xls",
]


@pytest.mark.parametrize("year,names", [(2020, _AIICO_2020), (2021, _AIICO_2021), (2025, _AIICO_2025)])
def test_aiico_claims_discovery_one_file_per_quarter(tmp_path: Path, year, names):
    d = tmp_path / str(year)
    d.mkdir()
    for n in names:
        (d / n).write_bytes(b"")
    ad = get_adapter("AIICO", "ARK")
    used = {}
    for q in range(1, 5):
        got = [p.name for p in ad.discover_claims_files(d, year, q)]
        assert got == [names[q - 1]], (q, got)
        for n in got:
            used.setdefault(n, []).append(q)
    # every claims file feeds exactly one quarter (no cross-quarter loading)
    assert all(len(qs) == 1 for qs in used.values()), used


def test_aiico_claims_year_digit_not_quarter(tmp_path: Path):
    d = tmp_path / "2021"
    d.mkdir()
    (d / "Claims Qtr 2021.xls").write_bytes(b"")      # no quarter at all
    (d / "2021 QTR 4 Claims.xls").write_bytes(b"")    # '1 QTR' inside 2021 is not Q1
    ad = get_adapter("AIICO", "ARK")
    assert [p.name for p in ad.discover_claims_files(d, 2021, 1)] == []
    assert [p.name for p in ad.discover_claims_files(d, 2021, 2)] == []
    assert [p.name for p in ad.discover_claims_files(d, 2021, 4)] == ["2021 QTR 4 Claims.xls"]
