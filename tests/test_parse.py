from datetime import date
from decimal import Decimal

import pytest

from einvoice import UnreadableInvoice, parse_bytes
from einvoice.sample import BROKEN, SPECS, SUSPICIOUS, build_cii, build_ubl

NORDLICHT = SPECS[0]   # UBL invoice, 19% and 7%
ATLANTICO = SPECS[1]   # CII invoice, 23% and 6%
CREDIT = SPECS[6]      # UBL credit note


def test_ubl_invoice_header_and_parties():
    inv = parse_bytes(build_ubl(NORDLICHT), "a.xml")
    assert (inv.syntax, inv.doc_type, inv.number, inv.currency) == ("UBL", "invoice", "INV-2026-0141", "EUR")
    assert (inv.issue_date, inv.due_date) == (date(2026, 10, 2), date(2026, 11, 1))
    assert (inv.seller_name, inv.seller_vat) == ("Nordlicht Papier GmbH", "DE123456789")
    assert inv.buyer_name == "Orbit Handel GmbH"


def test_ubl_lines_and_totals():
    inv = parse_bytes(build_ubl(NORDLICHT))
    assert len(inv.lines) == 3
    first = inv.lines[0]
    assert (first.quantity, first.unit_code, first.unit_price, first.net_amount) == (12, "XBX", Decimal("24.90"), Decimal("298.80"))
    assert {(v.rate, v.tax) for v in inv.vat} == {(Decimal(19), Decimal("80.71")), (Decimal(7), Decimal("5.88"))}
    assert (inv.line_total, inv.tax_total, inv.payable) == (Decimal("508.80"), Decimal("86.59"), Decimal("595.39"))


def test_cii_invoice_matches_ubl_semantics():
    cii = parse_bytes(build_cii(ATLANTICO))
    ubl = parse_bytes(build_ubl(ATLANTICO))
    assert cii.syntax == "CII"
    for attr in ("number", "issue_date", "due_date", "seller_name", "seller_vat", "buyer_vat",
                 "line_total", "tax_total", "tax_inclusive", "payable"):
        assert getattr(cii, attr) == getattr(ubl, attr), attr
    assert [(l.description, l.net_amount, l.vat_rate) for l in cii.lines] == \
           [(l.description, l.net_amount, l.vat_rate) for l in ubl.lines]


def test_credit_notes_are_recognised_in_both_syntaxes():
    assert parse_bytes(build_ubl(CREDIT)).doc_type == "credit_note"
    cii = parse_bytes(build_cii(CREDIT))
    assert cii.doc_type == "credit_note" and cii.sign == -1


def test_xxe_payload_is_blocked():
    with pytest.raises(UnreadableInvoice, match="Blocked unsafe XML"):
        parse_bytes(SUSPICIOUS)


def test_billion_laughs_is_blocked():
    bomb = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">&lol2;</Invoice>"""
    with pytest.raises(UnreadableInvoice, match="Blocked unsafe XML"):
        parse_bytes(bomb)


def test_broken_xml():
    with pytest.raises(UnreadableInvoice, match="Not well-formed"):
        parse_bytes(BROKEN)


def test_other_xml_documents_are_refused():
    with pytest.raises(UnreadableInvoice, match="Not a UBL or CII invoice"):
        parse_bytes(b"<Order><ID>1</ID></Order>")


def test_invalid_number_is_reported():
    xml = build_ubl(NORDLICHT).replace(b">595.39<", b">5,95.39<")
    with pytest.raises(UnreadableInvoice, match="Invalid number"):
        parse_bytes(xml)


def test_invalid_date_is_reported():
    xml = build_ubl(NORDLICHT).replace(b"2026-10-02", b"2026-02-30")
    with pytest.raises(UnreadableInvoice, match="Invalid value"):
        parse_bytes(xml)
