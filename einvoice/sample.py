"""Writes a folder of fictional e-invoices in both UBL and CII, with real-world problems planted.

The buyer is a fictional group with companies in several EU countries, so most invoices are
domestic (national VAT) and one is a cross-border reverse-charge invoice (category AE, 0%).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .parse import CAC, CBC, RAM, RSM, UBL_CREDIT, UBL_INVOICE, UDT

CENT = Decimal("0.01")
for prefix, uri in (("cac", CAC), ("cbc", CBC), ("rsm", RSM), ("ram", RAM), ("udt", UDT)):
    ET.register_namespace(prefix, uri)


def m(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Spec:
    file: str
    syntax: str
    number: str
    issue: str
    due: str
    seller: tuple[str, str]
    buyer: tuple[str, str]
    lines: list[tuple[str, str, str, str, str, str]]  # description, qty, unit, price, VAT category, rate
    credit: bool = False
    line_amount_override: dict[int, str] = field(default_factory=dict)
    vat_override: dict[str, str] = field(default_factory=dict)  # rate -> stated tax

    def computed(self):
        nets = [m(Decimal(q) * Decimal(p)) for _, q, _, p, _, _ in self.lines]
        for i, value in self.line_amount_override.items():
            nets[i] = m(value)
        groups: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        for net, (_, _, _, _, cat, rate) in zip(nets, self.lines):
            groups[(cat, rate)] += net
        vat = []
        for (cat, rate), base in groups.items():
            tax = m(self.vat_override.get(rate, base * Decimal(rate) / 100))
            vat.append((cat, rate, m(base), tax))
        line_total = sum(nets, Decimal("0.00"))
        tax_total = sum((v[3] for v in vat), Decimal("0.00"))
        return nets, vat, line_total, tax_total, line_total + tax_total


BUYERS = {
    "PT": ("Orbit Trading Lda", "PT507654321"),
    "DE": ("Orbit Handel GmbH", "DE298765432"),
    "FR": ("Orbit Commerce SARL", "FR41987654321"),
    "IT": ("Orbit Italia SRL", "IT09876543210"),
    "ES": ("Orbit Iberia SL", "ESB87654321"),
}

SPECS = [
    Spec("01_nordlicht_INV-2026-0141.xml", "UBL", "INV-2026-0141", "2026-10-02", "2026-11-01",
         ("Nordlicht Papier GmbH", "DE123456789"), BUYERS["DE"],
         [("Copy paper A4, 80 g, box of 5 reams", "12", "XBX", "24.90", "S", "19"),
          ("Recycled envelopes C5, pack of 500", "4", "XPK", "31.50", "S", "19"),
          ("Office calendar 2027", "10", "C62", "8.40", "S", "7")]),
    Spec("02_atlantico_FT-2026-0388.xml", "CII", "FT 2026/388", "2026-10-06", "2026-11-05",
         ("Atlântico Cafés Lda", "PT509123456"), BUYERS["PT"],
         [("Coffee beans, 1 kg", "15", "KGM", "18.75", "S", "23"),
          ("Sugar sachets, box of 1000", "3", "XBX", "12.60", "S", "6"),
          ("Espresso machine maintenance", "1", "C62", "95.00", "S", "23")]),
    Spec("03_lumiere_F2026-1102.xml", "UBL", "F2026-1102", "2026-10-09", "2026-11-08",
         ("Brasserie Lumière SARL", "FR32123456789"), BUYERS["FR"],
         [("Team lunch, 18 people", "18", "C62", "27.50", "S", "10"),
          ("Room hire, half day", "1", "C62", "350.00", "S", "20")]),
    Spec("04_vandijk_VD-55871.xml", "UBL", "VD-55871", "2026-10-12", "2026-11-11",
         ("Van Dijk Logistiek BV", "NL123456789B01"), BUYERS["PT"],
         [("Pallet transport Rotterdam to Lisbon", "6", "C62", "185.00", "AE", "0"),
          ("Fuel surcharge", "1", "C62", "96.30", "AE", "0")],
         line_amount_override={0: "1150.00"}),          # 6 x 185.00 is 1,110.00
    Spec("05_officina_2026-77.xml", "CII", "2026/77", "2026-10-15", "2026-11-14",
         ("Officina Verde SRL", "IT12345678901"), BUYERS["IT"],
         [("Office plant maintenance, October", "1", "C62", "420.00", "S", "22"),
          ("Replacement plants", "6", "C62", "34.00", "S", "22")],
         vat_override={"22": "115.48"}),                # 22% of 624.00 is 137.28
    Spec("06_costa_CL-0912.xml", "UBL", "CL-0912", "2026-10-20", "2026-10-05",
         ("Costa Limpieza SL", "ES12-34"), BUYERS["ES"],
         [("Office cleaning, October", "1", "C62", "780.00", "S", "21"),
          ("Window cleaning", "2", "C62", "65.00", "S", "21")]),
    Spec("07_nordlicht_CN-2026-0009.xml", "UBL", "CN-2026-0009", "2026-10-22", "2026-10-22",
         ("Nordlicht Papier GmbH", "DE123456789"), BUYERS["DE"],
         [("Return: recycled envelopes C5, pack of 500", "1", "XPK", "31.50", "S", "19")], credit=True),
    # The same invoice as 02, received a second time through another channel (and in another syntax).
    Spec("08_atlantico_FT-2026-0388_email.xml", "UBL", "FT 2026/388", "2026-10-06", "2026-11-05",
         ("Atlântico Cafés Lda", "PT509123456"), BUYERS["PT"],
         [("Coffee beans, 1 kg", "15", "KGM", "18.75", "S", "23"),
          ("Sugar sachets, box of 1000", "3", "XBX", "12.60", "S", "6"),
          ("Espresso machine maintenance", "1", "C62", "95.00", "S", "23")]),
]


def _sub(parent, tag: str, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k.replace("_", ""): str(v) for k, v in attrs.items()} if attrs else {})
    if text is not None:
        el.text = str(text)
    return el


def build_ubl(s: Spec) -> bytes:
    a, b = f"{{{CAC}}}", f"{{{CBC}}}"
    root_ns, root_tag = (UBL_CREDIT, "CreditNote") if s.credit else (UBL_INVOICE, "Invoice")
    root = ET.Element(f"{{{root_ns}}}{root_tag}")
    _sub(root, b + "CustomizationID", "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0")
    _sub(root, b + "ProfileID", "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0")
    _sub(root, b + "ID", s.number)
    _sub(root, b + "IssueDate", s.issue)
    if not s.credit:
        _sub(root, b + "DueDate", s.due)
        _sub(root, b + "InvoiceTypeCode", "380")
    else:
        _sub(root, b + "CreditNoteTypeCode", "381")
    _sub(root, b + "DocumentCurrencyCode", "EUR")
    for role, (name, vat) in (("AccountingSupplierParty", s.seller), ("AccountingCustomerParty", s.buyer)):
        party = _sub(_sub(root, a + role), a + "Party")
        _sub(_sub(party, a + "PartyName"), b + "Name", name)
        scheme = _sub(party, a + "PartyTaxScheme")
        _sub(scheme, b + "CompanyID", vat)
        _sub(_sub(scheme, a + "TaxScheme"), b + "ID", "VAT")
        _sub(_sub(party, a + "PartyLegalEntity"), b + "RegistrationName", name)

    nets, vat, line_total, tax_total, gross = s.computed()
    tax = _sub(root, a + "TaxTotal")
    _sub(tax, b + "TaxAmount", tax_total, currencyID="EUR")
    for cat, rate, base, amount in vat:
        sub = _sub(tax, a + "TaxSubtotal")
        _sub(sub, b + "TaxableAmount", base, currencyID="EUR")
        _sub(sub, b + "TaxAmount", amount, currencyID="EUR")
        category = _sub(sub, a + "TaxCategory")
        _sub(category, b + "ID", cat)
        _sub(category, b + "Percent", rate)
        if cat == "AE":
            _sub(category, b + "TaxExemptionReason", "Reverse charge")
        _sub(_sub(category, a + "TaxScheme"), b + "ID", "VAT")
    totals = _sub(root, a + "LegalMonetaryTotal")
    for tag, value in (("LineExtensionAmount", line_total), ("TaxExclusiveAmount", line_total),
                       ("TaxInclusiveAmount", gross), ("PayableAmount", gross)):
        _sub(totals, b + tag, value, currencyID="EUR")

    line_tag, qty_tag = ("CreditNoteLine", "CreditedQuantity") if s.credit else ("InvoiceLine", "InvoicedQuantity")
    for i, ((desc, qty, unit, price, cat, rate), net) in enumerate(zip(s.lines, nets), start=1):
        line = _sub(root, a + line_tag)
        _sub(line, b + "ID", i)
        _sub(line, b + qty_tag, qty, unitCode=unit)
        _sub(line, b + "LineExtensionAmount", net, currencyID="EUR")
        item = _sub(line, a + "Item")
        _sub(item, b + "Name", desc)
        category = _sub(item, a + "ClassifiedTaxCategory")
        _sub(category, b + "ID", cat)
        _sub(category, b + "Percent", rate)
        _sub(_sub(category, a + "TaxScheme"), b + "ID", "VAT")
        _sub(_sub(line, a + "Price"), b + "PriceAmount", price, currencyID="EUR")
    ET.indent(root)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def build_cii(s: Spec) -> bytes:
    r, x, u = f"{{{RSM}}}", f"{{{RAM}}}", f"{{{UDT}}}"
    root = ET.Element(r + "CrossIndustryInvoice")
    ctx = _sub(root, r + "ExchangedDocumentContext")
    _sub(_sub(ctx, x + "GuidelineSpecifiedDocumentContextParameter"), x + "ID", "urn:cen.eu:en16931:2017")
    doc = _sub(root, r + "ExchangedDocument")
    _sub(doc, x + "ID", s.number)
    _sub(doc, x + "TypeCode", "381" if s.credit else "380")
    _sub(_sub(doc, x + "IssueDateTime"), u + "DateTimeString", s.issue.replace("-", ""), format="102")
    trade = _sub(root, r + "SupplyChainTradeTransaction")

    nets, vat, line_total, tax_total, gross = s.computed()
    for i, ((desc, qty, unit, price, cat, rate), net) in enumerate(zip(s.lines, nets), start=1):
        item = _sub(trade, x + "IncludedSupplyChainTradeLineItem")
        _sub(_sub(item, x + "AssociatedDocumentLineDocument"), x + "LineID", i)
        _sub(_sub(item, x + "SpecifiedTradeProduct"), x + "Name", desc)
        _sub(_sub(_sub(item, x + "SpecifiedLineTradeAgreement"), x + "NetPriceProductTradePrice"), x + "ChargeAmount", price)
        _sub(_sub(item, x + "SpecifiedLineTradeDelivery"), x + "BilledQuantity", qty, unitCode=unit)
        settle = _sub(item, x + "SpecifiedLineTradeSettlement")
        t = _sub(settle, x + "ApplicableTradeTax")
        _sub(t, x + "TypeCode", "VAT")
        _sub(t, x + "CategoryCode", cat)
        _sub(t, x + "RateApplicablePercent", rate)
        _sub(_sub(settle, x + "SpecifiedTradeSettlementLineMonetarySummation"), x + "LineTotalAmount", net)

    agreement = _sub(trade, x + "ApplicableHeaderTradeAgreement")
    for role, (name, vat_id) in (("SellerTradeParty", s.seller), ("BuyerTradeParty", s.buyer)):
        party = _sub(agreement, x + role)
        _sub(party, x + "Name", name)
        _sub(_sub(party, x + "SpecifiedTaxRegistration"), x + "ID", vat_id, schemeID="VA")
    _sub(trade, x + "ApplicableHeaderTradeDelivery")
    settlement = _sub(trade, x + "ApplicableHeaderTradeSettlement")
    _sub(settlement, x + "InvoiceCurrencyCode", "EUR")
    for cat, rate, base, amount in vat:
        t = _sub(settlement, x + "ApplicableTradeTax")
        _sub(t, x + "CalculatedAmount", amount)
        _sub(t, x + "TypeCode", "VAT")
        _sub(t, x + "BasisAmount", base)
        _sub(t, x + "CategoryCode", cat)
        _sub(t, x + "RateApplicablePercent", rate)
    terms = _sub(settlement, x + "SpecifiedTradePaymentTerms")
    _sub(_sub(terms, x + "DueDateDateTime"), u + "DateTimeString", s.due.replace("-", ""), format="102")
    sums = _sub(settlement, x + "SpecifiedTradeSettlementHeaderMonetarySummation")
    _sub(sums, x + "LineTotalAmount", line_total)
    _sub(sums, x + "TaxBasisTotalAmount", line_total)
    _sub(sums, x + "TaxTotalAmount", tax_total, currencyID="EUR")
    _sub(sums, x + "GrandTotalAmount", gross)
    _sub(sums, x + "DuePayableAmount", gross)
    ET.indent(root)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


BROKEN = b'<?xml version="1.0" encoding="UTF-8"?>\n<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">\n  <cbc:ID>TRUNCATED'

# A classic XXE payload: a safe parser must refuse it instead of reading the local file.
SUSPICIOUS = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Invoice [ <!ENTITY secret SYSTEM "file:///etc/passwd"> ]>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>&secret;</cbc:ID>
</Invoice>
"""


def generate(out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in SPECS:
        path = out / spec.file
        path.write_bytes(build_ubl(spec) if spec.syntax == "UBL" else build_cii(spec))
        paths.append(path)
    for name, content in (("09_scanner_export_broken.xml", BROKEN), ("10_suspicious_attachment.xml", SUSPICIOUS)):
        (out / name).write_bytes(content)
        paths.append(out / name)
    return paths
