"""Processes a folder of e-invoices: parse, validate, find duplicates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .model import ZERO, Invoice, Issue
from .parse import UnreadableInvoice, parse_file
from .validate import ERROR, find_duplicates, validate


@dataclass
class SupplierTotal:
    name: str
    vat: str
    invoices: int = 0
    net: Decimal = ZERO
    tax: Decimal = ZERO
    gross: Decimal = ZERO


@dataclass
class Batch:
    invoices: list[Invoice] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def issues_for(self, source_file: str) -> list[Issue]:
        return [i for i in self.issues if i.source_file == source_file]

    def status(self, inv: Invoice) -> str:
        severities = {i.severity for i in self.issues_for(inv.source_file)}
        return "error" if ERROR in severities else "warning" if severities else "ok"

    @property
    def counted(self) -> list[Invoice]:
        """Invoices that count towards totals: duplicates are left out so nothing is paid twice."""
        duplicates = {i.source_file for i in self.issues if i.code == "DUPLICATE_INVOICE"}
        return [inv for inv in self.invoices if inv.source_file not in duplicates]

    @property
    def total_due(self) -> Decimal:
        return sum((inv.sign * (inv.payable or ZERO) for inv in self.counted), ZERO)

    @property
    def suppliers(self) -> list[SupplierTotal]:
        totals: dict[str, SupplierTotal] = {}
        for inv in self.counted:
            key = inv.seller_vat or inv.seller_name
            t = totals.setdefault(key, SupplierTotal(inv.seller_name, inv.seller_vat))
            t.invoices += 1
            t.net += inv.sign * (inv.tax_exclusive or ZERO)
            t.tax += inv.sign * (inv.tax_total or ZERO)
            t.gross += inv.sign * (inv.payable or ZERO)
        return sorted(totals.values(), key=lambda t: -t.gross)

    @property
    def vat_by_rate(self) -> list[tuple[str, Decimal, Decimal, Decimal]]:
        acc: dict[tuple[str, Decimal], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
        for inv in self.counted:
            for v in inv.vat:
                acc[(v.category, v.rate)][0] += inv.sign * v.taxable
                acc[(v.category, v.rate)][1] += inv.sign * v.tax
        return [(cat, rate, base, tax) for (cat, rate), (base, tax) in sorted(acc.items(), key=lambda kv: -kv[0][1])]


def process(paths: list[Path]) -> Batch:
    batch = Batch()
    for path in sorted(paths):
        try:
            inv = parse_file(path)
        except UnreadableInvoice as exc:
            batch.unreadable.append(path.name)
            batch.issues.append(Issue(path.name, "UNREADABLE_FILE", ERROR, str(exc)))
            continue
        batch.invoices.append(inv)
        batch.issues.extend(validate(inv))
    batch.issues.extend(find_duplicates(batch.invoices))
    order = {"error": 0, "warning": 1}
    batch.issues.sort(key=lambda i: (order[i.severity], i.source_file))
    return batch


def collect(inputs: list[str | Path]) -> list[Path]:
    """Accepts files and folders; folders are searched for *.xml."""
    paths: list[Path] = []
    for item in map(Path, inputs):
        if item.is_dir():
            paths.extend(sorted(item.rglob("*.xml")))
        elif item.exists():
            paths.append(item)
        else:
            raise FileNotFoundError(f"Not found: {item}")
    return paths
