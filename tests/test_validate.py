import dataclasses
from datetime import date
from decimal import Decimal

from einvoice import parse_bytes, validate
from einvoice.sample import SPECS, Spec, build_cii, build_ubl
from einvoice.validate import find_duplicates

CLEAN = SPECS[0]


def codes(inv) -> set[str]:
    return {i.code for i in validate(inv)}


def test_clean_invoices_have_no_issues():
    for spec in (SPECS[0], SPECS[1], SPECS[2], SPECS[6]):
        for build in (build_ubl, build_cii):
            assert validate(parse_bytes(build(spec), spec.file)) == [], (spec.file, build.__name__)


def test_reverse_charge_invoice_is_valid_apart_from_planted_line():
    inv = parse_bytes(build_ubl(dataclasses.replace(SPECS[3], line_amount_override={})))
    assert validate(inv) == []
    assert {v.category for v in inv.vat} == {"AE"}


def test_line_amount_mismatch():
    assert codes(parse_bytes(build_ubl(SPECS[3]))) == {"LINE_AMOUNT_MISMATCH"}


def test_vat_calculation_error():
    issues = validate(parse_bytes(build_cii(SPECS[4])))
    assert [(i.code, i.rule) for i in issues] == [("VAT_CALCULATION", "BR-S-09")]
    assert "should be 137.28" in issues[0].message


def test_invalid_vat_and_due_before_issue():
    assert codes(parse_bytes(build_ubl(SPECS[5]))) == {"INVALID_VAT_NUMBER", "DUE_BEFORE_ISSUE"}


def test_line_sum_and_total_rules():
    inv = parse_bytes(build_ubl(CLEAN))
    inv.line_total += Decimal("10")
    assert {"LINE_SUM_MISMATCH", "TOTAL_MISMATCH"} <= codes(inv)


def test_total_with_vat_and_payable_rules():
    inv = parse_bytes(build_ubl(CLEAN))
    inv.tax_inclusive += Decimal("1")
    found = {(i.code, i.rule) for i in validate(inv)}
    assert ("TOTAL_MISMATCH", "BR-CO-15") in found and ("PAYABLE_MISMATCH", "BR-CO-16") in found


def test_vat_total_rule():
    inv = parse_bytes(build_ubl(CLEAN))
    inv.tax_total += Decimal("5")
    assert "VAT_TOTAL_MISMATCH" in codes(inv)


def test_one_cent_rounding_is_tolerated():
    inv = parse_bytes(build_ubl(CLEAN))
    inv.vat[0].tax += Decimal("0.01")
    inv.tax_total += Decimal("0.01")
    inv.tax_inclusive += Decimal("0.01")
    inv.payable += Decimal("0.01")
    assert validate(inv) == []


def test_missing_mandatory_fields():
    inv = parse_bytes(build_ubl(CLEAN))
    inv.number, inv.buyer_name, inv.issue_date = "", "", None
    rules = {i.rule for i in validate(inv) if i.code == "MISSING_FIELD"}
    assert {"BR-02", "BR-03", "BR-07"} <= rules


def test_invoice_without_lines():
    spec = Spec("x.xml", "UBL", "X-1", "2026-10-01", "2026-10-31", ("Seller", "PT509123456"), ("Buyer", "PT507654321"), [])
    assert any(i.rule == "BR-16" for i in validate(parse_bytes(build_ubl(spec))))


def test_duplicates_across_syntaxes():
    a = parse_bytes(build_cii(SPECS[1]), "a.xml")
    b = parse_bytes(build_ubl(SPECS[7]), "b.xml")
    [dup] = find_duplicates([a, b])
    assert dup.source_file == "b.xml" and "already read from a.xml" in dup.message


def test_credit_note_with_same_number_is_not_a_duplicate():
    inv = parse_bytes(build_ubl(CLEAN), "a.xml")
    credit = dataclasses.replace(inv, source_file="b.xml", doc_type="credit_note")
    assert find_duplicates([inv, credit]) == []
