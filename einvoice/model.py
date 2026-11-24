"""Syntax-independent invoice model, loosely following the EN 16931 semantic model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ZERO = Decimal("0.00")


@dataclass
class Line:
    id: str
    description: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal
    net_amount: Decimal
    vat_category: str
    vat_rate: Decimal


@dataclass
class VatBreakdown:
    category: str
    rate: Decimal
    taxable: Decimal
    tax: Decimal


@dataclass
class Invoice:
    source_file: str
    syntax: str                     # "UBL" or "CII"
    doc_type: str                   # "invoice" or "credit_note"
    number: str = ""
    issue_date: date | None = None
    due_date: date | None = None
    currency: str = ""
    seller_name: str = ""
    seller_vat: str = ""
    buyer_name: str = ""
    buyer_vat: str = ""
    lines: list[Line] = field(default_factory=list)
    vat: list[VatBreakdown] = field(default_factory=list)
    line_total: Decimal | None = None
    allowance_total: Decimal = ZERO
    charge_total: Decimal = ZERO
    tax_exclusive: Decimal | None = None
    tax_total: Decimal | None = None
    tax_inclusive: Decimal | None = None
    prepaid: Decimal = ZERO
    payable: Decimal | None = None

    @property
    def sign(self) -> int:
        """Credit notes reduce what is owed, so totals are reported as negative."""
        return -1 if self.doc_type == "credit_note" else 1

    @property
    def key(self) -> tuple[str, str]:
        return (self.seller_vat or self.seller_name, self.number)


@dataclass(frozen=True)
class Issue:
    source_file: str
    code: str
    severity: str
    message: str
    rule: str = ""
    invoice_number: str = ""
