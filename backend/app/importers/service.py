"""Turns parsed statement entries into bank entries + balanced journal transactions."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..accounting import LineIn, create_transaction, get_setting, set_opening_balance, suspense_account
from ..ai.classify import apply_rules
from ..ai.receipts import rematch_unmatched
from ..config import get_settings
from ..matching import find_line_match
from ..merchants import Matcher
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


REDATE_SQL = """
UPDATE transactions t SET date = sub.d, updated_at = now()
FROM (
    SELECT jl.transaction_id,
           min(CASE WHEN :use_value THEN coalesce(be.value_date, be.booking_date) ELSE be.booking_date END) AS d
    FROM journal_lines jl JOIN bank_entries be ON be.id = jl.bank_entry_id
    WHERE jl.ledger_id = :ledger_id
    GROUP BY jl.transaction_id
) sub
WHERE t.id = sub.transaction_id AND t.ledger_id = :ledger_id AND t.source IN ('bank', 'csv') AND t.date <> sub.d
"""


def entry_date(ledger: Ledger, be: BankEntry) -> dt.date:
    """Date given to a transaction created from a statement line (ledger setting ``bank_date``)."""
    if get_setting(ledger, "bank_date", "value") == "value" and be.value_date:
        return be.value_date
    return be.booking_date


def redate_bank_transactions(db: Session, ledger: Ledger) -> int:
    """Re-date transactions created from statement lines after the ``bank_date`` setting changed.
    Manual entries that were only matched to a statement line keep their own date."""
    use_value = get_setting(ledger, "bank_date", "value") == "value"
    return db.execute(text(REDATE_SQL), {"use_value": use_value, "ledger_id": ledger.id}).rowcount


def book_entry(
    db: Session, ledger: Ledger, account: Account, be: BankEntry, user_id: int | None, *, allow_match: bool = True,
    matcher: Matcher | None = None,
) -> tuple[str, Transaction]:
    """Reconcile with an existing line or create a new suspense transaction. Returns (how, tx)."""
    if allow_match and get_setting(ledger, "auto_match", True):
        tol = int(get_setting(ledger, "match_tolerance_days", 7))
        line = find_line_match(
            db, ledger.id, account.id, be.amount, entry_date(ledger, be),
            f"{be.counterparty or ''} {be.description}", tol,
        )
        if line is not None:
            line.bank_entry_id = be.id
            if be.orig_amount is not None and line.orig_amount is None:
                line.orig_amount, line.orig_currency = be.orig_amount, be.orig_currency
            if line.transaction.merchant_id is None:
                (matcher or Matcher(db, ledger)).detect(line.transaction, guess=False)
            return "matched", line.transaction

    suspense = suspense_account(db, ledger.id)
    tx = create_transaction(
        db,
        ledger.id,
        entry_date(ledger, be),
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
    matcher = matcher or Matcher(db, ledger)
    if allow_match and get_setting(ledger, "auto_match", True):
        from ..invoices import link_payment, match_entry

        inv = match_entry(db, ledger, tx, be)
        if inv is not None:
            db.refresh(tx, ["lines"])
            link_payment(db, ledger, inv, tx)
            tx.ai_status = "done"
            return "invoice", tx
    if apply_rules(db, tx):
        tx.ai_status = "done"
        matcher.detect(tx)
        return "rule", tx
    tx.ai_status = "pending" if get_setting(ledger, "auto_classify", True) else "none"
    # when the AI will look at it, let it name unknown merchants instead of guessing from the text now
    matcher.detect(tx, guess=not (tx.ai_status == "pending" and get_settings().ai_enabled))
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
    matcher = Matcher(db, ledger)
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
        how, _ = book_entry(db, ledger, account, be, user_id, matcher=matcher)
        res.new += 1
        if how == "matched":
            res.matched += 1
        elif how in ("rule", "invoice"):
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
