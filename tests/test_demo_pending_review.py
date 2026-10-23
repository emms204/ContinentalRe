from streamlit.testing.v1 import AppTest
from demo_app import engine,store

def test_heirs_hib_has_local_marine_and_ga_choices():
    p=next(p for p in store.partners() if p['name']=='HEIRS(HIB)')
    assert {'Marine Hull','Marine Cargo','General Accident'} <= {o['class_name'] for o in engine.approved_class_options(p['partner_id'])}


def test_pending_batch_does_not_claim_rows_are_downloadable():
    app=AppTest.from_string('''
from demo_app.coverage_ui import render_coverage
render_coverage([dict(type='premium',label='Premium',rows=947,status='cleaned',reason='Rows included',year=2025,quarter=2)], pending=True)
''').run()
    assert not app.exception
    assert app.subheader[0].value=='Class review required'
    table=app.dataframe[0].value
    assert 'Published rows' not in table.columns
    assert 'awaiting' in table.iloc[0]['Status'].lower()


def test_marine_review_uses_independent_record_choices_and_no_default_md_class():
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='HEIRS(HIB)')
entries=[dict(label='MARINE',file='a.xlsx',sheet='claims',records=2,rows=[
 dict(row_id='cargo',source_row=5,description='MARINE CARGO DAMAGE',suggestion={'class':'Marine Cargo'}),
 dict(row_id='hull',source_row=6,description='VESSEL DAMAGE',suggestion={'class':'Marine Hull'})]),
 dict(label='MD/BI',file='a.xlsx',sheet='prem',records=3)]
_review(dict(unresolved=entries,coverage=[],warnings=[]),p['partner_id'],2025,None,'autodetect','separate')
''').run()
    assert not app.exception
    assert [s.value for s in app.selectbox]==['Marine Cargo','Marine Hull','Select class…']
    assert [s.label for s in app.selectbox] == ['Map MARINE row 5 to', 'Map MARINE row 6 to', 'Map MD/BI to']
    assert app.radio[-1].label == 'Action for MD/BI'
    assert app.radio[-1].options == ['Map these records', 'Exclude these records']
    assert any('Sheet: prem · 3 affected records' in c.value for c in app.caption)
    app.radio[-1].set_value('ignore').run()
    assert app.selectbox[-1].disabled
    assert any('3 MD/BI records in this sheet will be excluded' in c.value for c in app.caption)


def test_record_confirmations_do_not_create_global_marine_alias(monkeypatch):
    from io import BytesIO
    from openpyxl import Workbook,load_workbook
    import pytest
    p=next(p for p in store.partners() if p['name']=='HEIRS(HIB)')
    def workbook(amount=100):
        w=Workbook();s=w.active;s.title='CLAIMS PAID'
        s.append(['CLASS','Insured Name','Policy No','Claim No','Start Date','End Date','Total Claims','Description of loss'])
        s.append(['MARINE','Example Cargo','P1','C1','01-Jan-2025','31-Dec-2025',amount,'MARINE CARGO DAMAGE'])
        s.append(['MARINE','Example Hull','P2','C2','01-Jan-2025','31-Dec-2025',200,'LOSS OF VESSEL'])
        b=BytesIO();w.save(b);w.close();return b.getvalue()
    monkeypatch.setattr(engine,'_persist',lambda files:[])
    kwargs=dict(partner_id=p['partner_id'],year=2025,quarter=1,bordereau_type='claims',delivery='separate')
    original = workbook()
    with pytest.raises(engine.NeedsReview) as initial:
        engine.clean([('Q1 2025.xlsx',original)],**kwargs)
    rows=[r for e in initial.value.unresolved for r in e['rows']]
    assert len(rows)==2
    decisions=[dict(row_ids=[r['row_id']],class_name=r['suggestion']['class']) for r in rows]
    aliases_before=store.CONFIRMATIONS_PATH.read_bytes()
    out=engine.clean([('Q1 2025.xlsx',original)],row_mappings=decisions,**kwargs)
    b=load_workbook(BytesIO(out.files[0].data),read_only=True,data_only=True)
    assert {'Marine Cargo - CLAIMS','Marine Hull - CLAIMS'} <= set(b.sheetnames)
    b.close()
    assert store.CONFIRMATIONS_PATH.read_bytes()==aliases_before
    with pytest.raises(engine.CleanError):
        engine.clean([('Q1 2025.xlsx',workbook(101))],row_mappings=decisions,**kwargs)


def test_conflict_review_displays_evidence_and_submits_records_without_alias(monkeypatch):
    from demo_app.screens import run
    sent=[]
    monkeypatch.setattr(run, 'keep_alias', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Conflict alias must not be saved')))
    monkeypatch.setattr(run, '_run', lambda *a, **kw: sent.append(kw))
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='HEIRS(JOMOLA)')
_review(dict(coverage=[],warnings=[],unresolved=[dict(label='CLASS CONFLICT',file='a.xlsx',sheet='Premium',records=1,requires_row_resolution=True,rows=[dict(row_id='r126',source_row=126,policy_no='P1',insured='Example Ltd',raw_class='FIRE',resolution=dict(rule='conflicting_source_classes',section_class='Engineering',section_label='ENGINEERING (UW 2021)',row_class='FIRE',candidates=['Engineering','Fire']))])]),p['partner_id'],2021,4,'autodetect','separate')
''').run()
    assert not app.exception
    assert any('Section says Engineering; row says FIRE' in m.value for m in app.markdown)
    assert app.selectbox[0].value == 'Select class…'
    assert {'Engineering', 'Fire'} <= set(app.selectbox[0].options)
    app.selectbox[0].select('Fire').run()
    app.button[0].click().run()
    assert not app.exception
    assert sent[-1]['row_mappings'][0]['row_ids'] == ['r126']
    assert sent[-1]['row_mappings'][0]['class_name'] == 'Fire'


