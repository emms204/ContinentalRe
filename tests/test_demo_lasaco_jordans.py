"""LASACO/JORDANS cleaning in the isolated Streamlit engine."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_demo_lasaco_jordans_source_fields():
    root=Path(__file__).resolve().parents[1]
    source=root.parent/'data/raw/LASACO/JORDANS/2021/4TH QUARTER 2021 SETTLED RETURNS - To Reinsurers.xlsx'
    if not source.exists():pytest.skip('Private source workbook unavailable')
    code='''
from pathlib import Path
from io import BytesIO
from openpyxl import load_workbook
from demo_app import engine,store
from src.domain.cre_cleaner.adapters import lasaco_jordans
assert Path(lasaco_jordans.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
p=Path('../data/raw/LASACO/JORDANS/2021/4TH QUARTER 2021 SETTLED RETURNS - To Reinsurers.xlsx')
partner=next(p for p in store.partners() if p['name']=='LASACO(JORDANS)')
engine._persist=lambda files:[]
out=engine.clean([(p.name,p.read_bytes())],partner_id=partner['partner_id'],year=2021,quarter=4,bordereau_type='claims',delivery='separate')
assert out.files
w=load_workbook(BytesIO(out.files[0].data),read_only=True,data_only=True)
s=w['Engineering - CLAIMS'];rows=list(s.values);header=rows[3]
record=next(r for r in rows[4:] if 'CAR/10086/HO/2021' in r)
def val(name):return record[header.index(name)]
assert abs(val('RET AMOUNT')-172701.649395)<1e-6
assert val('DATE OF LOSS (day-mth-year)').strftime('%Y-%m-%d')=='2021-02-20'
assert val('DETAILS OF LOSS').startswith('ACCIDENTAL DAMAGE')
w.close()
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=root,env=dict(os.environ),capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
