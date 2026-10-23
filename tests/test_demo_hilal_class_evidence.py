"""Cross-quarter class evidence reaches the isolated demo engine."""
import subprocess
import sys
from pathlib import Path


def test_demo_hilal_date_classes_use_uploaded_claims_evidence():
    code = '''
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile
from pathlib import Path
from openpyxl import Workbook, load_workbook
from demo_app import engine, store
from src.domain.cre_cleaner.adapters import get_adapter
assert Path(__import__('src.domain.cre_cleaner.adapters.cornerstone', fromlist=['x']).__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
partner = next(p for p in store.partners() if p['name'].upper() == 'CORNERSTONE(HILAL)')
files=[]
for quarter,label in [(2,'G.I.T'),(4,datetime(2023,10,6))]:
    w=Workbook(); s=w.active; s.title='CLAIMS'
    s.append(['CLASS','POLICY NO','CLAIM NO','PARTICIPANT','PERIOD OF INS','TOTAL PAID AMOUNT','DETAILS OF LOSS','DATE OF LOSS','RETENTION AMOUNT','TREATY'])
    s.append([label,'GIT1',f'C{quarter}','Example','01/02/2023-31/01/2024',100,'LOSS OF GOODS IN TRANSIT',datetime(2023,5,1),20,80])
    b=BytesIO(); w.save(b);w.close();files.append((f'Q{quarter} 2023.xlsx',b.getvalue()))
engine._persist=lambda files: []
outcome=engine.clean(files,partner_id=partner['partner_id'],year=2023,quarter=None,bordereau_type='claims',delivery='separate')
assert outcome.files
books=[]
for output in outcome.files:
    if output.name.endswith('.zip'):
        with ZipFile(BytesIO(output.data)) as z:
            books.extend((name,z.read(name)) for name in z.namelist() if name.endswith('.xlsx'))
    else: books.append((output.name,output.data))
assert len(books)==2
for name,data in books:
    w=load_workbook(BytesIO(data),read_only=True)
    assert 'General Accident - CLAIMS' in w.sheetnames, (name,w.sheetnames)
    w.close()
'''
    result = subprocess.run([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_demo_hilal_claims_resolve_from_source_parent_headings():
    code = '''
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook, load_workbook
from demo_app import engine, store
partner=next(p for p in store.partners() if p['name'].upper()=='CORNERSTONE(HILAL)')
w=Workbook();s=w.active;s.title='Claim'
header=['CLASS','PLAN NO','CLAIM NO','PARTICIPANT','PERIOD OF INS','TOTAL PAID AMOUNT','DETAILS OF LOSS','DATE OF LOSS','RETENTION AMOUNT','TREATY']
for label,parent,policy in [('GROUP FAMILY','LIFE','L1'),('GL','LIFE','L2'),('GOOD IN TRANSIT','GENERAL ACCIDENT','G1')]:
    s.append([None,parent]);s.append(header)
    s.append([label,policy,'C'+policy,'Example','01/01/2025-31/12/2025',100,'Description',datetime(2025,2,1),20,80])
b=BytesIO();w.save(b);w.close()
engine._persist=lambda files: []
result=engine.clean([('Q1 2025.xlsx',b.getvalue())],partner_id=partner['partner_id'],year=2025,quarter=1,bordereau_type='claims',delivery='separate')
assert len(result.files)==1
w=load_workbook(BytesIO(result.files[0].data),read_only=True)
assert {'Life - CLAIMS','General Accident - CLAIMS'}<=set(w.sheetnames)
assert len(list(w['Life - CLAIMS'].iter_rows(min_row=5,values_only=True)))==2
w.close()
'''
    result = subprocess.run([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
