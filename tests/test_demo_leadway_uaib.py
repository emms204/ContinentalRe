"""The original UAIB upload resolves its explicit Marine Cargo title."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_original_2019_uaib_upload():
    root = Path(__file__).resolve().parents[1]
    if not (root.parent/'data/raw/LEADWAY/UAIB/2019').exists():
        pytest.skip('Private source workbooks unavailable')
    code = '''
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from openpyxl import load_workbook
from demo_app import engine, store
from src.domain.cre_cleaner.adapters import leadway
assert Path(leadway.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
partner = next(p for p in store.partners() if p['name']=='LEADWAY(UAIB)')
source = Path('../data/raw/LEADWAY/UAIB/2019')
uploads = [(p.name,p.read_bytes()) for p in sorted(source.iterdir()) if p.suffix.lower() in {'.xls','.xlsx'}]
engine._persist=lambda files: []
out = engine.clean(uploads,partner_id=partner['partner_id'],year=2019,quarter=None,bordereau_type='autodetect',delivery='separate')
assert out.files, out
found = 0
for file in out.files:
    if file.name.endswith('.zip'):
        z=ZipFile(BytesIO(file.data));books=[z.read(n) for n in z.namelist() if n.endswith('.xlsx')]
    elif file.name.endswith('.xlsx'): books=[file.data]
    else: continue
    for data in books:
        w=load_workbook(BytesIO(data),read_only=True,data_only=True)
        if 'Marine Cargo - OUTSTANDING' in w.sheetnames:
            rows=list(w['Marine Cargo - OUTSTANDING'].values)
            col=rows[3].index('CLASS')
            values=[r[col] for r in rows[4:] if r[col]]
            assert values and all(v=='Marine Cargo' for v in values)
            found+=1
        w.close()
assert found==2,found
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root, env=dict(os.environ),
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
