"""Verify original Zenith source shares reach the vendored demo export."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


def test_original_zenith_claims_ppn_exports():
    root = Path(__file__).resolve().parents[1]
    if not (root.parent / 'data/raw/ZENITH').exists():
        pytest.skip('Private sources unavailable')
    code = '''
from pathlib import Path
from io import BytesIO
from math import isclose
from openpyxl import Workbook, load_workbook
from demo_app import engine, store
from src.domain.cre_cleaner.adapters import get_adapter, zenith
from src.domain.cre_cleaner.io.excel import read_workbook_sheets
assert Path(zenith.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
before = store.CONFIRMATIONS_PATH.read_bytes()
engine._persist = lambda fs: []
for broker,year,relative,claim,expected in [
 ('ARK',2024,'ARK/2024/Q1/Q1 TREATY RETURNS 2nd surplus & local (2).xlsx',
  'ZG/C/1000/FSP/23/000069',(0.16321053844399,0.0245263077040068,0)),
 ('JORDANS',2025,'JORDANS GLOBAL/2025/Q1/Q1 2025 TREATY RETURNS - 1ST, 2ND SURPLUS & LOCAL To Reinsurers.xlsx',
  'ZG/C/1000/FSP/24/000162',(0.0228364308668161,0.5709107716704027,0.4062527974627812))]:
    partner = next(p for p in store.partners() if
       (lambda a:(a.cedant,a.broker))(get_adapter(*store.partner_name_to_cedant_broker(p['name']))) == ('ZENITH',broker))
    source = Path('../data/raw/ZENITH')/relative
    original = source.read_bytes()
    # Isolate the unchanged source Fire sheet from unrelated class reviews.
    rows = read_workbook_sheets(source)['FIRE CLAIMS']
    fixture = Workbook();fixture.active.title = 'FIRE CLAIMS'
    for row in rows:fixture.active.append(row)
    data = BytesIO();fixture.save(data);fixture.close()
    result = engine.clean([(source.name,data.getvalue())],partner_id=partner['partner_id'],
       year=year,quarter=1,bordereau_type='autodetect',delivery='separate')
    matches = []
    for output in result.files:
        w = load_workbook(BytesIO(output.data),read_only=True,data_only=True)
        if 'Fire - CLAIMS' in w.sheetnames:
            rr = list(w['Fire - CLAIMS'].values)
            hi = next(i for i,r in enumerate(rr) if 'CLAIM NO' in r and 'TOTAL CLAIMS' in r)
            h = rr[hi]
            matches += [dict(zip(h,r)) for r in rr[hi+1:] if r[h.index('CLAIM NO')] == claim]
        w.close()
    assert len(matches) == 1,len(matches)
    r = matches[0]
    for field,value in zip(('PPN RET %','PPN TREATY %','PPN FAC %'),expected):
        assert isclose(r[field],value,rel_tol=1e-12), (field,r[field],value)
    assert isclose(r['RET AMOUNT'],13056843.0755192 if year==2024 else 3202.700042567107)
    assert isclose(r['TREATY AMOUNT'],1962104.6163205442 if year==2024 else 80067.50106417768)
    assert source.read_bytes() == original
assert store.CONFIRMATIONS_PATH.read_bytes() == before
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root,
                            env=dict(os.environ), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
