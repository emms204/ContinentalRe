"""All registered partners have truthful, usable demo class choices."""
from demo_app import engine,store
from src.domain.cre_cleaner.adapters import get_adapter,list_adapters
import pytest


def test_all_adapter_partners_have_class_choices_without_snapshot_rows(monkeypatch):
    monkeypatch.setattr(store,'classes_for',lambda pid:[])
    partners=store.partners()
    pairs=set()
    for p in partners:
        a=get_adapter(*store.partner_name_to_cedant_broker(p['name']));pairs.add((a.cedant,a.broker))
        options=engine.approved_class_options(p['partner_id'])
        assert options,p['name']
        assert all(not o['store_class'] for o in options)
        single=a.setting('single_class')
        if single and not getattr(a.class_map(),'allow_record_class_override',False):
            assert [o['class_name'] for o in options]==[single]
    assert pairs=={(c,b) for c,b,*_ in list_adapters()}


def test_registry_choices_do_not_pretend_to_be_saved_alias_rows(monkeypatch):
    monkeypatch.setattr(store,'classes_for',lambda pid:[])
    p=next(p for p in store.partners() if p['name']=='LINKAGE(AGRIC)')
    monkeypatch.setattr(store,'append_alias',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No store write')))
    with pytest.raises(ValueError,match='record'):
        engine.keep_alias(p['partner_id'],'Example','Agriculture',confirmed_by='test')


def test_bond_crop_review_explains_source_and_partner_limit(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from demo_app.screens import run
    sent=[]
    monkeypatch.setattr(run,'keep_alias',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No conflict alias')))
    monkeypatch.setattr(run,'_run',lambda *a,**kw:sent.append(kw))
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='LINKAGE(AGRIC)')
e=dict(rule='conflicting_source_classes',section_class='Bond',section_label='Bond',row_class='MULTI-PERILS CROP INSURANCE',subclass_label='MULTI-PERILS CROP INSURANCE',product_class='Agriculture',candidates=['Agriculture','Bond'])
_review(dict(coverage=[],warnings=[],unresolved=[dict(label='CLASS CONFLICT',file='Q2.xlsx',sheet='Sent Bord.',records=1,expected_class='Agriculture',allows_record_class_override=False,suggestion_reason='conflicting_source_classes',rows=[dict(row_id='crop1',source_row=13,insured='Example',policy_no='P1',resolution=e)])]),p['partner_id'],2022,2,'autodetect','separate')
''').run()
    assert not app.exception
    assert app.selectbox[0].options==['Select class…','Agriculture']
    assert not app.selectbox[0].disabled
    text=' '.join(x.value for x in app.info)+' '.join(x.value for x in app.caption)+' '.join(x.value for x in app.markdown)
    assert 'Bond' in text and 'MULTI-PERILS CROP INSURANCE' in text and 'outside' in text
    app.selectbox[0].select('Agriculture').run();app.button[0].click().run()
    assert sent[-1]['row_mappings']==[dict(row_ids=['crop1'],class_name='Agriculture',file='Q2.xlsx',sheet='Sent Bord.')]


def test_original_bond_crop_source_round_trip_without_alias_write():
    from pathlib import Path
    from io import BytesIO
    from openpyxl import load_workbook
    p=Path(__file__).resolve().parents[2]/'data/raw/LINKAGE/AGRIC/2021_2022/Q2 2021-2022 AGRIC ACCOUNT STATEMENT SEND.xlsx'
    if not p.exists():pytest.skip('Private source unavailable')
    partner=next(p for p in store.partners() if p['name']=='LINKAGE(AGRIC)')
    before=store.CONFIRMATIONS_PATH.read_bytes()
    kw=dict(partner_id=partner['partner_id'],year=2022,quarter=2,bordereau_type='autodetect',delivery='separate')
    uploads=[(p.name,p.read_bytes())]
    from unittest.mock import patch
    with patch.object(engine,'_persist',lambda files:[]):
        with pytest.raises(engine.NeedsReview) as review:
            engine.clean(uploads,**kw)
        entries=review.value.unresolved
        assert len(entries)==1
        entry=entries[0]
        assert entry['requires_row_resolution'] and entry['expected_class']=='Agriculture'
        assert entry['rows'][0]['resolution']['section_class']=='Bond'
        out=engine.clean(uploads,row_mappings=[dict(row_ids=[r['row_id'] for r in entry['rows']],class_name='Agriculture')],**kw)
    assert out.files
    w=load_workbook(BytesIO(out.files[0].data),read_only=True,data_only=True)
    rows=list(w['Agriculture - PREMIUM'].values)
    header=next(r for r in rows if 'GROSS PREMIUM' in r and 'POLICY NO.' in r)
    record=next(r for r in rows if 'AGMC/22/02/63760/ABJ' in r)
    assert record[header.index('GROSS PREMIUM')]==770983.98
    w.close()
    assert store.CONFIRMATIONS_PATH.read_bytes()==before


def test_registry_only_group_selection_maps_record_ids_not_alias(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from demo_app.screens import run
    sent=[]
    monkeypatch.setattr(run,'approved_class_options',lambda pid:[dict(class_name='Fire',store_class='')])
    monkeypatch.setattr(run,'keep_alias',lambda *a,**kw:pytest.fail('Registry-only choice must not save an alias'))
    monkeypatch.setattr(run,'_run',lambda *a,**kw:sent.append(kw))
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='CORNERSTONE(JORDANS)')
_review(dict(coverage=[],warnings=[],unresolved=[dict(label='Unfamiliar cover',file='Q1.xlsx',sheet='Premium',records=2,rows=[dict(row_id='r1'),dict(row_id='r2')])]),p['partner_id'],2024,1,'autodetect','separate')
''').run()
    assert not app.exception
    app.selectbox[0].select('Fire').run();app.button[0].click().run()
    assert not app.exception
    assert sent[-1]['row_mappings']==[dict(row_ids=['r1','r2'],class_name='Fire',file='Q1.xlsx',sheet='Premium')]


def test_later_record_choice_splits_prior_group_without_duplicate_targets(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from demo_app.screens import run
    sent=[]
    monkeypatch.setattr(run,'approved_class_options',lambda pid:[dict(class_name=n,store_class='') for n in ['Fire','General Accident']])
    monkeypatch.setattr(run,'_run',lambda *a,**kw:sent.append(kw))
    app=AppTest.from_string('''
from demo_app.screens.run import _review
from demo_app import store
p=next(p for p in store.partners() if p['name']=='CORNERSTONE(JORDANS)')
_review(dict(coverage=[],warnings=[],row_mappings=[dict(row_ids=['r1','r2'],class_name='Fire',file='Q1.xlsx',sheet='Premium')],unresolved=[dict(label='New evidence',file='Q1.xlsx',sheet='Premium',records=1,rows=[dict(row_id='r2')])]),p['partner_id'],2024,1,'autodetect','separate')
''').run()
    app.selectbox[0].select('General Accident').run();app.button[0].click().run()
    assert not app.exception
    assert sent[-1]['row_mappings']==[
        dict(row_ids=['r1'],class_name='Fire',file='Q1.xlsx',sheet='Premium'),
        dict(row_ids=['r2'],class_name='General Accident',file='Q1.xlsx',sheet='Premium')]
