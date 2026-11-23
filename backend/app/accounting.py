"""Core double-entry bookkeeping services.

All writes to journal lines must go through this module so invariants hold:
- at least two lines, every amount non-zero, quantized to 0.01
- lines sum to exactly 0 (debits == credits)
- only active, non-group accounts of the same ledger can be posted to
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Account, JournalLine, Ledger, Transaction

CENT = Decimal("0.01")
DEBIT_NORMAL = {"asset", "expense"}
PAYMENT_KINDS = {"bank", "credit_card", "cash"}


class AccountingError(ValueError):
    """Raised when a posting would violate an accounting invariant."""


def q(v: Decimal | int | float | str) -> Decimal:
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class LineIn:
    account_id: int
    amount: Decimal  # + debit / - credit
    memo: str | None = None
    orig_amount: Decimal | None = None
    orig_currency: str | None = None
    bank_entry_id: int | None = None
    invoice_id: int | None = None


def validate_lines(db: Session, ledger_id: int, lines: list[LineIn]) -> None:
    if len(lines) < 2:
        raise AccountingError("A transaction needs at least two lines")
    total = Decimal(0)
    ids = set()
    for ln in lines:
        ln.amount = q(ln.amount)
        if ln.amount == 0:
            raise AccountingError("Line amounts must be non-zero")
        total += ln.amount
        ids.add(ln.account_id)
    if total != 0:
        raise AccountingError(f"Transaction is not balanced (difference {total})")
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))}
    for aid in ids:
        acc = accounts.get(aid)
        if acc is None or acc.ledger_id != ledger_id:
            raise AccountingError(f"Unknown account {aid}")
        if acc.is_group:
            raise AccountingError(f"Account {acc.code} is a group account and cannot be posted to")
        if not acc.is_active:
            raise AccountingError(f"Account {acc.code} is inactive")


def set_lines(db: Session, tx: Transaction, lines: list[LineIn]) -> None:
    """Replace the lines of a transaction, preserving bank reconciliation links when given.

    Invoice links follow their account: a line left on the same account keeps its invoice (editing the
    memo or the date of an invoice payment must not unlink it)."""
    validate_lines(db, tx.ledger_id, lines)
    kept = {ln.account_id: ln.invoice_id for ln in tx.lines if ln.invoice_id}
    for ln in lines:
        if ln.invoice_id is None and ln.account_id in kept:
            ln.invoice_id = kept.pop(ln.account_id)
    tx.lines.clear()
    db.flush()
    for i, ln in enumerate(lines):
        tx.lines.append(
            JournalLine(
                ledger_id=tx.ledger_id,
                account_id=ln.account_id,
                position=i,
                amount=ln.amount,
                memo=ln.memo,
                orig_amount=ln.orig_amount,
                orig_currency=ln.orig_currency,
                bank_entry_id=ln.bank_entry_id,
                invoice_id=ln.invoice_id,
            )
        )
    db.flush()


def create_transaction(
    db: Session,
    ledger_id: int,
    date: dt.date,
    description: str,
    lines: list[LineIn],
    *,
    source: str = "manual",
    state: str = "confirmed",
    counterparty: str | None = None,
    notes: str | None = None,
    user_id: int | None = None,
) -> Transaction:
    tx = Transaction(
        ledger_id=ledger_id,
        date=date,
        description=description[:500],
        counterparty=counterparty,
        notes=notes,
        source=source,
        state=state,
        created_by=user_id,
    )
    db.add(tx)
    db.flush()
    set_lines(db, tx, lines)
    return tx


# --------------------------------------------------------------------------- accounts


def account_by_kind(db: Session, ledger_id: int, kind: str) -> Account:
    acc = db.scalar(
        select(Account)
        .where(Account.ledger_id == ledger_id, Account.kind == kind, Account.is_group.is_(False), Account.is_active)
        .order_by(Account.code)
        .limit(1)
    )
    if acc is None:
        raise AccountingError(f"No active '{kind}' account configured")
    return acc


def suspense_account(db: Session, ledger_id: int) -> Account:
    return account_by_kind(db, ledger_id, "suspense")


def equity_account(db: Session, ledger_id: int) -> Account:
    return account_by_kind(db, ledger_id, "equity")


def next_child_code(db: Session, parent: Account) -> str:
    codes = db.scalars(select(Account.code).where(Account.parent_id == parent.id)).all()
    n = 1
    for c in codes:
        m = re.fullmatch(re.escape(parent.code) + r"\.(\d+)", c)
        if m:
            n = max(n, int(m.group(1)) + 1)
    return f"{parent.code}.{n:02d}"


def next_sibling_code(db: Session, ledger_id: int, base: Account) -> str:
    """Next free integer code after ``base`` within the same parent (e.g. 1020 -> 1021)."""
    try:
        start = int(base.code)
    except ValueError:
        return next_child_code(db, base.parent) if base.parent else f"{base.code}-2"
    existing = set(db.scalars(select(Account.code).where(Account.ledger_id == ledger_id)).all())
    c = start + 1
    while str(c) in existing:
        c += 1
    return str(c)


def create_person_account(db: Session, ledger_id: int, name: str, owed_to_me: bool = True) -> Account:
    """Create a per-person sub-account under the 'advances to people' (asset) or
    'money owed to people' (liability) group."""
    typ = "asset" if owed_to_me else "liability"
    parent = db.scalar(
        select(Account)
        .where(Account.ledger_id == ledger_id, Account.kind == "receivable", Account.type == typ, Account.is_group)
        .order_by(Account.code)
        .limit(1)
    )
    if parent is None:
        raise AccountingError("No receivable group account configured")
    tokens = {t for t in re.split(r"[\s,]+", name.lower()) if len(t) > 1}
    acc = Account(
        ledger_id=ledger_id,
        parent_id=parent.id,
        code=next_child_code(db, parent),
        name=name,
        type=typ,
        kind="receivable",
        currency=parent.currency,
        keywords=" ".join(sorted(tokens)) or None,
    )
    db.add(acc)
    db.flush()
    return acc


# --------------------------------------------------------------------------- balances


def balances(
    db: Session, ledger_id: int, *, start: dt.date | None = None, end: dt.date | None = None
) -> dict[int, Decimal]:
    """Raw signed balance (debit positive) per account id for the given period."""
    stmt = (
        select(JournalLine.account_id, func.coalesce(func.sum(JournalLine.amount), 0))
        .join(Transaction, Transaction.id == JournalLine.transaction_id)
        .where(JournalLine.ledger_id == ledger_id)
        .group_by(JournalLine.account_id)
    )
    if start:
        stmt = stmt.where(Transaction.date >= start)
    if end:
        stmt = stmt.where(Transaction.date <= end)
    return {aid: Decimal(v) for aid, v in db.execute(stmt).all()}


def natural(amount: Decimal, account_type: str) -> Decimal:
    """Convert a raw debit-positive balance to the account's natural sign."""
    return amount if account_type in DEBIT_NORMAL else -amount


