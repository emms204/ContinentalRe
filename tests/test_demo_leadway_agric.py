"""Full annual AGRIC uploads publish without spurious class mapping."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('year',[2022,2023,2024,2025])
def test_annual_leadway_agric_upload_publishes(year):
    root=Path(__file__).resolve().parents[1]
    source=root.parent/'data/raw/LEADWAY/AGRIC'/str(year)
    if not source.exists():pytest.skip('Private source workbooks unavailable')
    code='''
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from openpyxl import load_workbook
from demo_app import engine,store
from src.domain.cre_cleaner.adapters import leadway
assert Path(leadway.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
year=int(sys.argv[1])
p=Path('../data/raw/LEADWAY/AGRIC')/str(year)
partner=next(p for p in store.partners() if p['name']=='LEADWAY(AGRIC)')
engine._persist=lambda files:[]
uploads=[(f.name,f.read_bytes()) for f in sorted(p.iterdir()) if f.suffix.lower() in {'.xlsx','.xls'}]
out=engine.clean(uploads,partner_id=partner['partner_id'],year=year,quarter=None,bordereau_type='autodetect',delivery='separate')
assert out.files
seen=False
paid_counts=[]
for f in out.files:
    if f.name.endswith('.zip'):
        z=ZipFile(BytesIO(f.data));books=[z.read(n) for n in z.namelist() if n.endswith('.xlsx')]
    elif f.name.endswith('.xlsx'):books=[f.data]
    else:continue
    for data in books:
        w=load_workbook(BytesIO(data),read_only=True,data_only=True)
        assert not any('General Accident' in s for s in w.sheetnames)
        seen=seen or 'Agriculture - PREMIUM' in w.sheetnames
        if year==2025:
            rows=list(w['Agriculture - CLAIMS'].values)
            header=rows[3]
            claim_index=header.index('CLAIM NO')
            retention_index=header.index('RET AMOUNT')
            treaty_index=header.index('TREATY AMOUNT')
            claims=[r for r in rows[4:] if r[claim_index]]
            paid_counts.append(len(claims))
            assert all(r[retention_index] is not None for r in claims)
            for r in claims:
                if r[claim_index]=='KD25C000010AP':
                    assert abs(r[treaty_index]-241978014.03606263)<0.01
        w.close()
assert seen
if year==2025: assert sorted(paid_counts)==[6,10,16],paid_counts
'''
    result=subprocess.run([sys.executable,'-c',code,str(year)],cwd=root,env=dict(os.environ),capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
