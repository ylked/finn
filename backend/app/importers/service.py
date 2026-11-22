"""Turns parsed statement entries into bank entries + balanced journal transactions."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..accounting import LineIn, create_transaction, get_setting, set_opening_balance, suspense_account
from ..ai.classify import apply_rules
from ..ai.receipts import rematch_unmatched
from ..matching import find_line_match
from ..models import Account, BankEntry, ImportBatch, JournalLine, Ledger, Transaction
from .camt053 import ParsedEntry, ParsedStatement


class DuplicateFile(ValueError):
    pass


@dataclass
class ImportResult:
    batch_id: int | None
    account_id: int
    filename: str
    total: int = 0
    new: int = 0
    duplicates: int = 0
    zero: int = 0  # 0.00 lines (e.g. month-end closing entry without fees): no effect, not booked
    matched: int = 0
    classified_by_rule: int = 0
    errors: list[str] = field(default_factory=list)
    opening_set: bool = False


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def book_entry(
    db: Session, ledger: Ledger, account: Account, be: BankEntry, user_id: int | None, *, allow_match: bool = True
) -> tuple[str, Transaction]:
    """Reconcile with an existing line or create a new suspense transaction. Returns (how, tx)."""
    if allow_match and get_setting(ledger, "auto_match", True):
        tol = int(get_setting(ledger, "match_tolerance_days", 7))
        line = find_line_match(
            db, ledger.id, account.id, be.amount, be.booking_date,
            f"{be.counterparty or ''} {be.description}", tol,
        )
        if line is not None:
            line.bank_entry_id = be.id
            if be.orig_amount is not None and line.orig_amount is None:
                line.orig_amount, line.orig_currency = be.orig_amount, be.orig_currency
            return "matched", line.transaction

    suspense = suspense_account(db, ledger.id)
    tx = create_transaction(
        db,
        ledger.id,
        be.booking_date,
        be.description[:500] or (be.counterparty or ""),
        [
            LineIn(account.id, be.amount, None, be.orig_amount, be.orig_currency, be.id),
            LineIn(suspense.id, -be.amount),
        ],
        source="bank" if be.batch.kind == "camt053" else "csv",
        state="unclassified",
        counterparty=be.counterparty,
        user_id=user_id,
    )
    if apply_rules(db, tx):
        tx.ai_status = "done"
        return "rule", tx
    tx.ai_status = "pending" if get_setting(ledger, "auto_classify", True) else "none"
    return "new", tx


def import_entries(
    db: Session,
    ledger: Ledger,
    account: Account,
    *,
    kind: str,
    filename: str,
    file_hash: str,
    entries: list[ParsedEntry],
    statement: ParsedStatement | None = None,
    user_id: int | None = None,
) -> ImportResult:
    if db.scalar(select(ImportBatch.id).where(ImportBatch.ledger_id == ledger.id, ImportBatch.file_sha256 == file_hash)):
        raise DuplicateFile(filename)
    batch = ImportBatch(
        ledger_id=ledger.id,
        account_id=account.id,
        kind=kind,
        filename=filename[:500],
        file_sha256=file_hash,
        statement_id=statement.statement_id if statement else None,
        from_date=statement.from_date if statement else (min((e.booking_date for e in entries), default=None)),
        to_date=statement.to_date if statement else (max((e.booking_date for e in entries), default=None)),
        opening_balance=statement.opening_balance if statement else None,
        closing_balance=statement.closing_balance if statement else None,
        created_by=user_id,
    )
    db.add(batch)
    db.flush()
    res = ImportResult(batch.id, account.id, filename, total=len(entries))

    # remember the account holder's name(s): money from/to them is an own-account transfer
    if statement and statement.owner:
        names = list((ledger.settings or {}).get("owner_names", []))
        if statement.owner not in names:
            ledger.settings = {**(ledger.settings or {}), "owner_names": [*names, statement.owner]}

    # Opening balance from the earliest camt statement, unless configured manually earlier
    if statement and statement.opening_balance is not None and statement.from_date:
        opening_date = statement.from_date - dt.timedelta(days=1)
        has_earlier_entries = db.scalar(
            select(func.count(BankEntry.id)).where(
                BankEntry.account_id == account.id, BankEntry.booking_date < statement.from_date
            )
        )
        if not has_earlier_entries and (account.opening_date is None or opening_date < account.opening_date):
            set_opening_balance(db, account, statement.opening_balance, opening_date, user_id)
            res.opening_set = True

    existing = set(
        db.scalars(
            select(BankEntry.dedup_key).where(
                BankEntry.account_id == account.id, BankEntry.dedup_key.in_([e.dedup_key for e in entries])
            )
        )
    )
    for e in entries:
        if e.amount == 0:
            res.zero += 1
            continue
        if e.dedup_key in existing:
            res.duplicates += 1
            continue
        existing.add(e.dedup_key)
        be = BankEntry(
            ledger_id=ledger.id,
            account_id=account.id,
            batch_id=batch.id,
            booking_date=e.booking_date,
            value_date=e.value_date,
            amount=e.amount,
            currency=e.currency or account.currency,
            orig_amount=e.orig_amount,
            orig_currency=e.orig_currency,
            exchange_rate=e.exchange_rate,
            description=e.description,
            counterparty=e.counterparty,
            counterparty_iban=e.counterparty_iban,
            remittance_info=e.remittance_info,
            bank_ref=e.bank_ref,
            end_to_end_id=e.end_to_end_id,
            tx_code=e.tx_code,
            raw=e.raw,
            dedup_key=e.dedup_key,
        )
        be.batch = batch
        db.add(be)
        db.flush()
        how, _ = book_entry(db, ledger, account, be, user_id)
        res.new += 1
        if how == "matched":
            res.matched += 1
        elif how == "rule":
            res.classified_by_rule += 1

    batch.entries_total = res.total
    batch.entries_new = res.new
    batch.entries_matched = res.matched
    db.flush()
    rematch_unmatched(db, ledger.id)
    return res


def reconciliation(db: Session, account: Account, at: dt.date) -> dict:
    """Balances of an account at a date: bank view (opening + statement entries) vs ledger view."""
    opening = Decimal(0)
    if account.opening_balance is not None and account.opening_date and account.opening_date <= at:
        opening = account.opening_balance
    entries_sum = db.scalar(
        select(func.coalesce(func.sum(BankEntry.amount), 0)).where(
            BankEntry.account_id == account.id, BankEntry.booking_date <= at
        )
    )
    raw_ledger = db.scalar(
        select(func.coalesce(func.sum(JournalLine.amount), 0))
        .join(Transaction)
        .where(JournalLine.account_id == account.id, Transaction.date <= at)
    )
    ledger_bal = Decimal(raw_ledger) if account.type in ("asset", "expense") else -Decimal(raw_ledger)
    sign = 1 if account.type in ("asset", "expense") else -1
    unbooked = db.scalar(
        select(func.count(BankEntry.id))
        .outerjoin(JournalLine, JournalLine.bank_entry_id == BankEntry.id)
        .where(BankEntry.account_id == account.id, JournalLine.id.is_(None))
    )
    return {
        "date": at,
        "statement_balance": opening + sign * Decimal(entries_sum),
        "ledger_balance": ledger_bal,
        "unbooked_entries": unbooked,
    }
