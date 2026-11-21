"""Matching of statement entries and receipts against existing journal lines."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .accounting import PAYMENT_KINDS
from .models import Account, JournalLine, Receipt, Transaction

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
