"""Real 2019 EMPLE source regression through the isolated demo package."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_demo_emple_2019_allocations_and_uw_sections():
    root = Path(__file__).resolve().parents[1]
    source = root.parent / 'data/raw/EMPLE/SCIB/2019/Premium Paid Loss Bord. Q4 19 OMNGIC.XLSX'
    if not source.exists():
        pytest.skip('Private source workbook is not in this checkout')
    code = '''
from pathlib import Path
from demo_app import engine
from src.domain.cre_cleaner.adapters import get_adapter
from src.domain.cre_cleaner.core.quarterly import parse_premium_file,parse_claims_file
import src.domain.cre_cleaner.adapters.emple as emple
assert Path(emple.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
p=Path('../data/raw/EMPLE/SCIB/2019/Premium Paid Loss Bord. Q4 19 OMNGIC.XLSX')
a=get_adapter('EMPLE','SCIB')
pr,_,_=parse_premium_file(p,'Q4',adapter=a)
cl,os,_,_=parse_claims_file(p,adapter=a)
assert (len(pr),len(cl),len(os))==(178,270,0)
r=next(r for r in pr if r.policy_no=='617664720')
assert (r.ret_si,r.ret_ppn,r.ret_prem)==(37500000,.075,92875.806)
assert r.sur_prem==1145468.2740000002 or abs(r.sur_prem-1145468.274)<1e-6
assert sum(r.uw_yr==2018 for r in cl)==9
assert sum(r.uw_yr==2019 for r in cl)==30
assert all(r.period_from is None and r.period_to is None and not r.details for r in cl)
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root,
                            env=dict(os.environ), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
