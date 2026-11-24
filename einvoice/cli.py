"""Command line: `python -m einvoice sample` and `python -m einvoice read FILES_OR_FOLDERS`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .batch import collect, process
from .report_excel import write_excel
from .report_html import write_html
from .sample import generate
from .store import save


def cmd_sample(args: argparse.Namespace) -> int:
    paths = generate(args.out)
    print(f"  {len(paths)} files written to {args.out}\n\nNext: python -m einvoice read {args.out}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    paths = collect(args.inputs)
    if not paths:
        print("error: no .xml files found", file=sys.stderr)
        return 2
    batch = process(paths)
    out = Path(args.out)
    excel = write_excel(batch, out / "einvoices.xlsx")
    html = write_html(batch, out / "einvoices.html")
    db = save(batch, Path(args.db) if args.db else out / "einvoices.sqlite")

    icons = {"ok": "OK  ", "warning": "WARN", "error": "ERR "}
    print()
    for inv in batch.invoices:
        status = batch.status(inv)
        total = inv.sign * (inv.payable or 0)
        print(f"  [{icons[status]}] {inv.syntax:<4}{inv.seller_name[:26]:<27}{inv.number:<15}{total:>12,.2f} {inv.currency}")
    for name in batch.unreadable:
        print(f"  [{icons['error']}] {name} (not processed)")
    errors = sum(1 for i in batch.issues if i.severity == "error")
    warnings = sum(1 for i in batch.issues if i.severity == "warning")
    print(f"\n  {len(batch.invoices)} invoice(s) read, {len(batch.unreadable)} unreadable, {errors} error(s), {warnings} warning(s)")
    print(f"\n  Excel:  {excel}\n  HTML:   {html}\n  SQLite: {db}")
    return 1 if args.strict and errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="einvoice", description="Read and check UBL and CII e-invoices.")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sample", help="write fictional sample invoices")
    s.add_argument("--out", default="sample_invoices")
    s.set_defaults(func=cmd_sample)

    r = sub.add_parser("read", help="read, check and export invoices")
    r.add_argument("inputs", nargs="+", help="XML files or folders")
    r.add_argument("--out", default="output")
    r.add_argument("--db", help="SQLite file to create or update (default: OUT/einvoices.sqlite)")
    r.add_argument("--strict", action="store_true", help="exit with code 1 when there are errors")
    r.set_defaults(func=cmd_read)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
