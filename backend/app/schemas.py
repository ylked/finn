from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- accounts
class AccountOut(ORM):
    id: int
    parent_id: int | None
    code: str
    name: str
    type: str
    kind: str
    is_group: bool
    is_active: bool
    currency: str
    iban: str | None
    description: str | None
    keywords: str | None
    opening_balance: Decimal | None
    opening_date: dt.date | None
    balance: Decimal = Decimal(0)


class AccountIn(BaseModel):
    parent_id: int | None = None
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    type: str
    kind: str = "other"
    is_group: bool = False
    is_active: bool = True
    currency: str = "CHF"
    iban: str | None = None
    description: str | None = None
    keywords: str | None = None


class AccountPatch(BaseModel):
    parent_id: int | None = None
    code: str | None = None
    name: str | None = None
    kind: str | None = None
    is_active: bool | None = None
    iban: str | None = None
    description: str | None = None
    keywords: str | None = None


class OpeningIn(BaseModel):
    amount: Decimal | None
    date: dt.date | None


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owed_to_me: bool = True


# ---------------------------------------------------------------- transactions
class BankEntryOut(ORM):
    id: int
    account_id: int
    batch_id: int
    booking_date: dt.date
    value_date: dt.date | None
    amount: Decimal
    currency: str
    orig_amount: Decimal | None
    orig_currency: str | None
    description: str
    counterparty: str | None
    counterparty_iban: str | None
    remittance_info: str | None
    bank_ref: str | None
    tx_code: str | None


class LineOut(ORM):
    id: int
    account_id: int
    account_code: str
    account_name: str
    account_kind: str
    amount: Decimal
    memo: str | None
    orig_amount: Decimal | None
    orig_currency: str | None
    bank_entry_id: int | None
    invoice_id: int | None = None


class InvoiceBrief(BaseModel):
    id: int
    issuer: str | None
    number: str | None
    status: str
    due_date: dt.date | None
    amount: Decimal | None
    # booking: this transaction records the invoice; payment: it pays it
    role: str


class ReceiptBrief(ORM):
    id: int
    filename: str
    mime_type: str
    status: str
    kind: str = "receipt"


class MerchantBrief(BaseModel):
    id: int
    name: str
    logo_url: str | None = None


class TransactionOut(ORM):
    id: int
    date: dt.date
    description: str
    counterparty: str | None
    notes: str | None
    source: str
    state: str
    ai_status: str
    ai_confidence: float | None
    ai_reason: str | None
    lines: list[LineOut]
    receipts: list[ReceiptBrief] = []
    reconciled: bool = False
    merchant: MerchantBrief | None = None
    merchant_locked: bool = False
    stats_hidden: bool = False
    invoice: InvoiceBrief | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class TransactionDetail(TransactionOut):
    bank_entries: list[BankEntryOut] = []


class LineIn(BaseModel):
    account_id: int
    amount: Decimal  # + debit / - credit
    memo: str | None = None
    orig_amount: Decimal | None = None
    orig_currency: str | None = None
    bank_entry_id: int | None = None


class TransactionIn(BaseModel):
    date: dt.date
    description: str = Field(default="", max_length=500)
    counterparty: str | None = None
    notes: str | None = None
    lines: list[LineIn] = Field(min_length=2)
    confirm: bool = True


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int


class RuleIn(BaseModel):
    pattern: str = Field(min_length=2, max_length=255)
    direction: str = "any"
    source_account_id: int | None = None
    target_account_id: int
    priority: int = 100
    confirm: bool = False


class RuleOut(ORM):
    id: int
    pattern: str
    direction: str
    source_account_id: int | None
    target_account_id: int
    priority: int
    confirm: bool
    hits: int


# ---------------------------------------------------------------- imports
class BatchOut(ORM):
    id: int
    account_id: int
    kind: str
    filename: str
    statement_id: str | None
    from_date: dt.date | None
    to_date: dt.date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    entries_total: int
    entries_new: int
    entries_matched: int
    created_at: dt.datetime
    computed_balance: Decimal | None = None
    ledger_balance: Decimal | None = None
    balance_ok: bool | None = None


class CsvProfileIn(BaseModel):
    name: str
    config: dict


class CsvProfileOut(ORM):
    id: int
    name: str
    config: dict


# ---------------------------------------------------------------- receipts
class ReceiptOut(ORM):
    id: int
    transaction_id: int | None
    filename: str
    mime_type: str
    status: str
    extracted: dict | None
    error: str | None
    payment_account_id: int | None
    kind: str = "receipt"
    invoice_id: int | None = None
    created_at: dt.datetime


class LedgerSettingsIn(BaseModel):
    name: str | None = None
    locale: str | None = None
    settings: dict | None = None
