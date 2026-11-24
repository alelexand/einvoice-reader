"""Excel workbook: Invoices, Lines, VAT, Suppliers and Issues."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .batch import Batch

HEADER_FILL = PatternFill("solid", fgColor="1E3A8A")
FILLS = {"error": PatternFill("solid", fgColor="FDE2E1"), "warning": PatternFill("solid", fgColor="FEF3C7"),
         "ok": PatternFill("solid", fgColor="DCFCE7")}
MONEY = '#,##0.00;[Red]-#,##0.00'


def _sheet(ws: Worksheet, headers: list[str], rows: list[list], widths: list[int], money_cols: tuple[int, ...] = ()) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, Font(bold=True, color="FFFFFF")
    for row in rows:
        ws.append(row)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in money_cols:
            row[c].number_format = MONEY
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def write_excel(batch: Batch, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    ws = wb.active
    ws.title = "Invoices"
    _sheet(ws, ["Status", "File", "Syntax", "Type", "Number", "Issue date", "Due date", "Seller", "Seller VAT",
                "Buyer", "Currency", "Net", "VAT", "Total due", "Issues"],
           [[batch.status(i).upper(), i.source_file, i.syntax, i.doc_type.replace("_", " "), i.number, i.issue_date,
             i.due_date, i.seller_name, i.seller_vat, i.buyer_name, i.currency,
             i.sign * (i.tax_exclusive or 0), i.sign * (i.tax_total or 0), i.sign * (i.payable or 0),
             len(batch.issues_for(i.source_file))] for i in batch.invoices],
           [10, 36, 7, 12, 15, 11, 11, 26, 17, 22, 9, 12, 11, 12, 7], (11, 12, 13))
    for row in ws.iter_rows(min_row=2):
        row[0].fill = FILLS[row[0].value.lower()]
        for c in (5, 6):
            row[c].number_format = "yyyy-mm-dd"

    _sheet(wb.create_sheet("Lines"), ["File", "Invoice", "Seller", "Line", "Description", "Quantity", "Unit",
                                      "Unit price", "Net", "VAT cat.", "VAT %"],
           [[i.source_file, i.number, i.seller_name, l.id, l.description, l.quantity, l.unit_code, l.unit_price,
             i.sign * l.net_amount, l.vat_category, l.vat_rate] for i in batch.invoices for l in i.lines],
           [36, 15, 26, 6, 44, 9, 7, 11, 12, 9, 7], (7, 8))

    _sheet(wb.create_sheet("VAT"), ["VAT category", "Rate %", "Taxable", "VAT"],
           [[cat, rate, base, tax] for cat, rate, base, tax in batch.vat_by_rate], [13, 9, 14, 12], (2, 3))

    _sheet(wb.create_sheet("Suppliers"), ["Supplier", "VAT number", "Documents", "Net", "VAT", "Total"],
           [[s.name, s.vat, s.invoices, s.net, s.tax, s.gross] for s in batch.suppliers],
           [28, 18, 11, 13, 12, 13], (3, 4, 5))

    issues = wb.create_sheet("Issues")
    _sheet(issues, ["Severity", "File", "Invoice", "Issue", "Rule", "Details"],
           [[i.severity.upper(), i.source_file, i.invoice_number, i.code, i.rule, i.message] for i in batch.issues],
           [10, 36, 15, 22, 22, 90])
    for row in issues.iter_rows(min_row=2):
        for cell in row:
            cell.fill = FILLS[row[0].value.lower()]

    wb.save(path)
    return path
