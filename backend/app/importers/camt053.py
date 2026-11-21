"""ISO 20022 camt.053 (bank-to-customer statement) parser.

Namespace agnostic: works with camt.053.001.02 .. .08 (Swiss SPS variants included).
Only booked entries (Sts = BOOK) are returned. Entries with several TxDtls carrying their
own amounts (batch bookings) are split into one ParsedEntry per transaction detail.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

from lxml import etree


class CamtError(ValueError):
    pass


@dataclass
class ParsedEntry:
    booking_date: dt.date
    value_date: dt.date | None
    amount: Decimal  # signed: + credit (money in), - debit (money out)
    currency: str
    description: str
    counterparty: str | None = None
    counterparty_iban: str | None = None
    remittance_info: str | None = None
    bank_ref: str | None = None
    end_to_end_id: str | None = None
    tx_code: str | None = None
    orig_amount: Decimal | None = None
    orig_currency: str | None = None
    exchange_rate: Decimal | None = None
    dedup_key: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedStatement:
    statement_id: str
    iban: str | None
    account_other_id: str | None
    currency: str
    owner: str | None
    servicer: str | None
    from_date: dt.date | None
    to_date: dt.date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    entries: list[ParsedEntry]


def _strip_ns(root: etree._Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    etree.cleanup_namespaces(root)


def _t(el: etree._Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    if found is None or found.text is None:
        return None
    s = found.text.strip()
    return s or None


def _date(el: etree._Element | None, path: str) -> dt.date | None:
    """Reads <X><Dt>..</Dt></X> or <X><DtTm>..</DtTm></X>."""
    if el is None:
        return None
    node = el.find(path)
    if node is None:
        return None
    v = _t(node, "Dt") or _t(node, "DtTm") or (node.text or "").strip()
    return _parse_date(v)


def _parse_date(v: str | None) -> dt.date | None:
    if not v:
        return None
    return dt.date.fromisoformat(v[:10])


def _amt(el: etree._Element | None) -> tuple[Decimal | None, str | None]:
    if el is None or el.text is None:
        return None, None
    return Decimal(el.text.strip()), el.get("Ccy")


def _signed(amount: Decimal, ind: str | None) -> Decimal:
    return -amount if ind == "DBIT" else amount


def _balance(stmt: etree._Element, codes: tuple[str, ...]) -> tuple[Decimal | None, dt.date | None]:
    for bal in stmt.findall("Bal"):
        code = _t(bal, "Tp/CdOrPrtry/Cd") or _t(bal, "Tp/CdOrPrtry/Prtry")
        if code in codes:
            amt, _ = _amt(bal.find("Amt"))
            if amt is None:
                continue
            return _signed(amt, _t(bal, "CdtDbtInd")), _date(bal, "Dt")
    return None, None


def _party_name(party: etree._Element | None) -> str | None:
    if party is None:
        return None
    # camt.053.001.08 wraps parties in <Pty>
    pty = party.find("Pty")
    if pty is not None:
        party = pty
    name = _t(party, "Nm")
    if name:
        return name
    lines = [x.text.strip() for x in party.findall("PstlAdr/AdrLine") if x.text and x.text.strip()]
    return lines[0] if lines else None


_CARD_RE = re.compile(r"//\s*\d{4}X+\d{4}[^/]*//\s*(.+)$")
_CARD_MASK = re.compile(r"\s*//\s*\d{4}X+\d{4}(\s+\d{2}/\d{2})?")


def _clean_text(text: str | None) -> str | None:
    """Drop masked card numbers / expiry and collapse whitespace."""
    if not text:
        return text
    return re.sub(r"\s+", " ", _CARD_MASK.sub("", text)).strip()


def _clean_counterparty_from_text(text: str) -> str | None:
    """'Debit card payment // 4397XXXXXXXX5084 10/28 // Migros M Dorigny' -> 'Migros M Dorigny'
    'Credit UBS TWINT // MAQUET, ALEXANDRE' -> 'MAQUET, ALEXANDRE'"""
    m = _CARD_RE.search(text)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    if "//" in text:
        tail = text.split("//")[-1].strip()
        if tail and tail.upper() not in {"E-BILL", "FILETRANSFER"}:
            return re.sub(r"\s+", " ", tail)
    return None


def _tx_code(node: etree._Element) -> str | None:
    dom = _t(node, "BkTxCd/Domn/Cd")
    fam = _t(node, "BkTxCd/Domn/Fmly/Cd")
    sub = _t(node, "BkTxCd/Domn/Fmly/SubFmlyCd")
    prop = _t(node, "BkTxCd/Prtry/Cd")
    parts = [p for p in (dom, fam, sub) if p]
    code = "/".join(parts) if parts else None
    if prop:
        code = f"{code} ({prop})" if code else prop
    return code


def _entry_from(ntry: etree._Element, tx: etree._Element | None, idx: int, n_tx: int) -> ParsedEntry:
    src = tx if tx is not None else ntry
    ind = _t(src, "CdtDbtInd") or _t(ntry, "CdtDbtInd")
    amt, ccy = _amt(src.find("Amt"))
    if amt is None:
        amt, ccy = _amt(ntry.find("Amt"))
    if amt is None:
        raise CamtError("Entry without amount")
    signed = _signed(amt, ind)

    booking = _date(ntry, "BookgDt")
    value = _date(ntry, "ValDt")
    if booking is None:
        booking = value
    if booking is None:
        raise CamtError("Entry without booking date")

    addtl_tx = _t(src, "AddtlTxInf") if tx is not None else None
    addtl_ntry = _t(ntry, "AddtlNtryInf")
    ustrd = []
    strd_ref = None
    if tx is not None:
        ustrd = [u.text.strip() for u in tx.findall("RmtInf/Ustrd") if u.text and u.text.strip()]
        strd_ref = _t(tx, "RmtInf/Strd/CdtrRefInf/Ref")
    remittance = " ".join(ustrd) or None
    if strd_ref:
        remittance = f"{remittance} Ref {strd_ref}" if remittance else f"Ref {strd_ref}"

    counterparty = None
    cp_iban = None
    if tx is not None:
        parties = tx.find("RltdPties")
        if parties is not None:
            if ind == "DBIT":
                counterparty = _party_name(parties.find("Cdtr")) or _party_name(parties.find("UltmtCdtr"))
                cp_iban = _t(parties, "CdtrAcct/Id/IBAN")
            else:
                counterparty = _party_name(parties.find("Dbtr")) or _party_name(parties.find("UltmtDbtr"))
                cp_iban = _t(parties, "DbtrAcct/Id/IBAN")
    text_for_cp = addtl_tx or addtl_ntry or ""
    if not counterparty:
        counterparty = _clean_counterparty_from_text(text_for_cp)

    description = " | ".join(x for x in dict.fromkeys([_clean_text(addtl_tx or addtl_ntry), remittance]) if x) or (
        counterparty or ""
    )

    # foreign currency details
    orig_amt = orig_ccy = rate = None
    for path in ("AmtDtls/InstdAmt", "AmtDtls/TxAmt"):
        node = src.find(path)
        if node is None:
            continue
        a, c = _amt(node.find("Amt"))
        if a is not None and c and c != ccy:
            orig_amt, orig_ccy = (-a if ind == "DBIT" else a), c
            r = _t(node, "CcyXchg/XchgRate")
            rate = Decimal(r) if r else None
            break

    ntry_ref = _t(ntry, "AcctSvcrRef")
    tx_ref = _t(tx, "Refs/AcctSvcrRef") if tx is not None else None
    e2e = _t(tx, "Refs/EndToEndId") if tx is not None else None
    if e2e == "NOTPROVIDED":
        e2e = None
    bank_ref = tx_ref or ntry_ref
    if ntry_ref:
        dedup = f"ref:{ntry_ref}" + (f"#{idx}" if n_tx > 1 else "")
    else:
        dedup = f"h:{booking}:{signed}:{(text_for_cp or remittance or '')[:80]}#{idx}"

    return ParsedEntry(
        booking_date=booking,
        value_date=value,
        amount=signed,
        currency=ccy or "",
        description=description[:2000],
        counterparty=(counterparty or None) and counterparty[:255],
        counterparty_iban=cp_iban,
        remittance_info=remittance,
        bank_ref=bank_ref,
        end_to_end_id=e2e,
        tx_code=_tx_code(src) or _tx_code(ntry),
        orig_amount=orig_amt,
        orig_currency=orig_ccy,
        exchange_rate=rate,
        dedup_key=dedup,
        raw={
            "addtl_ntry": addtl_ntry,
            "addtl_tx": addtl_tx,
            "ntry_ref": ntry_ref,
            "tx_ref": tx_ref,
            "e2e": e2e,
        },
    )


def parse(content: bytes) -> list[ParsedStatement]:
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=True)
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError as e:
        raise CamtError(f"Invalid XML: {e}") from e
    _strip_ns(root)
    container = root.find("BkToCstmrStmt")
    if container is None:
        raise CamtError("Not a camt.053 document (BkToCstmrStmt missing)")

    statements: list[ParsedStatement] = []
    for stmt in container.findall("Stmt"):
        iban = _t(stmt, "Acct/Id/IBAN")
        other = _t(stmt, "Acct/Id/Othr/Id")
        ccy = _t(stmt, "Acct/Ccy") or ""
        opening, op_date = _balance(stmt, ("OPBD", "PRCD"))
        closing, cl_date = _balance(stmt, ("CLBD",))
        from_d = _parse_date(_t(stmt, "FrToDt/FrDtTm")) or op_date
        to_d = _parse_date(_t(stmt, "FrToDt/ToDtTm")) or cl_date

        entries: list[ParsedEntry] = []
        for ntry in stmt.findall("Ntry"):
            status = _t(ntry, "Sts") or _t(ntry, "Sts/Cd")
            if status and status not in ("BOOK",):
                continue
            txs = ntry.findall("NtryDtls/TxDtls")
            # batch booking: several TxDtls each with own amount -> split
            if len(txs) > 1 and all(t.find("Amt") is not None for t in txs):
                for i, tx in enumerate(txs):
                    entries.append(_entry_from(ntry, tx, i, len(txs)))
            else:
                entries.append(_entry_from(ntry, txs[0] if txs else None, 0, 1))
        statements.append(
            ParsedStatement(
                statement_id=_t(stmt, "Id") or "",
                iban=iban,
                account_other_id=other,
                currency=ccy,
                owner=_t(stmt, "Acct/Ownr/Nm") or _t(stmt, "Acct/Ownr/Pty/Nm"),
                servicer=_t(stmt, "Acct/Svcr/FinInstnId/Nm"),
                from_date=from_d,
                to_date=to_d,
                opening_balance=opening,
                closing_balance=closing,
                entries=entries,
            )
        )
    return statements
