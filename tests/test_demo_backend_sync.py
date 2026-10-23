"""The Streamlit demo runs the complete, locally vendored backend cleaner."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'demo_app' / '_backend'
PACKAGE = VENDOR / 'src' / 'domain' / 'cre_cleaner'


def test_vendored_cleaner_matches_recorded_source_files():
    manifest = json.loads((VENDOR / 'cleaner-sync.json').read_text())
    actual = {str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}
    assert actual == set(manifest['files'])
    for relative, digest in manifest['files'].items():
        assert hashlib.sha256((PACKAGE / relative).read_bytes()).hexdigest() == digest, relative


def test_vendored_cleaner_matches_available_backend_source():
    source = ROOT.parent / 'reinsurance-ai-backend' / 'src' / 'domain' / 'cre_cleaner'
    if not source.is_dir():
        import pytest
        pytest.skip('Backend source is not part of the standalone demo checkout')
    for p in source.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'):
            assert (PACKAGE / p.relative_to(source)).read_bytes() == p.read_bytes(), str(p)


def test_streamlit_engine_cleans_approved_royal_subclasses():
    # A fresh interpreter verifies that the backend repository cannot satisfy
    # demo imports accidentally. No cloud services or snapshot writes are used.
    code = '''
from datetime import datetime
from io import BytesIO
from pathlib import Path
from openpyxl import Workbook, load_workbook
from demo_app import engine, store
from demo_app import workbook_metrics
from src.domain.cre_cleaner import pipeline
from src.domain.cre_cleaner.adapters import get_adapter
assert Path(pipeline.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
assert workbook_metrics._read_workbook_sheets.__module__ == 'src.domain.cre_cleaner.io.excel'
assert get_adapter('EMPLE', 'SCIB')
assert get_adapter('NSIA', 'JORDANS')
partner = next(p for p in store.partners() if p['name'] == 'ROYAL EXCHANGE(DIRECT)')
workbook = Workbook(); workbook.remove(workbook.active)
premium = workbook.create_sheet('PL Q1 2021')
premium.append(['Policy No','Insured','Policy Class','Start Date','End Date','Sum Insured','Gross Premium','Retention PPN','Retention SI','Retention Premium','Treaty Quota Share PPN','Treaty Quota Share SI','Treaty Quota Share Premium'])
premium.append(['P1','Example','PUBLIC LIABILITY',datetime(2021,1,1),datetime(2021,12,31),10000,100,20,2000,20,80,8000,80])
for title,total in [('GIT CLM PD Q1 2021','Total Claims Paid'),('FG OS Q1 2021','Outstanding Amount')]:
    sheet=workbook.create_sheet(title)
    sheet.append(['Policy No','Claim No','Insured','Start Date','End Date',total,'Retn Amount','QS Amount'])
    sheet.append(['P1','C1','Example',datetime(2021,1,1),datetime(2021,12,31),100,20,80])
buffer=BytesIO(); workbook.save(buffer);workbook.close()
engine._persist=lambda files: []
outcome=engine.clean([('Q1 2021.xlsx',buffer.getvalue())],partner_id=partner['partner_id'],year=2021,quarter=1,bordereau_type='autodetect',delivery='separate')
assert outcome.files
book=load_workbook(BytesIO(outcome.files[0].data),read_only=True)
assert {'General Accident - PREMIUM','General Accident - CLAIMS','General Accident - OUTSTANDING'} <= set(book.sheetnames)
book.close()
'''
    run = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                         env=dict(os.environ), capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stdout + run.stderr


def test_streamlit_starts_without_optional_secrets_file():
    import pytest
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / 'demo_app' / 'app.py'), default_timeout=30)
    app.run()
    assert not app.exception, [item.message for item in app.exception]
    assert not app.error, [item.value for item in app.error]
