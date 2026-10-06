"""Guard (Bisola 2026-09-29 13:44 WAT): FAC is NOT a class. FAC / Facultative
tabs are excluded for EVERY cedant and no adapter may produce a FAC class
sheet. FAC proportion / SI / premium columns inside class sheets are kept."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl
import pytest

from cre_cleaner.adapters import ADAPTERS
from cre_cleaner.config import FAC_CLASS_LABEL
from cre_cleaner.core.class_labels import is_fac_class, is_fac_sheet_name, normalize_class_label
from cre_cleaner.core.quarterly import parse_claims_file, parse_premium_file

FAC_TABS = ["FAC", "FACULTATIVE", "FAC OBLIG", "Fac Oblig 2024", "FACULTATIVE PREMIUM"]
FAC_LABELS = ["FAC", "FACULTATIVE", "FAC OBLIG", "Facultative"]
ADAPTER_KEYS = sorted(ADAPTERS)


def test_fac_tab_names_are_excluded():
    for name in FAC_TABS:
        assert is_fac_sheet_name(name), name


@pytest.mark.parametrize("key", ADAPTER_KEYS, ids=["_".join(k) for k in ADAPTER_KEYS])
def test_no_adapter_turns_fac_into_a_class(key):
    ad = ADAPTERS[key]()
    cmap = ad.class_map() if hasattr(ad, "class_map") else None
    # every FAC-ish label resolves to the FAC marker, which the pipeline drops
    for lab in FAC_LABELS:
        assert is_fac_class(lab, cmap), (key, lab)
    # no adapter class extra maps anything onto the FAC marker or a FAC-named class
    for src, dst in (getattr(ad, "class_label_extra", {}) or {}).items():
        assert dst != FAC_CLASS_LABEL and not str(dst).upper().startswith("FAC"), (key, src, dst)
    assert not getattr(ad, "include_fac", False), key


@pytest.mark.parametrize("key", ADAPTER_KEYS, ids=["_".join(k) for k in ADAPTER_KEYS])
def test_fac_tabs_ignored_by_every_adapter(tmp_path, key):
    ad = ADAPTERS[key]()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FACULTATIVE"
    ws.append(["POLICY NO", "NAME OF INSURED", "SUM INSURED", "GROSS PREMIUM"])
    ws.append(["P/1", "ACME", 1000.0, 10.0])
    ws2 = wb.create_sheet("FAC OBLIG")
    ws2.append(["POLICY NO", "NAME OF INSURED", "CLAIM NO", "TOTAL CLAIMS PAID"])
    ws2.append(["P/1", "ACME", "C/1", 5.0])
    p = tmp_path / "1ST QTR 2024 FAC.xlsx"
    wb.save(p)
    rows, _exc, _aud = parse_premium_file(p, "", adapter=ad)
    assert rows == [], key
    paid, ost, _exc2, _aud2 = parse_claims_file(p, adapter=ad)
    assert paid == [] and ost == [], key


def test_default_output_has_no_fac_class_sheet(tmp_path):
    from cre_cleaner.io.excel import _filter_fac_labels
    assert FAC_CLASS_LABEL not in _filter_fac_labels(["Fire", FAC_CLASS_LABEL, "Motor"], False)
    assert normalize_class_label("FAC") == FAC_CLASS_LABEL
