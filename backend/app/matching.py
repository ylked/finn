"""Matching of statement entries and receipts against existing journal lines."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .accounting import PAYMENT_KINDS, get_setting
from .models import Account, Invoice, JournalLine, Ledger, Receipt, Transaction

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {"debit", "card", "payment", "credit", "order", "banking", "twint", "ubs", "the", "and", "les", "des", "pending"}


def tokens(*texts: str | None) -> set[str]:
    out: set[str] = set()
    for t in texts:
        if t:
            out |= {w for w in _TOKEN.findall(t.lower()) if w not in _STOP and not w.isdigit()}
    return out


def similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def find_line_match(
    db: Session,
    ledger_id: int,
    account_id: int,
    amount: Decimal,
    date: dt.date,
    text: str,
    tolerance_days: int,
) -> JournalLine | None:
    """Find an unreconciled line on ``account_id`` with the same signed amount, close in date.

    Used when importing a statement entry: a manually entered expense (or the other side of a
    transfer / credit card payment already booked from another statement) gets reconciled
    instead of creating a duplicate transaction.
    """
    cands = db.scalars(
        select(JournalLine)
        .join(Transaction)
        .options(joinedload(JournalLine.transaction))
        .where(
            JournalLine.ledger_id == ledger_id,
            JournalLine.account_id == account_id,
            JournalLine.bank_entry_id.is_(None),
            JournalLine.amount == amount,
            Transaction.source != "opening",
            Transaction.date >= date - dt.timedelta(days=tolerance_days),
            Transaction.date <= date + dt.timedelta(days=tolerance_days),
        )
    ).all()
    if not cands:
        return None
    t = tokens(text)

    def score(ln: JournalLine) -> tuple:
        tx = ln.transaction
        return (abs((tx.date - date).days), -similarity(t, tokens(tx.description, tx.counterparty, ln.memo)), ln.id)

    return sorted(cands, key=score)[0]


def merge_mirror(db: Session, tx: Transaction) -> list[int]:
    """Merge the two halves of a transfer between two accounts that both have statements.

    Example: a card charge classified as a top-up of a prepaid card whose own statement is
    imported too. The card statement gave "card -20 / prepaid +20" and the prepaid statement
    already gave "prepaid +20 / (suspense or card) -20": the same money counted twice. The
    other statement line is moved onto ``tx`` and its now empty entry is deleted.

    Only a clear mirror is merged: same account and signed amount, close in date, the other
    entry created from that single statement line with its other side on suspense or on one
    of ``tx``'s own accounts, and no invoice attached. Returns the ids of deleted entries.
    """
    if tx.source == "opening":
        return []
    ledger = db.get(Ledger, tx.ledger_id)
    tol = int(get_setting(ledger, "match_tolerance_days", 7))
    removed: list[int] = []
    for ln in list(tx.lines):
        if ln.bank_entry_id is not None:
            continue
        others = {x.account_id for x in tx.lines if x is not ln}
        cands = db.scalars(
            select(JournalLine)
            .join(Transaction)
            .where(
                JournalLine.ledger_id == tx.ledger_id,
                JournalLine.account_id == ln.account_id,
                JournalLine.amount == ln.amount,
                JournalLine.bank_entry_id.is_not(None),
                Transaction.id != tx.id,
                Transaction.source.in_(("bank", "csv")),
                Transaction.date.between(tx.date - dt.timedelta(days=tol), tx.date + dt.timedelta(days=tol)),
            )
            .with_for_update(of=Transaction, skip_locked=True)
        ).all()

        def mirror(c: JournalLine) -> bool:
            d = c.transaction
            return (
                all(x is c or (x.bank_entry_id is None and (x.account.kind == "suspense" or x.account_id in others))
                    for x in d.lines)
                and not any(x.invoice_id for x in d.lines)
                and db.scalar(select(Invoice.id).where(Invoice.transaction_id == d.id).limit(1)) is None
            )

        cands = sorted((c for c in cands if mirror(c)), key=lambda c: abs((c.transaction.date - tx.date).days))
        if not cands or (len(cands) > 1 and cands[0].transaction.date == cands[1].transaction.date):
            continue  # nothing, or ambiguous
        c = cands[0]
        d = c.transaction
        be_id = c.bank_entry_id
        c.bank_entry_id = None
        db.flush()
        ln.bank_entry_id = be_id
        if c.orig_amount is not None and ln.orig_amount is None:
            ln.orig_amount, ln.orig_currency = c.orig_amount, c.orig_currency
        for r in db.scalars(select(Receipt).where(Receipt.transaction_id == d.id)):
            r.transaction_id = tx.id
        if tx.merchant_id is None and d.merchant_id is not None:
            tx.merchant_id = d.merchant_id
        if d.notes:
            tx.notes = f"{tx.notes}\n{d.notes}" if tx.notes else d.notes
        removed.append(d.id)
        db.delete(d)
        db.flush()
    return removed


def receipt_candidates(db: Session, receipt: Receipt, tolerance_days: int, limit: int = 5) -> list[Transaction]:
    ex = receipt.extracted or {}
    try:
        total = Decimal(str(ex.get("total"))).quantize(Decimal("0.01"))
    except Exception:
        return []
    if total == 0:
        return []
    try:
        date = dt.date.fromisoformat(str(ex.get("date"))[:10])
    except ValueError:
        date = receipt.created_at.date()
    ccy = (ex.get("currency") or "").upper() or None

    pay_accounts = select(Account.id).where(Account.ledger_id == receipt.ledger_id, Account.kind.in_(PAYMENT_KINDS))
    if receipt.payment_account_id:
        pay_accounts = select(Account.id).where(Account.id == receipt.payment_account_id)
    amounts = [-abs(total), abs(total)]  # outflow (purchase) or inflow (refund)
    stmt = (
        select(JournalLine)
        .join(Transaction)
        .options(joinedload(JournalLine.transaction))
        .where(
            JournalLine.ledger_id == receipt.ledger_id,
            JournalLine.account_id.in_(pay_accounts),
            Transaction.date >= date - dt.timedelta(days=tolerance_days),
            Transaction.date <= date + dt.timedelta(days=tolerance_days),
            Transaction.source != "opening",
        )
    )
    lines = db.scalars(stmt).all()
    t = tokens(ex.get("merchant"))
    scored = []
    for ln in lines:
        tx = ln.transaction
        hit = ln.amount in amounts or (
            ccy and ln.orig_currency == ccy and ln.orig_amount is not None and abs(ln.orig_amount) == abs(total)
        )
        if not hit:
            # card FX: check the bank entry original amount too
            be = ln.bank_entry
            hit = bool(be and ccy and be.orig_currency == ccy and be.orig_amount and abs(be.orig_amount) == abs(total))
        if not hit:
            continue
        if any(r.id != receipt.id for r in tx.receipts):
            continue
        scored.append((abs((tx.date - date).days) - 2 * similarity(t, tokens(tx.description, tx.counterparty)), tx))
    scored.sort(key=lambda x: x[0])
    seen, out = set(), []
    for _, tx in scored:
        if tx.id not in seen:
            seen.add(tx.id)
            out.append(tx)
    return out[:limit]
