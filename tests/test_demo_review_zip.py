from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def _workbook(policy):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Fire - PREMIUM'
    sheet.append(['POLICY NO.', 'NAME OF INSURED', 'GROSS PREMIUM'])
    sheet.append([policy, 'Example', 100])
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def _review(path):
    app = AppTest.from_string(f'''
import streamlit as st
from demo_app import engine
from demo_app.screens import review
st.session_state.last_outputs = [{{'output_path': {str(path)!r}}}]
review.render()
''').run()
    assert not app.exception
    return app


def test_zip_workbook_selection_previews_each_quarter_without_extracting_paths(tmp_path):
    path = tmp_path/'quarters.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('Q2.xlsx', _workbook('Q2-POLICY'))
        archive.writestr('../../Q3.xlsx', _workbook('Q3-POLICY'))
        archive.writestr('notes.txt', 'not a workbook')
    app = _review(path)
    assert not app.error
    assert len(app.selectbox[0].options) == 2
    assert app.dataframe[0].value.iloc[0,0] == 'Q2-POLICY'
    app.selectbox[0].select(1).run()
    assert not app.exception and not app.error
    assert app.dataframe[0].value.iloc[0,0] == 'Q3-POLICY'
    assert list(tmp_path.iterdir()) == [path]


def test_plain_workbook_still_previews(tmp_path):
    path = tmp_path/'cleaned.xlsx'
    path.write_bytes(_workbook('P1'))
    app = _review(path)
    assert not app.error
    assert app.dataframe[0].value.iloc[0,0] == 'P1'
    assert app.selectbox[0].label == 'Sheet'


def test_empty_and_invalid_archives_have_friendly_messages(tmp_path):
    path = tmp_path/'empty.zip'
    with ZipFile(path,'w') as archive:
        archive.writestr('notes.txt','No Excel files')
    app = _review(path)
    assert 'no Excel workbooks' in app.info[0].value
    path.write_bytes(b'invalid zip')
    app = _review(path)
    assert 'Could not read this ZIP' in app.error[0].value


def test_navigation_removes_compare_and_recovers_old_selection():
    app = AppTest.from_file(str(ROOT/'demo_app/app.py'))
    app.session_state['demo_authed'] = True
    app.session_state['nav_radio'] = 'Compare'
    app.run()
    assert not app.exception
    assert app.radio[0].options == ['Run','Review']
    assert app.radio[0].value == 'Run'
