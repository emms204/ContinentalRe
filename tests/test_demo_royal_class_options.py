import json

from demo_app import engine, store


def test_royal_partners_have_local_mapping_options():
    partners = {p['name']: p for p in store.partners()}
    direct = engine.approved_class_options(partners['ROYAL EXCHANGE(DIRECT)']['partner_id'])
    assert 'General Accident' in {o['class_name'] for o in direct}
    agric = engine.approved_class_options(partners['ROYAL EXCHANGE(AGRIC)']['partner_id'])
    assert [o['class_name'] for o in agric] == ['Agriculture']


def test_manual_confirmation_works_without_writing_shipped_snapshot(monkeypatch, tmp_path):
    rows = json.loads(store.CLASSES_PATH.read_text())
    monkeypatch.setattr(store, 'CLASSES_PATH', tmp_path/'classes.json')
    store.CLASSES_PATH.write_text(json.dumps(rows))
    monkeypatch.setattr(store, 'CONFIRMATIONS_PATH', tmp_path/'confirmations.json')
    monkeypatch.setattr(store, 'AUDIT_PATH', tmp_path/'audit.jsonl')
    partner = next(p for p in store.partners() if p['name']=='ROYAL EXCHANGE(DIRECT)')
    engine.keep_alias(partner['partner_id'], 'Example user class', 'General Accident', confirmed_by='test')
    assert any('Example user class' in r.get('aliases', []) for r in store.classes_for(partner['partner_id']))