def rollup(accounts: list[Account], raw: dict[int, Decimal]) -> dict[int, Decimal]:
    """Natural-sign balances including group totals."""
    children: dict[int | None, list[Account]] = {}
    for a in accounts:
        children.setdefault(a.parent_id, []).append(a)
    out: dict[int, Decimal] = {}

    def walk(a: Account) -> Decimal:
        total = natural(raw.get(a.id, Decimal(0)), a.type)
        for c in children.get(a.id, []):
            total += walk(c)
        out[a.id] = total
        return total

    for root in children.get(None, []):
        walk(root)
    return out


# --------------------------------------------------------------------------- opening balances


def set_opening_balance(
    db: Session, account: Account, amount: Decimal | None, date: dt.date | None, user_id: int | None = None
) -> Transaction | None:
    """Create / update / remove the opening-balance transaction of an account.

    ``amount`` is in the account's natural sign (e.g. 500 on a bank account = 500 available,
    300 on a credit card = 300 owed). Counterpart is the equity account.
    """
    existing = db.scalar(
        select(Transaction)
        .join(JournalLine)
        .where(
            Transaction.ledger_id == account.ledger_id,
            Transaction.source == "opening",
            JournalLine.account_id == account.id,
        )
    )
    account.opening_balance = amount
    account.opening_date = date
    if amount is None or q(amount) == 0 or date is None:
        if existing:
            db.delete(existing)
            db.flush()
        return None
    raw = q(amount) if account.type in DEBIT_NORMAL else -q(amount)
    eq = equity_account(db, account.ledger_id)
    lines = [LineIn(account.id, raw, "Solde initial"), LineIn(eq.id, -raw, account.name)]
    label = f"Solde initial / Opening balance – {account.name}"
    if existing:
        existing.date = date
        existing.description = label
        set_lines(db, existing, lines)
        return existing
    return create_transaction(
        db, account.ledger_id, date, label, lines, source="opening", state="confirmed", user_id=user_id
    )


def get_setting(ledger: Ledger, key: str, default=None):
    from .seed import DEFAULT_SETTINGS

    return (ledger.settings or {}).get(key, DEFAULT_SETTINGS.get(key, default))
