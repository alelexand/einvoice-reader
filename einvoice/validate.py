"""A practical subset of the EN 16931 business rules, plus checks that matter to accounts payable.

This is not a full schematron validation. It covers the arithmetic and mandatory-field
rules that catch the errors seen most often in practice, and it explains each problem in
plain language with the official rule id.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from .model import ZERO, Invoice, Issue

ERROR, WARNING = "error", "warning"
CENT = Decimal("0.01")
TOLERANCE = Decimal("0.01")

# EU VAT identifiers: country prefix followed by 2 to 13 letters/digits (EL is used for Greece).
VAT_RE = re.compile(r"^(AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|XI)[0-9A-Z+*.]{2,13}$")

REQUIRED = [
    ("number", "BR-02", "Invoice number"),
    ("issue_date", "BR-03", "Issue date"),
    ("currency", "BR-05", "Currency"),
    ("seller_name", "BR-06", "Seller name"),
    ("buyer_name", "BR-07", "Buyer name"),
    ("line_total", "BR-12", "Sum of line net amounts"),
    ("tax_exclusive", "BR-13", "Total without VAT"),
    ("tax_inclusive", "BR-14", "Total with VAT"),
    ("payable", "BR-15", "Amount due"),
]


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _differs(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(a - b) > TOLERANCE


def validate(inv: Invoice) -> list[Issue]:
    issues: list[Issue] = []

    def add(code: str, severity: str, message: str, rule: str = "") -> None:
        issues.append(Issue(inv.source_file, code, severity, message, rule, inv.number))

    for attr, rule, label in REQUIRED:
        if getattr(inv, attr) in (None, ""):
            add("MISSING_FIELD", ERROR, f"{label} is missing", rule)
    if not inv.lines:
        add("MISSING_FIELD", ERROR, "The invoice has no lines", "BR-16")

    lines_sum = sum((l.net_amount for l in inv.lines), ZERO)
    if _differs(lines_sum, inv.line_total):
        add("LINE_SUM_MISMATCH", ERROR,
            f"Lines add up to {lines_sum:,.2f} but the invoice states {inv.line_total:,.2f}", "BR-CO-10")

    for line in inv.lines:
        expected = _money(line.quantity * line.unit_price)
        if abs(expected - line.net_amount) > TOLERANCE:
            add("LINE_AMOUNT_MISMATCH", WARNING,
                f"Line {line.id}: {line.quantity} x {line.unit_price} = {expected:,.2f}, "
                f"but the line amount is {line.net_amount:,.2f}", "PEPPOL-EN16931-R120")

    if inv.line_total is not None and inv.tax_exclusive is not None:
        expected = inv.line_total - inv.allowance_total + inv.charge_total
        if _differs(expected, inv.tax_exclusive):
            add("TOTAL_MISMATCH", ERROR,
                f"Total without VAT should be {expected:,.2f} (lines - allowances + charges), "
                f"invoice states {inv.tax_exclusive:,.2f}", "BR-CO-13")

    vat_sum = sum((v.tax for v in inv.vat), ZERO)
    if inv.vat and _differs(vat_sum, inv.tax_total):
        add("VAT_TOTAL_MISMATCH", ERROR,
            f"VAT breakdown adds up to {vat_sum:,.2f} but total VAT is {inv.tax_total:,.2f}", "BR-CO-14")

    for v in inv.vat:
        expected = _money(v.taxable * v.rate / 100)
        if abs(expected - v.tax) > TOLERANCE:
            add("VAT_CALCULATION", ERROR,
                f"VAT {v.category} {v.rate}% on {v.taxable:,.2f} should be {expected:,.2f}, invoice states {v.tax:,.2f}",
                "BR-S-09")

    taxable_by_rate: dict[Decimal, Decimal] = defaultdict(lambda: ZERO)
    for line in inv.lines:
        taxable_by_rate[line.vat_rate] += line.net_amount
    for v in inv.vat:
        if inv.allowance_total == 0 and inv.charge_total == 0 and _differs(taxable_by_rate.get(v.rate, ZERO), v.taxable):
            add("VAT_BASE_MISMATCH", ERROR,
                f"Lines at {v.rate}% add up to {taxable_by_rate.get(v.rate, ZERO):,.2f} "
                f"but the VAT base for that rate is {v.taxable:,.2f}", "BR-S-08")

    if inv.tax_exclusive is not None and inv.tax_total is not None and inv.tax_inclusive is not None:
        expected = inv.tax_exclusive + inv.tax_total
        if _differs(expected, inv.tax_inclusive):
            add("TOTAL_MISMATCH", ERROR,
                f"Total with VAT should be {expected:,.2f}, invoice states {inv.tax_inclusive:,.2f}", "BR-CO-15")

    if inv.tax_inclusive is not None and inv.payable is not None:
        expected = inv.tax_inclusive - inv.prepaid
        if _differs(expected, inv.payable):
            add("PAYABLE_MISMATCH", ERROR,
                f"Amount due should be {expected:,.2f}, invoice states {inv.payable:,.2f}", "BR-CO-16")

    for role, vat in (("Seller", inv.seller_vat), ("Buyer", inv.buyer_vat)):
        if vat and not VAT_RE.match(vat.replace(" ", "").upper()):
            add("INVALID_VAT_NUMBER", WARNING, f"{role} VAT number {vat!r} is not in EU format", "BR-CO-09")
    if not inv.seller_vat:
        add("MISSING_FIELD", WARNING, "Seller VAT number is missing", "BR-S-02")

    if inv.issue_date and inv.due_date and inv.due_date < inv.issue_date:
        add("DUE_BEFORE_ISSUE", WARNING, f"Due date {inv.due_date} is before the issue date {inv.issue_date}")

    return issues


def find_duplicates(invoices: list[Invoice]) -> list[Issue]:
    """The same seller and invoice number in more than one file: the classic double payment."""
    seen: dict[tuple[str, str, str], str] = {}
    issues = []
    for inv in invoices:
        if not inv.number:
            continue
        key = (*inv.key, inv.doc_type)
        if key in seen:
            issues.append(Issue(inv.source_file, "DUPLICATE_INVOICE", ERROR,
                                f"Invoice {inv.number} from {inv.seller_name} was already read from {seen[key]}",
                                "", inv.number))
        else:
            seen[key] = inv.source_file
    return issues
