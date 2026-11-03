"""Merged financial regressions must also pass in the demo vendor."""
import os
from pathlib import Path
import subprocess
import sys


def test_vendored_jomola_controlled_recovery_and_emple_explicit_year():
    root = Path(__file__).resolve().parents[1]
    code = '''
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from openpyxl import Workbook
from demo_app import engine
from src.domain.cre_cleaner.adapters import get_adapter,heirs,emple
from src.domain.cre_cleaner.core.quarterly import parse_claims_file
for module in (heirs,emple):
    assert Path(module.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
with TemporaryDirectory() as td:
    def book(name,rows):
        w=Workbook();s=w.active;s.title='CLAIMS'
        for row in rows:s.append(row)
        p=Path(td)/name;w.save(p);w.close();return p
    header=['DATE OF LOSS','REGISTERED DATE',None,'NAME OF INSURED/ CLAIMANT','CLAIM NO','NEW CLAIM NO.','POLICY NO','PERIOD OF COVER',None,'DESCRIPTION OF LOSS','SUM INSURED','AMOUNT PAID','ADJUSTER/ OTHER FEE','TOTAL CLAIMS PAID']
    row=[datetime(2021,1,1),None,'FIRE','Example','OLD-C1','C1','P1',datetime(2021,1,1),datetime(2021,12,31),'Damage',1000,100,None,100,60]
    for footer,expected in [(60,60),(75,None)]:
        p=book(f'Q4 2021 {footer}.xlsx',[header,[None]*7+['START DATE','END DATE'],row,[None]*13+[100,footer]])
        paid,_,_,_=parse_claims_file(p,adapter=get_adapter('HEIRS','JOMOLA'))
        assert len(paid)==1
        assert paid[0].to_template_values()['TREATY AMOUNT']==expected
        assert paid[0].ppn_treaty is None
    for broker in ('SCIB','AON'):
        p=book('Q4 2019.xlsx',[
          ['Claim No','Policy','Client Name','GROSS LOSS','TREATY RECOVERY','UW YR','Loss Date','NAICOM Class Description'],
          [None,'2018 U/YR'],
          ['C1','P1','Example',-100,-80,'2016','2017.05.05','Fire Insurance']])
        paid,_,_,_=parse_claims_file(p,adapter=get_adapter('EMPLE',broker))
        value=paid[0].to_template_values()['UW YR']
        assert value==2016 and type(value) is int
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root,
                            env=dict(os.environ), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
