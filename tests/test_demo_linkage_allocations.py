"""The demo exports the original, validated Q2 Linkage source allocations."""
from pathlib import Path
import os
import subprocess
import sys
import pytest


def test_label_period_policy_keeps_mismatch_checks(tmp_path):
    from openpyxl import Workbook
    from demo_app.engine import _reject_strong_period, CleanError
    p=tmp_path/'Q2 2021.xlsx'
    w=Workbook();w.active.append(['Q1 2021 PREMIUM BORDEREAU']);w.active.append(['Policy No','Gross Premium'])
    w.active.append(['P1',100]);w.save(p);w.close()
    _reject_strong_period(p,2021,2,period_source='label')
    with pytest.raises(CleanError):_reject_strong_period(p,2022,2,period_source='label')
    with pytest.raises(CleanError):_reject_strong_period(p,2021,1,period_source='label')


def test_original_linkage_q2_allocations_in_download():
    root=Path(__file__).resolve().parents[1]
    source=root.parent/'data/raw/LINKAGE/AGRIC/2020_2021/Q2 2021 AGRIC ACCOUNT STATEMENT TO SEND.xlsx'
    if not source.exists():pytest.skip('Private source unavailable')
    code='''
from pathlib import Path
from io import BytesIO
from openpyxl import load_workbook
from demo_app import engine,store
from src.domain.cre_cleaner.adapters import linkage
assert Path(linkage.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
p=Path('../data/raw/LINKAGE/AGRIC/2020_2021/Q2 2021 AGRIC ACCOUNT STATEMENT TO SEND.xlsx')
partner=next(p for p in store.partners() if p['name']=='LINKAGE(AGRIC)')
before=store.CONFIRMATIONS_PATH.read_bytes()
engine._persist=lambda files:[]
out=engine.clean([(p.name,p.read_bytes())],partner_id=partner['partner_id'],year=2021,quarter=2,bordereau_type='autodetect',delivery='separate')
assert out.files
w=load_workbook(BytesIO(out.files[0].data),data_only=True,read_only=True)
rs=list(w['Agriculture - PREMIUM'].values)
hi=next(i for i,r in enumerate(rs) if 'POLICY NO.' in r and 'GROSS PREMIUM' in r)
h=rs[hi];pi=h.index('POLICY NO.')
data=[r for r in rs[hi+1:] if len(r)>pi and r[pi]]
assert len(data)==23
names=['RET PROPORTION %','RET SUM INSURED','RET PREMIUM','TREATY PROPORTION %','TREATY SUM INSURED','TREATY PREMIUM']
assert all(all(r[h.index(n)] is not None for n in names) for r in data)
first=next(r for r in data if r[pi]=='AGPT/21/01/50527/HQR')
values=[first[h.index(n)] for n in names]
assert values==[.35,10500000,288750,.65,19500000,536250],values
assert all(r[h.index('UNDERWRITING YEAR')] is None and r[h.index('FAC PREMIUM')] is None for r in data)
w.close()
assert store.CONFIRMATIONS_PATH.read_bytes()==before
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=root,env=dict(os.environ),capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