def test_missing_snapshot_classes_use_record_mapping_without_saving_alias(monkeypatch):
    from demo_app.screens import run
    sent=[]
    monkeypatch.setattr(run,'approved_class_options',lambda pid:[])
    monkeypatch.setattr(run,'keep_alias',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No alias store row exists')))
    monkeypatch.setattr(run,'_run',lambda *a,**kw:sent.append(kw))
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='HEIRS(JOMOLA)')
_review(dict(coverage=[],warnings=[],unresolved=[dict(label='PACKAGE POLICY',file='a.xlsx',sheet='Claims',records=1,rows=[dict(row_id='r32',source_row=32,policy_no='P1',insured='Example Ltd',raw_class='PACKAGE POLICY',resolution={})])]),p['partner_id'],2024,2,'autodetect','separate')
''').run()
    assert not app.exception
    assert app.selectbox[0].value=='Select class…'
    assert 'General Accident' in app.selectbox[0].options
    app.selectbox[0].select('General Accident').run()
    app.button[0].click().run()
    assert not app.exception
    assert sent[-1]['row_mappings'][0]['row_ids']==['r32']


def test_correction_ledger_does_not_double_count_repeated_parser_diagnostics():
    app=AppTest.from_string('''
import json
from demo_app.screens.run import _render_corrections
item=dict(source_row=40,file='a.xlsx',sheet='Claims',role='prior_cession_reversal',**{'class':'Marine Hull'},insured='Example',claim_no='C1',policy_no='P1',recovery_adjustment=-120)
w=dict(code='heirs_correction_record',detail=json.dumps(item))
_render_corrections([w,w])
''').run()
    assert not app.exception
    assert len(app.dataframe[0].value)==1


def test_outside_single_class_goes_to_class_review():
    from types import SimpleNamespace
    entry=dict(label='FIRE',file='a.xlsx',sheet='prem',records=4,actions=['keep','ignore'])
    result=SimpleNamespace(summary=dict(error='class_outside_single_class',detail='x',unresolved=[entry]),exceptions=[])
    assert engine._unresolved(result)==[entry]
    assert engine._pipeline_error(result) is None
