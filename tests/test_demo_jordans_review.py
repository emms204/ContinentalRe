from streamlit.testing.v1 import AppTest


def test_jordans_conflict_shows_cover_suggestion_and_record_choices_without_alias_store(monkeypatch):
    from demo_app.screens import run
    sent=[]
    monkeypatch.setattr(run,'keep_alias',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No conflict alias')))
    monkeypatch.setattr(run,'_run',lambda *a,**kw:sent.append(kw))
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='CORNERSTONE(JORDANS)')
_review(dict(coverage=[],warnings=[],unresolved=[dict(label='CLASS CONFLICT',file='q3.xls',sheet='premium',records=1,rows=[dict(row_id='j1',source_row=13,insured='Example',policy_no='P1',resolution=dict(rule='conflicting_source_classes',section_class='Fire',section_label='FIRE & ALLIED PERILS',subclass_label='FIRE, CONSEQUENTIAL LOSS & HOUSEHOLDER INSURANCE',row_class='PERFORMANCE BOND',product_class='Bond',candidates=['Bond','Fire']))])]),p['partner_id'],2024,3,'autodetect','separate')
''').run()
    assert not app.exception
    assert app.selectbox[0].value=='Select class…'
    assert {'Bond','Fire'}<=set(app.selectbox[0].options)
    assert not app.selectbox[0].disabled
    assert any('Record-cover suggestion: Bond' in c.value for c in app.caption)
    assert any('Source subclass heading:' in c.value for c in app.caption)
    app.selectbox[0].select('Bond').run();app.button[0].click().run()
    assert not app.exception
    assert sent[-1]['row_mappings'][0]['row_ids']==['j1']
    assert sent[-1]['row_mappings'][0]['class_name']=='Bond'


def test_record_review_displays_suggestion_without_saved_class_rows(monkeypatch):
    from demo_app.screens import run
    monkeypatch.setattr(run,'approved_class_options',lambda p:[])
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='CORNERSTONE(JORDANS)')
_review(dict(coverage=[],warnings=[],unresolved=[dict(label='Example',file='a.xls',sheet='premium',records=1,suggestion={'class':'General Accident'},rows=[dict(row_id='j2',source_row=5,insured='Example',policy_no='P2')])]),p['partner_id'],2024,2,'autodetect','separate')
''').run()
    assert not app.exception
    assert app.selectbox[0].value=='General Accident'
    assert not app.selectbox[0].disabled
    assert any('Suggestion: General Accident' in c.value for c in app.caption)


def test_demo_engine_conflicts_require_record_choice_without_alias_writes(monkeypatch):
    from io import BytesIO
    from openpyxl import Workbook,load_workbook
    from datetime import datetime
    import pytest
    from demo_app import engine,store
    from src.domain.cre_cleaner.adapters.cornerstone_jordans import HEADER
    partner=next(p for p in store.partners() if p['name']=='CORNERSTONE(JORDANS)')
    w=Workbook();s=w.active;s.title='Premium'
    s.append(['CLASS:',None,None,'FIRE']);s.append(['SUBCLASS:',None,None,'FIRE'])
    s.append(HEADER)
    s.append(['LAGOS','PERFORMANCE BOND','DN100-24010001','P1','Example','LAGOS','BROKERS',10000,None,None,100,
        datetime(2024,1,1),datetime(2024,12,31),40,4000,40,60,6000,60,None,None,None])
    b=BytesIO();w.save(b);w.close()
    uploads=[('Q3 2024.xlsx',b.getvalue())]
    monkeypatch.setattr(engine,'_persist',lambda files:[])
    before=store.CONFIRMATIONS_PATH.read_bytes()
    kw=dict(partner_id=partner['partner_id'],year=2024,quarter=3,bordereau_type='premium',delivery='separate')
    with pytest.raises(engine.NeedsReview) as blocked:engine.clean(uploads,**kw)
    row=blocked.value.unresolved[0]['rows'][0]
    assert row['resolution']['candidates']==['Bond','Fire']
    out=engine.clean(uploads,row_mappings=[dict(row_ids=[row['row_id']],class_name='Bond')],**kw)
    w=load_workbook(BytesIO(out.files[0].data),read_only=True)
    assert 'Bond - PREMIUM' in w.sheetnames
    w.close()
    assert store.CONFIRMATIONS_PATH.read_bytes()==before
