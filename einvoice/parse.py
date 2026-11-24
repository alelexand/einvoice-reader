"""Reads UBL 2.1 (Peppol BIS Billing 3.0) and UN/CEFACT CII (Factur-X / ZUGFeRD / XRechnung) e-invoices.

XML is parsed with defusedxml, which refuses DTDs, entity expansion and external
references. E-invoices arrive from outside the company, so XXE and "billion laughs"
payloads are a real risk.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from .model import ZERO, Invoice, Line, VatBreakdown

UBL_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_CREDIT = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"

UBL_NS = {"cac": CAC, "cbc": CBC}
CII_NS = {"rsm": RSM, "ram": RAM, "udt": UDT}
CII_CREDIT_CODES = {"381", "396"}

MAX_BYTES = 10 * 1024 * 1024


class UnreadableInvoice(Exception):
    pass


def _text(el: Element | None, path: str, ns: dict[str, str]) -> str:
    if el is None:
        return ""
    found = el.find(path, ns)
    return (found.text or "").strip() if found is not None and found.text else ""


def _amount(el: Element | None, path: str, ns: dict[str, str], default: Decimal | None = None) -> Decimal | None:
    raw = _text(el, path, ns)
    if not raw:
        return default
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise UnreadableInvoice(f"Invalid number {raw!r} in {path}") from None


def _iso_date(raw: str) -> date | None:
    return date.fromisoformat(raw) if raw else None


def _cii_date(raw: str) -> date | None:
    return datetime.strptime(raw, "%Y%m%d").date() if raw else None


def parse_bytes(data: bytes, source_file: str = "") -> Invoice:
    if len(data) > MAX_BYTES:
        raise UnreadableInvoice("File is larger than 10 MB")
    try:
        root = fromstring(data, forbid_dtd=True)
    except DefusedXmlException as exc:
        raise UnreadableInvoice(f"Blocked unsafe XML ({type(exc).__name__}); the file was not processed") from None
    except ParseError as exc:
        raise UnreadableInvoice(f"Not well-formed XML: {exc}") from None

    tag = root.tag
    try:
        if tag in (f"{{{UBL_INVOICE}}}Invoice", f"{{{UBL_CREDIT}}}CreditNote"):
            return _parse_ubl(root, source_file)
        if tag == f"{{{RSM}}}CrossIndustryInvoice":
            return _parse_cii(root, source_file)
    except ValueError as exc:  # e.g. an impossible date
        raise UnreadableInvoice(f"Invalid value: {exc}") from None
    raise UnreadableInvoice(f"Not a UBL or CII invoice (root element {tag})")


def parse_file(path: str | Path) -> Invoice:
    path = Path(path)
    return parse_bytes(path.read_bytes(), path.name)


# ---------------------------------------------------------------------------------- UBL ----
def _ubl_party(root: Element, role: str) -> tuple[str, str]:
    party = root.find(f"cac:{role}/cac:Party", UBL_NS)
    name = _text(party, "cac:PartyLegalEntity/cbc:RegistrationName", UBL_NS) or _text(party, "cac:PartyName/cbc:Name", UBL_NS)
    return name, _text(party, "cac:PartyTaxScheme/cbc:CompanyID", UBL_NS)


def _parse_ubl(root: Element, source_file: str) -> Invoice:
    credit = root.tag.endswith("CreditNote")
    seller, seller_vat = _ubl_party(root, "AccountingSupplierParty")
    buyer, buyer_vat = _ubl_party(root, "AccountingCustomerParty")
    due = _text(root, "cbc:DueDate", UBL_NS) or _text(root, "cac:PaymentMeans/cbc:PaymentDueDate", UBL_NS)
    totals = root.find("cac:LegalMonetaryTotal", UBL_NS)
    tax_total = root.find("cac:TaxTotal", UBL_NS)

    inv = Invoice(
        source_file=source_file, syntax="UBL", doc_type="credit_note" if credit else "invoice",
        number=_text(root, "cbc:ID", UBL_NS),
        issue_date=_iso_date(_text(root, "cbc:IssueDate", UBL_NS)),
        due_date=_iso_date(due),
        currency=_text(root, "cbc:DocumentCurrencyCode", UBL_NS),
        seller_name=seller, seller_vat=seller_vat, buyer_name=buyer, buyer_vat=buyer_vat,
        line_total=_amount(totals, "cbc:LineExtensionAmount", UBL_NS),
        allowance_total=_amount(totals, "cbc:AllowanceTotalAmount", UBL_NS, ZERO),
        charge_total=_amount(totals, "cbc:ChargeTotalAmount", UBL_NS, ZERO),
        tax_exclusive=_amount(totals, "cbc:TaxExclusiveAmount", UBL_NS),
        tax_total=_amount(tax_total, "cbc:TaxAmount", UBL_NS),
        tax_inclusive=_amount(totals, "cbc:TaxInclusiveAmount", UBL_NS),
        prepaid=_amount(totals, "cbc:PrepaidAmount", UBL_NS, ZERO),
        payable=_amount(totals, "cbc:PayableAmount", UBL_NS),
    )
    line_tag, qty_tag = ("cac:CreditNoteLine", "cbc:CreditedQuantity") if credit else ("cac:InvoiceLine", "cbc:InvoicedQuantity")
    for line in root.findall(line_tag, UBL_NS):
        qty = line.find(qty_tag, UBL_NS)
        inv.lines.append(Line(
            id=_text(line, "cbc:ID", UBL_NS),
            description=_text(line, "cac:Item/cbc:Name", UBL_NS),
            quantity=_amount(line, qty_tag, UBL_NS, ZERO),
            unit_code=qty.get("unitCode", "") if qty is not None else "",
            unit_price=_amount(line, "cac:Price/cbc:PriceAmount", UBL_NS, ZERO),
            net_amount=_amount(line, "cbc:LineExtensionAmount", UBL_NS, ZERO),
            vat_category=_text(line, "cac:Item/cac:ClassifiedTaxCategory/cbc:ID", UBL_NS),
            vat_rate=_amount(line, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent", UBL_NS, ZERO),
        ))
    for sub in root.findall("cac:TaxTotal/cac:TaxSubtotal", UBL_NS):
        inv.vat.append(VatBreakdown(
            category=_text(sub, "cac:TaxCategory/cbc:ID", UBL_NS),
            rate=_amount(sub, "cac:TaxCategory/cbc:Percent", UBL_NS, ZERO),
            taxable=_amount(sub, "cbc:TaxableAmount", UBL_NS, ZERO),
            tax=_amount(sub, "cbc:TaxAmount", UBL_NS, ZERO),
        ))
    return inv


# ---------------------------------------------------------------------------------- CII ----
def _cii_party(agreement: Element | None, role: str) -> tuple[str, str]:
    party = agreement.find(f"ram:{role}", CII_NS) if agreement is not None else None
    vat = ""
    if party is not None:
        for reg in party.findall("ram:SpecifiedTaxRegistration/ram:ID", CII_NS):
            if reg.get("schemeID") == "VA":
                vat = (reg.text or "").strip()
    return _text(party, "ram:Name", CII_NS), vat


def _parse_cii(root: Element, source_file: str) -> Invoice:
    doc = root.find("rsm:ExchangedDocument", CII_NS)
    trade = root.find("rsm:SupplyChainTradeTransaction", CII_NS)
    agreement = trade.find("ram:ApplicableHeaderTradeAgreement", CII_NS) if trade is not None else None
    settlement = trade.find("ram:ApplicableHeaderTradeSettlement", CII_NS) if trade is not None else None
    sums = settlement.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", CII_NS) if settlement is not None else None
    seller, seller_vat = _cii_party(agreement, "SellerTradeParty")
    buyer, buyer_vat = _cii_party(agreement, "BuyerTradeParty")
    tax_exclusive = _amount(sums, "ram:TaxBasisTotalAmount", CII_NS)
    tax_total = _amount(sums, "ram:TaxTotalAmount", CII_NS)

    inv = Invoice(
        source_file=source_file, syntax="CII",
        doc_type="credit_note" if _text(doc, "ram:TypeCode", CII_NS) in CII_CREDIT_CODES else "invoice",
        number=_text(doc, "ram:ID", CII_NS),
        issue_date=_cii_date(_text(doc, "ram:IssueDateTime/udt:DateTimeString", CII_NS)),
        due_date=_cii_date(_text(settlement, "ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString", CII_NS)),
        currency=_text(settlement, "ram:InvoiceCurrencyCode", CII_NS),
        seller_name=seller, seller_vat=seller_vat, buyer_name=buyer, buyer_vat=buyer_vat,
        line_total=_amount(sums, "ram:LineTotalAmount", CII_NS),
        allowance_total=_amount(sums, "ram:AllowanceTotalAmount", CII_NS, ZERO),
        charge_total=_amount(sums, "ram:ChargeTotalAmount", CII_NS, ZERO),
        tax_exclusive=tax_exclusive,
        tax_total=tax_total,
        tax_inclusive=_amount(sums, "ram:GrandTotalAmount", CII_NS),
        prepaid=_amount(sums, "ram:TotalPrepaidAmount", CII_NS, ZERO),
        payable=_amount(sums, "ram:DuePayableAmount", CII_NS),
    )
    for item in (trade.findall("ram:IncludedSupplyChainTradeLineItem", CII_NS) if trade is not None else []):
        qty = item.find("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", CII_NS)
        tax = "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax"
        inv.lines.append(Line(
            id=_text(item, "ram:AssociatedDocumentLineDocument/ram:LineID", CII_NS),
            description=_text(item, "ram:SpecifiedTradeProduct/ram:Name", CII_NS),
            quantity=_amount(item, "ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", CII_NS, ZERO),
            unit_code=qty.get("unitCode", "") if qty is not None else "",
            unit_price=_amount(item, "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount", CII_NS, ZERO),
            net_amount=_amount(item, "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount", CII_NS, ZERO),
            vat_category=_text(item, f"{tax}/ram:CategoryCode", CII_NS),
            vat_rate=_amount(item, f"{tax}/ram:RateApplicablePercent", CII_NS, ZERO),
        ))
    for t in (settlement.findall("ram:ApplicableTradeTax", CII_NS) if settlement is not None else []):
        inv.vat.append(VatBreakdown(
            category=_text(t, "ram:CategoryCode", CII_NS),
            rate=_amount(t, "ram:RateApplicablePercent", CII_NS, ZERO),
            taxable=_amount(t, "ram:BasisAmount", CII_NS, ZERO),
            tax=_amount(t, "ram:CalculatedAmount", CII_NS, ZERO),
        ))
    return inv
