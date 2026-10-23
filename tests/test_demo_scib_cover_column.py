import subprocess
import sys
from pathlib import Path


def test_demo_scib_reads_source_covers_and_keeps_home_guard_for_review():
    code = '''
from io import BytesIO
from datetime import datetime
from openpyxl import Workbook,load_workbook
from demo_app import engine,store
partner=next(p for p in store.partners() if p['name']=='CORNERSTONE(SCIB)')
header=[None,None,'NUMBER','POLICY NO.','NAME OF INSURED','CHANNEL','SUB CHANNEL','INSURED','MPL 60%','RATE','PREMIUM','FROM','TO','%','SUM INSURED','PREMIUM','%','SUM INSURED','PREMIUM','%','SUM INSURED','PREMIUM']
groups=[None,None,'CESSION',None,None,None,None,'TOTAL SUM',None,'PREMIUM','GROSS','INSURANCE PERIOD',None,None,'RETENTION',None,None,'SURPLUS',None,None,'FACULTATIVE',None]
def upload(labels):
    w=Workbook();s=w.active;s.title='Prem Bordreaux 2';s.append(groups);s.append(header)
    for n,label in enumerate(labels,1):
        s.append(['LAGOS',label,f'DN107-22090000{n}',f'P{n}','Example','LAGOS','BROKERS',10000,6000,None,100,datetime(2022,1,1),datetime(2022,12,31),40,4000,40,60,6000,60,None,None,None])
    b=BytesIO();w.save(b);w.close();return [('Q3 2022.xlsx',b.getvalue())]
engine._persist=lambda files: []
kw=dict(partner_id=partner['partner_id'],year=2022,quarter=3,bordereau_type='premium',delivery='separate')
result=engine.clean(upload(['FIRE & SPECIAL PERILS - BUSINESS','MARINE CARGO - SINGLE TRANSIT (IMPORT)']),**kw)
w=load_workbook(BytesIO(result.files[0].data),read_only=True)
assert {'Fire - PREMIUM','Marine Cargo - PREMIUM'}<=set(w.sheetnames);w.close()
try:engine.clean(upload(['FIRE & SPECIAL PERILS - BUSINESS','BETTER HOME GUARD']),**kw)
except engine.NeedsReview as e:
    assert any(x['label']=='BETTER HOME GUARD' for x in e.unresolved)
    assert not any(x['label']=='(blank)' for x in e.unresolved)
else:raise AssertionError('Unknown home cover must require review')
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                          text=True,capture_output=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
