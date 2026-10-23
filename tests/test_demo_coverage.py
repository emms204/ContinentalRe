from streamlit.testing.v1 import AppTest


def test_availability_is_visible_before_download_and_keeps_zero_reason():
    app=AppTest.from_string('''
from types import SimpleNamespace
from demo_app.screens.run import _downloads
rows=[dict(type='premium',label='Premium',rows=5,status='cleaned',reason='Rows included',year=2023,quarter=1),
      dict(type='claims',label='Claims (paid)',rows=0,status='not_detected',reason='No source table identified',year=2023,quarter=1),
      dict(type='outstanding',label='Outstanding',rows=0,status='failed',reason='Could not detect header',year=2023,quarter=1)]
_downloads(SimpleNamespace(coverage=rows,files=[SimpleNamespace(name='cleaned.xlsx',data=b'file')],
                          warnings=[],ignored=[],ignored_files=[]))
''').run()
    assert not app.exception
    assert [m.value for m in app.metric]==['5','0','0']
    table=app.dataframe[0].value
    assert list(table['Status'])==['Rows published','Not detected','Failed']
    assert 'header' in table.iloc[2]['Reason']
    assert app.subheader[0].value=='Cleaning results'


def test_group_failure_is_included_in_report_when_other_quarters_export(monkeypatch, tmp_path):
    from types import SimpleNamespace as NS
    from demo_app import engine,store
    from src.domain.cre_cleaner.models import PipelineResult,SourceAuditRecord
    output=tmp_path/'cleaned.xlsx'
    output.write_bytes(b'workbook')
    good=PipelineResult(source_audit=[SourceAuditRecord('source.xlsx','PREM','premium',1,3,3,0)],
                        outputs=[dict(output_path=str(output),premium_rows=3,claims_rows=0,outstanding_rows=0)])
    groups=[NS(year=2023,quarter=1),NS(year=2023,quarter=2)]
    plan=NS(groups=groups,pending=[])
    monkeypatch.setattr(engine,'plan_batch',lambda *a,**k:plan)
    batch=NS(groups=[NS(group=groups[0],result=good,errors=[]),
                     NS(group=groups[1],result=None,errors=['Workbook could not be read'])],
             deliverable_outputs=lambda:good.outputs)
    monkeypatch.setattr(engine,'run_batch',lambda *a,**k:batch)
    monkeypatch.setattr(engine,'upload_files',lambda files:[output])
    monkeypatch.setattr(engine,'_persist',lambda files:[])
    partner=next(p for p in store.partners() if p['name']=='ROYAL EXCHANGE(AGRIC)')
    result=engine.clean([('a.xlsx',b'file'),('b.xlsx',b'file')],partner_id=partner['partner_id'],
                        year=2023,quarter=None,bordereau_type='autodetect',delivery='separate')
    assert len(result.coverage)==6
    failed=[r for r in result.coverage if r['quarter']==2]
    assert all(r['status']=='failed' and 'could not be read' in r['reason'] for r in failed)
    assert result.coverage[0]['rows']==3


def test_outstanding_source_conflict_is_prominent_before_download():
    app=AppTest.from_string('''
from demo_app.coverage_ui import render_coverage
render_coverage([dict(type='outstanding',label='Outstanding',rows=6,status='partial',
    year=2022,quarter=4,reason='Choose the controlling file',
    details=[dict(code='lasaco_outstanding_source_conflict',file='a.xlsx',sheet='Bond',detail='100000 versus 10000')])])
''').run()
    assert not app.exception
    assert len(app.warning)==1
    assert '2022 Q4' in app.warning[0].value
    assert 'controlling file' in app.warning[0].value
    assert 'Both source versions' in app.warning[0].value
