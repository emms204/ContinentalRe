"""Vendored Sanlam parser/export honors source sections and outstanding status."""
from pathlib import Path
import os
import subprocess
import sys
import pytest


def test_original_numbered_sections_export_as_outstanding_without_aliases():
    root=Path(__file__).resolve().parents[1]
    raw=root.parent/'data/raw/SANLAM GENERAL INSURANCE/JORDANS GLOBAL/2023/1st Quarter 2023 Premium & Claims Bordereaux.xlsx'
    if not raw.exists():pytest.skip('Private source unavailable')
    code='''
from pathlib import Path
from io import BytesIO
from openpyxl import Workbook,load_workbook
from demo_app import engine,store
from src.domain.cre_cleaner.adapters import get_adapter,sanlam
from src.domain.cre_cleaner.core.quarterly import parse_claims_file
assert Path(sanlam.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
p=Path('../data/raw/SANLAM GENERAL INSURANCE/JORDANS GLOBAL/2023/1st Quarter 2023 Premium & Claims Bordereaux.xlsx')
a=get_adapter('SANLAM','JORDANS');source,_=a.read_source_workbook(p)
paid,ost,_,_=parse_claims_file(p,adapter=a)
assert not any(r.audit.source_sheet=='2021' for r in paid)
original=[r for r in ost if r.audit.source_sheet=='2021']
assert len(original)==495
# Export a disposable, unchanged copy of this source sheet, isolating its
# regression from unrelated premium product labels and other cohort sheets.
w=Workbook();w.active.title='2021'
for row in source['2021']:w.active.append(row)
buf=BytesIO();w.save(buf);w.close()
partner=next(p for p in store.partners() if p['name']=='SANLAM GENERAL INSURANCE(JORDANS)')
before=store.CONFIRMATIONS_PATH.read_bytes();engine._persist=lambda files:[]
out=engine.clean([('Q1 2023.xlsx',buf.getvalue())],partner_id=partner['partner_id'],year=2023,quarter=1,bordereau_type='outstanding',delivery='separate')
assert out.files
book=load_workbook(BytesIO(out.files[0].data),read_only=True,data_only=True)
classes={'Fire','General Accident','Marine Cargo','Marine Hull','Engineering'}
assert {c+' - OUTSTANDING' for c in classes}<=set(book.sheetnames)
assert not any(n.endswith(' - CLAIMS') for n in book.sheetnames)
for c in classes:
    values=list(book[c+' - OUTSTANDING'].values)
    header_at=next(i for i,r in enumerate(values) if 'TOTAL CLAIMS' in r and 'CLAIM NO' in r)
    h=values[header_at];ci=h.index('CLAIM NO');ti=h.index('TOTAL CLAIMS')
    data=[r for r in values[header_at+1:] if r[ci]]
    assert len(data)==sum(r.class_name==c for r in original),(c,len(data))
    assert sum(r[ti] for r in data)==sum(r.total_claims for r in original if r.class_name==c)
book.close()
assert store.CONFIRMATIONS_PATH.read_bytes()==before
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=root,env=dict(os.environ),capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
