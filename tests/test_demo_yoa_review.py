from streamlit.testing.v1 import AppTest


def test_yoa_conflict_has_usable_choices_and_submits_only_record_ids(monkeypatch):
    from demo_app.screens import run
    sent=[]
    monkeypatch.setattr(run,'approved_class_options',lambda p:[])
    monkeypatch.setattr(run,'keep_alias',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No alias allowed')))
    monkeypatch.setattr(run,'_run',lambda *a,**kw:sent.append(kw))
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='LEADWAY(YOA)')
e=dict(rule='conflicting_source_classes',section_class='Fire',section_label='FIRE',row_class='TERRORISM',subclass_label='TERRORISM',product_class='Terrorism & PVT',expected_class='Terrorism & PVT',candidates=['Fire','Terrorism & PVT'])
rows=[dict(row_id=rid,source_row=i,insured='Example',policy_no='P1',resolution=e) for i,rid in enumerate(['y1','y2'],2)]
_review(dict(coverage=[],warnings=[],unresolved=[dict(label='CLASS CONFLICT',file='q1.xlsx',sheet='Premium Bordereau',records=2,expected_class='Terrorism & PVT',suggestion_reason='conflicting_source_classes',rows=rows)]),p['partner_id'],2024,1,'premium','separate')
''').run()
    assert not app.exception
    assert len(app.selectbox)==1
    assert app.selectbox[0].value=='Select class…'
    assert {'Fire','Terrorism & PVT'}<=set(app.selectbox[0].options)
    assert not app.selectbox[0].disabled
    text=' '.join(e.value for e in app.caption)+ ' '.join(e.value for e in app.info)
    assert 'FIRE' in text and 'TERRORISM' in text and 'YOA' in text
    app.selectbox[0].select('Fire').run();app.button[0].click().run()
    assert not app.exception
    assert sent[-1]['row_mappings'][0]['row_ids']==['y1','y2']
    assert sent[-1]['row_mappings'][0]['class_name']=='Fire'


def test_real_yoa_2024_annual_upload_round_trip_without_alias_writes():
    import os
    from pathlib import Path
    import subprocess
    import sys
    import pytest
    root=Path(__file__).resolve().parents[1]
    if not (root.parent/'data/raw/LEADWAY/YOA/2024').exists():
        pytest.skip('Private sources unavailable')
    code='''
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from openpyxl import load_workbook
from demo_app import engine,store
from src.domain.cre_cleaner.adapters import leadway
assert Path(leadway.__file__).resolve().is_relative_to(Path('demo_app/_backend').resolve())
p=next(p for p in store.partners() if p['name']=='LEADWAY(YOA)')
source=Path('../data/raw/LEADWAY/YOA/2024')
uploads=[(p.name,p.read_bytes()) for p in sorted(source.iterdir()) if p.suffix.lower() in {'.xls','.xlsx'}]
engine._persist=lambda files: []
before=store.CONFIRMATIONS_PATH.read_bytes()
kw=dict(partner_id=p['partner_id'],year=2024,quarter=None,bordereau_type='autodetect',delivery='separate')
try:
    engine.clean(uploads,**kw)
    raise AssertionError('Conflicts should require review')
except engine.NeedsReview as review:
    entries=review.unresolved
assert entries and all(e['rows'] for e in entries)
mappings=[]
for e in entries:
    for r in e['rows']:
        evidence=r['resolution']
        assert evidence['expected_class']=='Terrorism & PVT'
        assert {'Fire','Terrorism & PVT'} <= set(evidence['candidates'])
        mappings.append(dict(row_ids=[r['row_id']],class_name='Fire'))
out=engine.clean(uploads,row_mappings=mappings,**kw)
assert out.files
found=0
for f in out.files:
    if f.name.endswith('.zip'):
        z=ZipFile(BytesIO(f.data));books=[z.read(n) for n in z.namelist() if n.endswith('.xlsx')]
    elif f.name.endswith('.xlsx'):books=[f.data]
    else:continue
    for b in books:
        w=load_workbook(BytesIO(b),read_only=True)
        found+='Fire - PREMIUM' in w.sheetnames
        w.close()
assert found==4,found
assert store.CONFIRMATIONS_PATH.read_bytes()==before
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=root,env=dict(os.environ),capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
