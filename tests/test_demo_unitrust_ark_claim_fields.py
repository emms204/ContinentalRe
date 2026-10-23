"""The demo exports source amounts/percentages and cover dates for Unitrust ARK."""
from pathlib import Path
import os,subprocess,sys
import pytest


def test_original_claims_and_outstanding_exports():
    root=Path(__file__).resolve().parents[1]
    if not (root.parent/'data/raw/UNITRUST/UNITRUST & ARK').exists():pytest.skip('Private sources unavailable')
    code='''
from pathlib import Path
from io import BytesIO
from datetime import datetime
from math import isclose
import hashlib
from openpyxl import load_workbook,Workbook
from demo_app import engine,store
from src.domain.cre_cleaner.adapters import unitrust,get_adapter
from src.domain.cre_cleaner.io.excel import read_workbook_sheets
assert Path(unitrust.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
p=next(p for p in store.partners() if (lambda a:(a.cedant,a.broker))(get_adapter(*store.partner_name_to_cedant_broker(p['name'])))==('UNITRUST','ARK'))
root=Path('../data/raw/UNITRUST/UNITRUST & ARK')
before=store.CONFIRMATIONS_PATH.read_bytes();engine._persist=lambda fs:[]
for name,year,quarter,sheet,claim in [
 ('2025/Q1 2025 CLAIMS PAID NAIRA.xlsx',2025,1,'Engineering - CLAIMS','IKJEAR2025018983'),
 ('2022/Q4 OUTSTANDING BORDEREAUX.xlsx',2022,4,'General Accident - OUTSTANDING','2016000011-L544')]:
    source=root/name;data=source.read_bytes();original=hashlib.sha256(data).hexdigest()
    # Keep the actual source sheet intact while isolating field export from
    # unrelated Marine class decisions elsewhere in the workbook.
    sheets=read_workbook_sheets(source);tab='ENG' if year==2025 else 'GEN ACC'
    fixture=Workbook();fixture.active.title=tab
    for row in sheets[tab]:fixture.active.append(row)
    payload=BytesIO();fixture.save(payload);fixture.close()
    outcome=engine.clean([(source.name,payload.getvalue())],partner_id=p['partner_id'],year=year,quarter=quarter,bordereau_type='autodetect',delivery='separate')
    assert outcome.files
    w=load_workbook(BytesIO(outcome.files[0].data),read_only=True,data_only=True)
    rows=list(w[sheet].values);hi=next(i for i,r in enumerate(rows) if 'CLAIM NO' in r and 'TOTAL CLAIMS' in r);h=rows[hi]
    matching=[r for r in rows[hi+1:] if r[h.index('CLAIM NO')]==claim]
    if year==2025:
        # Three actual payments for one claim must remain separate.
        assert len(matching)==3,len(matching)
        r=next(r for r in matching if isclose(r[h.index('TOTAL CLAIMS')],9231576.28))
        assert isclose(r[h.index('PPN RET %')],17.5972875)
        assert r[h.index('RET AMOUNT')]==1624507.02
        assert isclose(r[h.index('PPN TREATY %')],82.4027124836)
        assert r[h.index('TREATY AMOUNT')]==7607069.26
    else:
        r=matching[0]
        assert r[h.index('TOTAL CLAIMS')]==0
        assert r[h.index('FROM')]==datetime(2016,1,1)
        assert r[h.index('TO')]==datetime(2016,12,31)
        assert r[h.index('PPN RET %')]==26.23
    assert r[h.index('UW YR')] is None
    w.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
assert store.CONFIRMATIONS_PATH.read_bytes()==before
'''
    r=subprocess.run([sys.executable,'-c',code],cwd=root,env=dict(os.environ),capture_output=True,text=True,timeout=120)
    assert r.returncode==0,r.stdout+r.stderr
