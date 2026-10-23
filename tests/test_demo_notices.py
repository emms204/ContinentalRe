"""Large diagnostic batches stay compact without discarding source evidence."""
import csv
from io import StringIO
from types import SimpleNamespace

from demo_app.notices import grouped_rows, notice_rows, notices_csv
from demo_app.engine import _result_warns


def test_grouping_preserves_different_locations_and_csv_occurrences():
    warnings = [dict(code="unmapped_headers", detail="ENDORSMENT NO", file="a.xlsx", sheet="Fire", row=2)] * 40
    warnings += [dict(warnings[0], sheet="Engineering")]
    rows = notice_rows(warnings)
    grouped = grouped_rows(rows)
    assert len(grouped) == 2
    assert [row["Occurrences"] for row in grouped] == [40, 1]
    exported = list(csv.DictReader(StringIO(notices_csv(rows).decode("utf-8-sig"))))
    assert exported == rows


def test_warning_extraction_retains_source_location():
    rec = SimpleNamespace(severity="WARN", reason="unmapped_headers", detail="ENDORSMENT NO",
                          source_filename="a.xlsx", source_sheet="Fire", source_row=12)
    assert _result_warns(SimpleNamespace(exceptions=[rec])) == [{
        "code": "unmapped_headers", "detail": "ENDORSMENT NO", "file": "a.xlsx", "sheet": "Fire", "row": 12,
    }]


def test_csv_does_not_execute_workbook_controlled_formulas():
    rows = notice_rows([dict(file="=1+1.xlsx", detail="@SUM(A1)")])
    exported = list(csv.DictReader(StringIO(notices_csv(rows).decode("utf-8-sig"))))
    assert exported[0]["File"] == "'=1+1.xlsx"
    assert exported[0]["Detail"] == "'@SUM(A1)"


def test_output_ui_keeps_downloads_ahead_of_collapsed_diagnostics():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from types import SimpleNamespace
from demo_app.screens.run import _downloads
warnings = [dict(code="unmapped_headers",detail="Unmapped headers: ENDORSMENT NO",file="a.xlsx",sheet="Fire")] * 120
warnings += [dict(code="claims_file_ignored",detail="Wrong year; claims excluded",file="2022.xlsx")]
_downloads(SimpleNamespace(files=[SimpleNamespace(name="cleaned.xlsx",data=b"workbook")],
    warnings=warnings, ignored=[], ignored_files=[dict(file="2022.xlsx",reason="year_mismatch",detail="2022 differs from 2021")]))
''').run()
    assert not app.exception
    assert len(app.warning) == 1
    assert not app.error
    assert any("1 include claims" in item.value for item in app.caption)
    assert len(app.expander) == 3
    assert all(not item.proto.expanded for item in app.expander)
    assert app.get("download_button")[0].label == "Download cleaned.xlsx"
    assert app.dataframe[-1].value.iloc[0]["Occurrences"] == 120
