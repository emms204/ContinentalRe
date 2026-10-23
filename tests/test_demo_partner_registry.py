"""Registry adapters must be selectable without inventing shared partner IDs."""
import hashlib
import json
from pathlib import Path

from demo_app import engine, store
from src.domain.cre_cleaner.adapters import get_adapter, list_adapters


def pair(row):
    adapter=get_adapter(*store.partner_name_to_cedant_broker(row['name']))
    return adapter.cedant,adapter.broker


def test_every_registered_adapter_is_available_without_snapshot_writes():
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in store.SNAPSHOT_DIR.rglob('*') if p.is_file()}
    rows=store.partners()
    assert {pair(r) for r in rows}=={(c,b) for c,b,*_ in list_adapters()}
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert len({r['partner_id'] for r in rows})==len(rows)


def test_existing_ids_and_aliases_are_preserved_and_missing_ids_are_stable(monkeypatch,tmp_path):
    existing=dict(partner_id='original-id',name='LASACO(FEYBILL)')
    monkeypatch.setattr(store,'PARTNERS_PATH',tmp_path/'partners.json')
    store.PARTNERS_PATH.write_text(json.dumps([existing]))
    first=store.partners();second=store.partners()
    assert first==second and next(r for r in first if r['partner_id']=='original-id')==existing
    assert len([r for r in first if pair(r)==('LASACO','FEYBIL')])==1
    added=[r for r in first if r.get('demo_only')]
    assert len(added)==len(list_adapters())-1
    assert all(r['partner_id'].startswith('demo-') for r in added)
    assert all(store.partner_by_id(r['partner_id'])==r for r in added)


def test_missing_snapshot_can_still_test_every_registered_adapter(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'PARTNERS_PATH',tmp_path/'missing.json')
    rows=store.partners()
    assert len(rows)==len(list_adapters()) and all(r['demo_only'] for r in rows)


def test_registry_partner_can_clean_through_demo_engine_without_persisting(monkeypatch,tmp_path):
    from io import BytesIO
    from datetime import datetime
    from openpyxl import Workbook,load_workbook
    monkeypatch.setattr(store,'PARTNERS_PATH',tmp_path/'missing.json')
    partner=next(p for p in store.partners() if p['name']=='LINKAGE(AGRIC)')
    book=Workbook();sheet=book.active;sheet.title='AGRICULTURE PREMIUM Q1 2024'
    sheet.append(['Policy No','Insured','Class','Start Date','End Date','Sum Insured','Gross Premium',
                  'Retention PPN','Retention SI','Retention Premium','Treaty PPN','Treaty SI','Treaty Premium'])
    sheet.append(['P1','Example','Agriculture',datetime(2024,1,1),datetime(2024,12,31),1000,100,40,400,40,60,600,60])
    data=BytesIO();book.save(data);book.close()
    monkeypatch.setattr(engine,'_persist',lambda files:[])
    result=engine.clean([('Q1 2024.xlsx',data.getvalue())],partner_id=partner['partner_id'],year=2024,
                        quarter=1,bordereau_type='premium',delivery='separate')
    assert result.files
    cleaned=load_workbook(BytesIO(result.files[0].data),read_only=True,data_only=True)
    assert 'Agriculture - PREMIUM' in cleaned.sheetnames
    cleaned.close()


def test_registry_entries_are_visible_in_the_partner_picker():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'demo_app/app.py'))
    app.session_state['demo_authed']=True
    app.run()
    assert not app.exception
    options=app.selectbox[0].options
    for name in ['LINKAGE(AGRIC)','EMPLE(SCIB)','NSIA(ARK)','SUNU(UAIB)','ZENITH(ARK)']:
        assert any(name in option for option in options),name
    assert any('demo' in option.lower() for option in options if 'LINKAGE(AGRIC)' in option)
