import sqlite3

import pytest
from openpyxl import load_workbook

from einvoice.batch import collect, process
from einvoice.cli import main
from einvoice.report_excel import write_excel
from einvoice.report_html import render_html
from einvoice.sample import generate
from einvoice.store import save


@pytest.fixture
def batch(tmp_path):
    return process(collect([generate(tmp_path / "in")[0].parent]))


def test_batch_overview(batch):
    assert len(batch.invoices) == 8 and len(batch.unreadable) == 2
    assert {i.code for i in batch.issues} == {
        "UNREADABLE_FILE", "VAT_CALCULATION", "DUPLICATE_INVOICE", "LINE_AMOUNT_MISMATCH",
        "INVALID_VAT_NUMBER", "DUE_BEFORE_ISSUE"}
    assert [i.severity for i in batch.issues] == sorted((i.severity for i in batch.issues), key=lambda s: s != "error")


def test_supplier_totals_net_credit_notes(batch):
    nordlicht = next(s for s in batch.suppliers if s.name == "Nordlicht Papier GmbH")
    assert nordlicht.invoices == 2 and str(nordlicht.gross) == "557.90"   # 595.39 - 37.49


def test_duplicates_are_left_out_of_totals(batch):
    atlantico = next(s for s in batch.suppliers if s.name.startswith("Atl"))
    assert atlantico.invoices == 1 and str(atlantico.gross) == "502.86"
    assert str(batch.total_due) == "5112.14"   # 5,615.00 minus the duplicate 502.86


def test_excel_sheets(tmp_path, batch):
    wb = load_workbook(write_excel(batch, tmp_path / "x.xlsx"))
    assert wb.sheetnames == ["Invoices", "Lines", "VAT", "Suppliers", "Issues"]
    assert wb["Invoices"].max_row == 9 and wb["Issues"].max_row == 1 + len(batch.issues)


def test_sqlite_is_idempotent(tmp_path, batch):
    db = tmp_path / "x.sqlite"
    save(batch, db)
    save(batch, db)
    con = sqlite3.connect(db)
    assert con.execute("select count(*) from invoices").fetchone()[0] == 8
    assert con.execute("select count(*) from invoice_lines").fetchone()[0] == 18
    assert con.execute("select count(*) from issues").fetchone()[0] == len(batch.issues)
    assert con.execute("select payable from invoices where doc_type='credit_note'").fetchone()[0] == -37.49


def test_html_escapes_content(batch):
    batch.invoices[0].seller_name = "<img src=x onerror=alert(1)>"
    html = render_html(batch)
    assert "<img src=x" not in html and "&lt;img" in html


def test_cli(tmp_path, capsys):
    data = tmp_path / "in"
    assert main(["sample", "--out", str(data)]) == 0
    assert main(["read", str(data), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "8 invoice(s) read, 2 unreadable, 4 error(s), 3 warning(s)" in out
    assert main(["read", str(data), "--out", str(tmp_path / "out"), "--strict"]) == 1
    assert main(["read", str(tmp_path / "missing")]) == 2
