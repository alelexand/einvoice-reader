"""SQLite export, so the data can be queried, joined with the ledger or loaded into a BI tool.

Re-running on the same files updates the rows instead of duplicating them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .batch import Batch

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    source_file TEXT PRIMARY KEY, syntax TEXT, doc_type TEXT, number TEXT, issue_date TEXT, due_date TEXT,
    currency TEXT, seller_name TEXT, seller_vat TEXT, buyer_name TEXT, buyer_vat TEXT,
    net REAL, vat REAL, payable REAL, status TEXT
);
CREATE TABLE IF NOT EXISTS invoice_lines (
    source_file TEXT REFERENCES invoices(source_file) ON DELETE CASCADE, line_id TEXT, description TEXT,
    quantity REAL, unit_code TEXT, unit_price REAL, net_amount REAL, vat_category TEXT, vat_rate REAL
);
CREATE TABLE IF NOT EXISTS vat_breakdown (
    source_file TEXT REFERENCES invoices(source_file) ON DELETE CASCADE, category TEXT, rate REAL,
    taxable REAL, tax REAL
);
CREATE TABLE IF NOT EXISTS issues (
    source_file TEXT, invoice_number TEXT, severity TEXT, code TEXT, rule TEXT, message TEXT
);
"""


def save(batch: Batch, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA)
        files = [inv.source_file for inv in batch.invoices] + batch.unreadable
        con.executemany("DELETE FROM invoices WHERE source_file = ?", [(f,) for f in files])
        con.executemany("DELETE FROM issues WHERE source_file = ?", [(f,) for f in files])
        for inv in batch.invoices:
            s = inv.sign
            con.execute(
                "INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inv.source_file, inv.syntax, inv.doc_type, inv.number,
                 inv.issue_date and inv.issue_date.isoformat(), inv.due_date and inv.due_date.isoformat(),
                 inv.currency, inv.seller_name, inv.seller_vat, inv.buyer_name, inv.buyer_vat,
                 float(s * (inv.tax_exclusive or 0)), float(s * (inv.tax_total or 0)), float(s * (inv.payable or 0)),
                 batch.status(inv)))
            con.executemany(
                "INSERT INTO invoice_lines VALUES (?,?,?,?,?,?,?,?,?)",
                [(inv.source_file, l.id, l.description, float(l.quantity), l.unit_code, float(l.unit_price),
                  float(s * l.net_amount), l.vat_category, float(l.vat_rate)) for l in inv.lines])
            con.executemany(
                "INSERT INTO vat_breakdown VALUES (?,?,?,?,?)",
                [(inv.source_file, v.category, float(v.rate), float(s * v.taxable), float(s * v.tax)) for v in inv.vat])
        con.executemany(
            "INSERT INTO issues VALUES (?,?,?,?,?,?)",
            [(i.source_file, i.invoice_number, i.severity, i.code, i.rule, i.message) for i in batch.issues])
        con.commit()
    finally:
        con.close()
    return path
