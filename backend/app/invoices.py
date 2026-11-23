"""Invoices (bills to pay): booking as a debt, and settlement by payments.

Accounting (accrual): once an invoice is complete it is booked on its issue date

    expense account (category)   +amount
    payable account (2000)       -amount   invoice_id = the invoice

so the expense counts in the month the bill was received and the debt shows in the balance sheet.
A payment is any transaction whose counter side is a payable line carrying the invoice id

    payable account              +paid     invoice_id = the invoice
    bank / card / cash           -paid

The amount still due is minus the sum of the invoice's lines; the invoice is paid when it reaches 0.
Payments are recognised automatically on import (QR reference, creditor IBAN, or amount + name) and
can be linked by hand.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .accounting import (
    PAYMENT_KINDS,
    AccountingError,
    LineIn,
    create_transaction,
    q,
    set_lines,
    suspense_account,
)
from .matching import similarity, tokens
from .merchants import Matcher
from .models import Account, BankEntry, Invoice, JournalLine, Ledger, Transaction

ZERO = Decimal(0)
# payments are looked for from a few days before the invoice date up to this long after it
LOOKBACK_DAYS = 10
LOOKAHEAD_DAYS = 180


def payable_account(db: Session, ledger_id: int) -> Account:
    acc = db.scalar(
        select(Account)
        .where(Account.ledger_id == ledger_id, Account.kind == "payable", Account.is_group.is_(False),
               Account.is_active)
        .order_by(Account.code)
        .limit(1)
    )
    if acc is not None:
        return acc
    # ledgers created before the 'payable' kind existed, with a custom chart: create one
    parent = db.scalar(select(Account).where(Account.ledger_id == ledger_id, Account.code == "20",
                                             Account.is_group))
    code = "2000"
    while db.scalar(select(Account.id).where(Account.ledger_id == ledger_id, Account.code == code)):
        code = str(int(code) + 1)
    acc = Account(ledger_id=ledger_id, parent_id=parent.id if parent else None, code=code,
                  name="Factures à payer", type="liability", kind="payable")
    db.add(acc)
    db.flush()
    return acc


def norm_ref(ref: str | None) -> str:
    """Payment reference without spaces and leading zeros (QR / BVR references are zero-padded)."""
    return re.sub(r"[^0-9A-Z]", "", (ref or "").upper()).lstrip("0")


def norm_iban(iban: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", (iban or "").upper())


# ---------------------------------------------------------------------------- state


def remaining(db: Session, inv: Invoice) -> Decimal:
    """Amount still due."""
    if inv.transaction_id is None:
        return q(inv.amount or 0)
    total = db.scalar(select(func.coalesce(func.sum(JournalLine.amount), 0)).where(JournalLine.invoice_id == inv.id))
    return -Decimal(total)


def remaining_many(db: Session, ids: list[int]) -> dict[int, Decimal]:
    rows = db.execute(
        select(JournalLine.invoice_id, func.sum(JournalLine.amount))
        .where(JournalLine.invoice_id.in_(ids or [0]))
        .group_by(JournalLine.invoice_id)
    ).all()
    return {i: -Decimal(v) for i, v in rows}


def payment_lines(db: Session, inv: Invoice) -> list[JournalLine]:
    return list(db.scalars(
        select(JournalLine)
        .options(selectinload(JournalLine.transaction))
        .where(JournalLine.invoice_id == inv.id, JournalLine.amount > 0)
        .order_by(JournalLine.id)
    ))


def refresh(db: Session, inv: Invoice, rem: Decimal | None = None) -> str:
    """Recompute the status from the books (the booking may have been deleted, a payment edited...)."""
    db.flush()
    if inv.transaction_id is None:
        inv.status, inv.paid_date = "draft", None
        return inv.status
    rem = remaining(db, inv) if rem is None else rem
    if rem <= 0:
        inv.status = "paid"
        inv.paid_date = db.scalar(
            select(func.max(Transaction.date)).join(JournalLine)
            .where(JournalLine.invoice_id == inv.id, JournalLine.amount > 0)
        ) or inv.paid_date or dt.date.today()
    else:
        inv.status, inv.paid_date = "open", None
    return inv.status


def missing(inv: Invoice, base_currency: str) -> list[str]:
    """What prevents booking the invoice."""
    out = []
    if not inv.amount or inv.amount <= 0:
        out.append("amount")
    if (inv.currency or base_currency).upper() != base_currency.upper():
        out.append("currency")
    return out


# ---------------------------------------------------------------------------- booking


def book(db: Session, ledger: Ledger, inv: Invoice, *, state: str = "confirmed",
         user_id: int | None = None, matcher: Matcher | None = None, domain: str | None = None) -> Transaction | None:
    """Create or update the booking transaction (expense / payable). Returns None while incomplete."""
    if missing(inv, ledger.base_currency):
        return None
    payable = payable_account(db, ledger.id)
    target = inv.account_id
    if target is not None:
        acc = db.get(Account, target)
        if acc is None or acc.ledger_id != ledger.id or acc.is_group or not acc.is_active or acc.id == payable.id:
            target = None
    if target is None:
        target = suspense_account(db, ledger.id).id
    date = inv.issue_date or dt.date.today()
    label = " ".join(x for x in ("Facture", inv.issuer or "", inv.number and f"n° {inv.number}") if x)
    lines = [LineIn(target, q(inv.amount), inv.description[:500] if inv.description else None),
             LineIn(payable.id, -q(inv.amount), None, invoice_id=inv.id)]
    tx = inv.transaction
    if tx is None:
        tx = create_transaction(db, ledger.id, date, label, lines, source="invoice", state=state,
                                counterparty=inv.issuer, user_id=user_id)
        inv.transaction_id, inv.transaction = tx.id, tx
    else:
        set_lines(db, tx, lines)
        tx.date, tx.description, tx.counterparty = date, label[:500], inv.issuer
    if any(db.get(Account, ln.account_id).kind == "suspense" for ln in lines):
        tx.state = "unclassified"
    elif tx.state == "unclassified":
        tx.state = state
    # the payee
    if inv.issuer and inv.merchant_id is None:
        m = (matcher or Matcher(db, ledger)).named(inv.issuer.strip(), domain)
        inv.merchant_id = m.id
    if inv.merchant_id and not tx.merchant_locked:
        tx.merchant_id, tx.merchant_locked = inv.merchant_id, True
    refresh(db, inv)
    return tx


# ---------------------------------------------------------------------------- payments


def _payment_side(tx: Transaction) -> JournalLine | None:
    src = next((ln for ln in tx.lines if ln.bank_entry_id is not None), None)
    return src or next((ln for ln in tx.lines if ln.account.kind in PAYMENT_KINDS), None)


def link_payment(db: Session, ledger: Ledger, inv: Invoice, tx: Transaction, *, state: str = "confirmed") -> None:
    """Make ``tx`` (money out of a bank / card / cash account) a payment of ``inv``: its counter side
    becomes the payable account, tagged with the invoice."""
    if inv.transaction_id is None:
        raise AccountingError("The invoice is not booked yet (amount missing?)")
    if tx.id == inv.transaction_id:
        raise AccountingError("This is the invoice itself")
    if tx.ledger_id != ledger.id:
        raise AccountingError("Unknown transaction")
    other = {ln.invoice_id for ln in tx.lines if ln.invoice_id and ln.invoice_id != inv.id}
    if other:
        raise AccountingError("This transaction already pays another invoice")
    src = _payment_side(tx)
    if src is None or src.amount >= 0:
        raise AccountingError("Only a payment (money out of a bank, card or cash account) can settle an invoice")
    others = [ln for ln in tx.lines if ln is not src]
    if any(ln.bank_entry_id for ln in others):
        raise AccountingError("Transfers between two statement lines cannot settle an invoice")
    payable = payable_account(db, ledger.id)
    lines = [LineIn(src.account_id, src.amount, src.memo, src.orig_amount, src.orig_currency, src.bank_entry_id),
             LineIn(payable.id, -src.amount, None, invoice_id=inv.id)]
    set_lines(db, tx, lines)
    tx.state = state
    if tx.ai_status == "pending":
        tx.ai_status = "done"
    tx.ai_reason = f"Paiement / payment: {inv.issuer or ''} {inv.number or ''}".strip()
    if inv.merchant_id and not tx.merchant_locked:
        tx.merchant_id, tx.merchant_locked = inv.merchant_id, True
    db.flush()
    db.refresh(tx, ["lines"])
    refresh(db, inv)


def unlink_payment(db: Session, ledger: Ledger, inv: Invoice, tx: Transaction) -> None:
    """Not a payment of this invoice after all: the counter side goes back to 'to classify'."""
    mine = [ln for ln in tx.lines if ln.invoice_id == inv.id]
    if not mine or tx.id == inv.transaction_id:
        raise AccountingError("This transaction is not a payment of the invoice")
    susp = suspense_account(db, ledger.id)
    lines = []
    for ln in tx.lines:
        if ln.invoice_id == inv.id:
            lines.append(LineIn(susp.id, ln.amount, ln.memo))
        else:
            lines.append(LineIn(ln.account_id, ln.amount, ln.memo, ln.orig_amount, ln.orig_currency,
                                ln.bank_entry_id, ln.invoice_id))
    for ln in tx.lines:  # set_lines would carry the link over otherwise
        if ln.invoice_id == inv.id:
            ln.invoice_id = None
    set_lines(db, tx, lines)
    tx.state, tx.ai_reason = "unclassified", None
    tx.merchant_locked = False
    db.flush()
    refresh(db, inv)


def pay_from(db: Session, ledger: Ledger, inv: Invoice, account_id: int, date: dt.date,
             user_id: int | None = None) -> Transaction:
    """Record the payment by hand (e-banking order, cash...). When the bank statement is imported
    later, its line reconciles with this transaction instead of creating a new one."""
    acc = db.get(Account, account_id)
    if acc is None or acc.ledger_id != ledger.id or acc.kind not in PAYMENT_KINDS or acc.is_group:
        raise AccountingError("Choose a bank, card or cash account")
    rem = remaining(db, inv)
    if inv.transaction_id is None or rem <= 0:
        raise AccountingError("Nothing left to pay")
    payable = payable_account(db, ledger.id)
    label = " ".join(x for x in ("Paiement facture", inv.issuer or "", inv.number and f"n° {inv.number}") if x)
    tx = create_transaction(db, ledger.id, date, label,
                            [LineIn(payable.id, rem, None, invoice_id=inv.id), LineIn(acc.id, -rem)],
                            counterparty=inv.issuer, user_id=user_id)
    if inv.merchant_id:
        tx.merchant_id, tx.merchant_locked = inv.merchant_id, True
    refresh(db, inv)
    return tx


# ---------------------------------------------------------------------------- matching


def _entry_text(be: BankEntry | None, tx: Transaction) -> str:
    parts = [tx.description, tx.counterparty or ""]
    if be is not None:
        parts += [be.description, be.counterparty or "", be.remittance_info or "", be.end_to_end_id or ""]
    return " ".join(parts)


def _score(inv: Invoice, rem: Decimal, amount_out: Decimal, date: dt.date, text: str,
           cp_iban: str | None) -> tuple[bool, float] | None:
    """(strong, score) when the payment can be for this invoice, else None."""
    if inv.issue_date and not (inv.issue_date - dt.timedelta(days=LOOKBACK_DAYS) <= date
                               <= inv.issue_date + dt.timedelta(days=LOOKAHEAD_DAYS)):
        return None
    ref = norm_ref(inv.reference)
    ref_hit = len(ref) >= 6 and ref in re.sub(r"[^0-9A-Z]", "", text.upper())
    iban_hit = bool(inv.iban and cp_iban and norm_iban(inv.iban) == norm_iban(cp_iban))
    if amount_out != rem:
        # a reference names the invoice even for a partial payment
        return (True, 3.0) if ref_hit and 0 < amount_out <= rem else None
    sim = similarity(tokens(inv.issuer), tokens(text))
    strong = ref_hit or iban_hit
    return strong, (2 if ref_hit else 0) + (1 if iban_hit else 0) + sim


def match_entry(db: Session, ledger: Ledger, tx: Transaction, be: BankEntry) -> Invoice | None:
    """For a new statement line (money out): the open invoice it pays, if recognisable."""
    if be.amount >= 0:
        return None
    invs = db.scalars(select(Invoice).where(Invoice.ledger_id == ledger.id, Invoice.status == "open")).all()
    if not invs:
        return None
    rems = remaining_many(db, [i.id for i in invs])
    text = _entry_text(be, tx)
    scored = []
    for inv in invs:
        s = _score(inv, rems.get(inv.id, ZERO), -be.amount, tx.date, text, be.counterparty_iban)
        if s is not None:
            scored.append((s, inv))
    return _pick(scored)


def _pick(scored: list) -> Invoice | None:
    """Strong evidence wins; otherwise an exact amount with a matching name, if only one invoice fits."""
    strong = sorted((x for x in scored if x[0][0]), key=lambda x: -x[0][1])
    if strong:
        return strong[0][1]
    named = [x for x in scored if x[0][1] > 0]
    return named[0][1] if len(named) == 1 else None


def find_payment(db: Session, ledger: Ledger, inv: Invoice) -> Transaction | None:
    """For a newly booked invoice: an already imported payment that is not classified yet."""
    rem = remaining(db, inv)
    if inv.transaction_id is None or rem <= 0:
        return None
    best = None
    for tx, ln, be in _outflows(db, ledger, inv, states=("unclassified", "auto")):
        if ln.amount != -rem:
            continue
        s = _score(inv, rem, -ln.amount, tx.date, _entry_text(be, tx), be.counterparty_iban if be else None)
        if s is not None:
            if best is None or s > best[0]:
                best = (s, tx)
    if best is None:
        return None
    (strong, score), tx = best
    return tx if strong or score > 0 else None


def _outflows(db: Session, ledger: Ledger, inv: Invoice, states: tuple[str, ...] | None = None, limit: int = 300):
    start = (inv.issue_date or dt.date.today()) - dt.timedelta(days=LOOKBACK_DAYS)
    end = max(inv.due_date or start, start) + dt.timedelta(days=LOOKAHEAD_DAYS)
    stmt = (
        select(Transaction, JournalLine, BankEntry)
        .join(JournalLine, JournalLine.transaction_id == Transaction.id)
        .join(Account, Account.id == JournalLine.account_id)
        .outerjoin(BankEntry, BankEntry.id == JournalLine.bank_entry_id)
        .where(Transaction.ledger_id == ledger.id, Account.kind.in_(PAYMENT_KINDS), JournalLine.amount < 0,
               Transaction.date.between(start, end), Transaction.source != "opening",
               Transaction.id != (inv.transaction_id or 0))
        .where(~Transaction.lines.any(JournalLine.invoice_id.is_not(None)))
        .order_by(Transaction.date)
        .limit(limit)
    )
    if states:
        stmt = stmt.where(Transaction.state.in_(states))
    return db.execute(stmt).all()


def candidates(db: Session, ledger: Ledger, inv: Invoice, limit: int = 10) -> list[Transaction]:
    """Payments worth proposing for manual linking, best first."""
    rem = remaining(db, inv)
    ref = inv.issue_date or dt.date.today()
    out = []
    issuer = tokens(inv.issuer)
    for tx, ln, be in _outflows(db, ledger, inv):
        text = _entry_text(be, tx)
        sim = similarity(issuer, tokens(text))
        exact = -ln.amount == rem
        if not exact and sim == 0:
            continue
        s = _score(inv, rem, -ln.amount, tx.date, text, be.counterparty_iban if be else None)
        out.append(((0 if s and s[0] else 1), (0 if exact else 1), -sim, abs((tx.date - ref).days), tx.id, tx))
    out.sort(key=lambda x: x[:5])
    seen, res = set(), []
    for *_, tx in out:
        if tx.id not in seen:
            seen.add(tx.id)
            res.append(tx)
    return res[:limit]


def after_booking(db: Session, ledger: Ledger, inv: Invoice) -> Transaction | None:
    """Link an already imported payment, if one is recognisable."""
    tx = find_payment(db, ledger, inv)
    if tx is not None:
        db.refresh(tx, ["lines"])
        link_payment(db, ledger, inv, tx, state="auto")
    return tx


def apply_extraction(db: Session, ledger: Ledger, inv: Invoice, data: dict) -> None:
    """Fill what the user has not entered yet from the AI reading, then book the debt (to review)."""
    for field, key in (("issuer", "issuer"), ("number", "number"), ("iban", "iban"), ("reference", "reference"),
                       ("description", "description")):
        if not getattr(inv, field) and data.get(key):
            setattr(inv, field, data[key])
    for field in ("issue_date", "due_date"):
        if getattr(inv, field) is None and data.get(field):
            setattr(inv, field, dt.date.fromisoformat(data[field]))
    if inv.amount is None and data.get("total"):
        inv.amount = q(data["total"])
        inv.currency = data.get("currency") or ledger.base_currency
    if inv.account_id is None and data.get("account_code"):
        inv.account_id = db.scalar(select(Account.id).where(Account.ledger_id == ledger.id,
                                                            Account.code == data["account_code"]))
    gaps = missing(inv, ledger.base_currency)
    inv.error = None
    if gaps:
        inv.error = ("Montant en devise étrangère : indiquez le montant en " + ledger.base_currency
                     if "currency" in gaps else "Montant illisible")
        return
    if inv.transaction_id is None:
        book(db, ledger, inv, state="auto", domain=data.get("website"))
        after_booking(db, ledger, inv)
