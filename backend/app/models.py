"""Database models.

Every business table carries ``ledger_id`` so a single deployment can host several
independent (or shared) sets of books. Access is granted through ``LedgerMember``.

Amounts are stored as NUMERIC(14,2). Journal lines use a single signed ``amount``:
positive = debit, negative = credit. A transaction is balanced when its lines sum to 0,
which is enforced by a deferred constraint trigger in the database (see migration 0001).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    LargeBinary,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

Money = Numeric(14, 2)

ACCOUNT_TYPES = ("asset", "liability", "income", "expense")
# kind refines the role of an account; it drives matching, UI and AI hints
# payable = bills received and not paid yet (see invoices.py)
ACCOUNT_KINDS = ("bank", "credit_card", "cash", "receivable", "payable", "equity", "suspense", "other")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    oidc_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(8), default="fr")
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    memberships: Mapped[list[LedgerMember]] = relationship(back_populates="user")


class Ledger(TimestampMixin, Base):
    __tablename__ = "ledgers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    base_currency: Mapped[str] = mapped_column(String(3), default="CHF")
    locale: Mapped[str] = mapped_column(String(8), default="fr")
    # auto_classify, auto_match, match_tolerance_days, ...
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    members: Mapped[list[LedgerMember]] = relationship(back_populates="ledger", cascade="all, delete-orphan")


class LedgerMember(Base):
    __tablename__ = "ledger_members"
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="owner")  # owner | editor | viewer
    ledger: Mapped[Ledger] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")
    __table_args__ = (CheckConstraint("role in ('owner','editor','viewer')", name="ck_member_role"),)


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16), default="other")
    # group accounts only aggregate children and cannot receive postings
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    currency: Mapped[str] = mapped_column(String(3), default="CHF")
    iban: Mapped[str | None] = mapped_column(String(34))
    description: Mapped[str | None] = mapped_column(Text)
    # free text keywords helping matching and AI (e.g. person name variants for receivables)
    keywords: Mapped[str | None] = mapped_column(Text)
    opening_balance: Mapped[Decimal | None] = mapped_column(Money)
    opening_date: Mapped[dt.date | None] = mapped_column(Date)

    parent: Mapped[Account | None] = relationship(remote_side=[id])

    __table_args__ = (
        UniqueConstraint("ledger_id", "code", name="uq_account_code"),
        CheckConstraint(f"type in {ACCOUNT_TYPES}", name="ck_account_type"),
        CheckConstraint(f"kind in {ACCOUNT_KINDS}", name="ck_account_kind"),
        Index("ix_account_iban", "ledger_id", "iban"),
    )


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    counterparty: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    # bank | csv | manual | receipt | opening
    source: Mapped[str] = mapped_column(String(16), default="manual")
    # classification state: unclassified (on suspense account) | auto (AI/rule, to review) | confirmed
    state: Mapped[str] = mapped_column(String(16), default="confirmed", index=True)
    ai_status: Mapped[str] = mapped_column(String(16), default="none", index=True)  # none|pending|done|failed|skipped
    ai_confidence: Mapped[float | None]
    ai_reason: Mapped[str | None] = mapped_column(Text)
    ai_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # who was paid (Migros, Netflix, ...); merchant_locked = chosen by the user, never re-detected
    merchant_id: Mapped[int | None] = mapped_column(ForeignKey("merchants.id", ondelete="SET NULL"), index=True)
    merchant_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # left out of the insights lists (largest expenses, merchants); totals still count it
    stats_hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    merchant: Mapped[Merchant | None] = relationship()
    lines: Mapped[list[JournalLine]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", order_by="JournalLine.position"
    )
    receipts: Mapped[list[Receipt]] = relationship(back_populates="transaction")

    __table_args__ = (
        CheckConstraint("state in ('unclassified','auto','confirmed')", name="ck_tx_state"),
        CheckConstraint("source in ('bank','csv','manual','receipt','opening','invoice')", name="ck_tx_source"),
        CheckConstraint("ai_status in ('none','pending','done','failed','skipped')", name="ck_tx_ai_status"),
        Index("ix_tx_ledger_date", "ledger_id", "date"),
    )


class Merchant(TimestampMixin, Base):
    """A shop / payee recognised from statement texts (see merchants.py)."""

    __tablename__ = "merchants"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # website, used to fetch the logo (its favicon / apple-touch-icon)
    domain: Mapped[str | None] = mapped_column(String(253))
    # folded match patterns (see merchants.match), one per entry
    patterns: Mapped[list] = mapped_column(JSON, default=list)
    catalog_key: Mapped[str | None] = mapped_column(String(50))
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    logo_mime: Mapped[str | None] = mapped_column(String(50))
    # none (not tried) | ok | failed | custom (uploaded by the user)
    logo_status: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    logo_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("ledger_id", "catalog_key", name="uq_merchant_catalog"),)


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[Decimal] = mapped_column(Money)  # + debit / - credit
    memo: Mapped[str | None] = mapped_column(String(500))
    orig_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    orig_currency: Mapped[str | None] = mapped_column(String(3))
    # set when this line has been reconciled with a bank/card statement entry
    bank_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_entries.id", ondelete="SET NULL"), unique=True
    )
    # on the payable account: this line books (credit) or settles (debit) that invoice
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), index=True)

    transaction: Mapped[Transaction] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()
    bank_entry: Mapped[BankEntry | None] = relationship(back_populates="line")
    invoice: Mapped[Invoice | None] = relationship()

    __table_args__ = (CheckConstraint("amount <> 0", name="ck_line_nonzero"),)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(16))  # camt053 | csv
    filename: Mapped[str] = mapped_column(String(500))
    file_sha256: Mapped[str] = mapped_column(String(64))
    statement_id: Mapped[str | None] = mapped_column(String(100))
    from_date: Mapped[dt.date | None] = mapped_column(Date)
    to_date: Mapped[dt.date | None] = mapped_column(Date)
    opening_balance: Mapped[Decimal | None] = mapped_column(Money)
    closing_balance: Mapped[Decimal | None] = mapped_column(Money)
    entries_total: Mapped[int] = mapped_column(Integer, default=0)
    entries_new: Mapped[int] = mapped_column(Integer, default=0)
    entries_matched: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    account: Mapped[Account] = relationship()

    __table_args__ = (UniqueConstraint("ledger_id", "file_sha256", name="uq_batch_file"),)


class BankEntry(Base):
    """A raw line from a bank / card statement. Immutable once imported."""

    __tablename__ = "bank_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    booking_date: Mapped[dt.date] = mapped_column(Date)
    value_date: Mapped[dt.date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)  # signed: + money in, - money out (account holder view)
    currency: Mapped[str] = mapped_column(String(3), default="CHF")
    orig_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    orig_currency: Mapped[str | None] = mapped_column(String(3))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    description: Mapped[str] = mapped_column(Text, default="")
    counterparty: Mapped[str | None] = mapped_column(String(255))
    counterparty_iban: Mapped[str | None] = mapped_column(String(34))
    remittance_info: Mapped[str | None] = mapped_column(Text)
    bank_ref: Mapped[str | None] = mapped_column(String(100))
    end_to_end_id: Mapped[str | None] = mapped_column(String(100))
    tx_code: Mapped[str | None] = mapped_column(String(50))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    dedup_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    line: Mapped[JournalLine | None] = relationship(back_populates="bank_entry")
    batch: Mapped[ImportBatch] = relationship()

    __table_args__ = (UniqueConstraint("account_id", "dedup_key", name="uq_bank_entry_dedup"),)


class CsvProfile(TimestampMixin, Base):
    __tablename__ = "csv_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # see importers/csv_import.py: CsvMapping
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class ClassificationRule(TimestampMixin, Base):
    __tablename__ = "classification_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    pattern: Mapped[str] = mapped_column(String(255))  # case-insensitive substring
    direction: Mapped[str] = mapped_column(String(8), default="any")  # any | in | out
    source_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    target_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    confirm: Mapped[bool] = mapped_column(Boolean, default=False)  # mark as confirmed instead of auto
    hits: Mapped[int] = mapped_column(Integer, default=0)
    target_account: Mapped[Account] = relationship(foreign_keys=[target_account_id])
    __table_args__ = (CheckConstraint("direction in ('any','in','out')", name="ck_rule_direction"),)


class Receipt(TimestampMixin, Base):
    __tablename__ = "receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    storage_path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    # pending (awaiting AI) | unmatched | matched | failed | manual (AI unavailable)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    extracted: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # receipt (ticket) | invoice | payment (proof of payment) | other
    kind: Mapped[str] = mapped_column(String(16), default="receipt", server_default="receipt")
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), index=True)
    # payment account hint chosen at upload (optional)
    payment_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    transaction: Mapped[Transaction | None] = relationship(back_populates="receipts")

    __table_args__ = (
        CheckConstraint(
            "status in ('pending','unmatched','matched','failed','manual')", name="ck_receipt_status"
        ),
        CheckConstraint("kind in ('receipt','invoice','payment','other')", name="ck_receipt_kind"),
    )


class Invoice(TimestampMixin, Base):
    """A bill to pay. Once complete it is booked as expense / payable (``transaction``) on its issue
    date; payments are lines on the payable account carrying ``invoice_id`` (see invoices.py)."""

    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    # draft (being read / incomplete, not booked) | open | paid
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    issuer: Mapped[str | None] = mapped_column(String(255))
    merchant_id: Mapped[int | None] = mapped_column(ForeignKey("merchants.id", ondelete="SET NULL"))
    number: Mapped[str | None] = mapped_column(String(100))
    # payment reference of the QR-bill / BVR, used to recognise the payment in statements
    reference: Mapped[str | None] = mapped_column(String(64))
    iban: Mapped[str | None] = mapped_column(String(34))
    issue_date: Mapped[dt.date | None] = mapped_column(Date)
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="CHF", server_default="CHF")
    # expense category
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    description: Mapped[str | None] = mapped_column(Text)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"),
                                                       index=True)
    paid_date: Mapped[dt.date | None] = mapped_column(Date)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    merchant: Mapped[Merchant | None] = relationship()
    account: Mapped[Account | None] = relationship()
    transaction: Mapped[Transaction | None] = relationship()
    documents: Mapped[list[Receipt]] = relationship(order_by="Receipt.id")

    __table_args__ = (
        CheckConstraint("status in ('draft','open','paid')", name="ck_invoice_status"),
        Index("ix_invoice_ledger_status", "ledger_id", "status", "due_date"),
    )


class ApiToken(Base):
    """Personal access token for programmatic clients (MCP). Only a SHA-256 hash is stored."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))  # first chars, shown in the UI to recognise a token
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
