"""Planning cache and error/progress boundaries of the Streamlit cleaner."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from demo_app import engine, store
from src.domain.cre_cleaner.io import excel


def _inputs():
    workbook = Workbook()
    workbook.active.append(['Example'])
    data = BytesIO()
    workbook.save(data)
    workbook.close()
    partner = next(p for p in store.partners() if p['name'] == 'ROYAL EXCHANGE(DIRECT)')
    return [('a.xlsx', data.getvalue()), ('b.xlsx', data.getvalue())], dict(
        partner_id=partner['partner_id'], year=2022, quarter=None,
        bordereau_type='autodetect', delivery='separate',
    )


def test_planner_reads_each_file_once_and_releases_cache(monkeypatch):
    calls = []
    original = excel._read_workbook_sheets_uncached
    def read(path):
        calls.append(path.name)
        return original(path)
    def plan(paths, **kwargs):
        for path in paths:
            for _ in range(4):
                rows = excel.read_workbook_sheets(path)
                assert rows['Sheet'][0][0] == 'Example'
                rows['Sheet'][0][0] = 'mutated copy'
        return SimpleNamespace(groups=[], pending=[])
    monkeypatch.setattr(excel, '_read_workbook_sheets_uncached', read)
    monkeypatch.setattr(engine, 'plan_batch', plan)
    uploads, kwargs = _inputs()
    with pytest.raises(engine.CleanError, match='No files matched'):
        engine.clean(uploads, **kwargs)
    assert calls == ['a.xlsx', 'b.xlsx']
    assert excel._READ_CACHE.get() is None


def test_planning_progress_names_each_uploaded_file(monkeypatch):
    def plan(paths, **kwargs):
        for index,path in enumerate(paths,1):kwargs['on_file'](path,index,len(paths))
        return SimpleNamespace(groups=[],pending=[])
    monkeypatch.setattr(engine,'plan_batch',plan)
    uploads,kwargs=_inputs();seen=[]
    with pytest.raises(engine.CleanError,match='No files matched'):
        engine.clean(uploads,on_progress=lambda message,fraction:seen.append((message,fraction)),**kwargs)
    messages=[(m,f) for m,f in seen if m.startswith('Checking file')]
    assert [m for m,f in messages]==['Checking file 1 of 2: a.xlsx','Checking file 2 of 2: b.xlsx']
    assert 0 < messages[0][1] < messages[1][1] < 0.25


def test_native_reader_panic_becomes_actionable_error(monkeypatch):
    panic = type('PanicException', (BaseException,), {'__module__': 'pyo3_runtime'})
    def fail(*args, **kwargs):
        raise panic('SetLoggerError')
    monkeypatch.setattr(engine, 'plan_batch', fail)
    uploads, kwargs = _inputs()
    with pytest.raises(engine.CleanError, match='Restart the Streamlit server'):
        engine.clean(uploads, **kwargs)
    assert excel._READ_CACHE.get() is None


def test_interrupts_are_not_converted_to_reader_errors(monkeypatch):
    def stop(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(engine, 'plan_batch', stop)
    uploads, kwargs = _inputs()
    with pytest.raises(KeyboardInterrupt):
        engine.clean(uploads, **kwargs)


def test_ui_closes_status_and_clears_stale_output_on_reader_failure():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from demo_app.engine import CleanError
from demo_app.screens.run import _run
st.session_state.demo_uploads = [('a.xlsx', b'file')]
st.session_state.demo_outcome = 'stale output'
def fail(*args, **kwargs):
    kwargs['on_progress']('Cleaning 2022 Q2 · group 2 of 4', 0.4)
    raise CleanError('The native Excel reader failed. Restart the Streamlit server.')
with patch('demo_app.screens.run.clean', side_effect=fail):
    _run('partner', 2022, None, 'autodetect', 'separate', None)
''').run()
    assert not app.exception
    assert len(app.error) == 1
    assert 'Restart the Streamlit server' in app.error[0].value
    assert app.status[0].state == 'error'
    assert app.session_state['demo_outcome'] is None
