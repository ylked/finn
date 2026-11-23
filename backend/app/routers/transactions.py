import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import accounting as acc_svc
from ..accounting import AccountingError, LineIn
from ..ai.classify import apply_rules_to_unclassified, split_lines
from ..auth import Ctx, ctx, writer
from ..db import get_db
from ..importers.service import book_entry
from ..merchants import Matcher, brief
from ..models import Account, BankEntry, ClassificationRule, Invoice, JournalLine, Transaction
from ..schemas import (
    BankEntryOut,
    InvoiceBrief,
    LineOut,
    ReceiptBrief,
    RuleIn,
    RuleOut,
    TransactionDetail,
    TransactionIn,
    TransactionOut,
    TransactionPage,
)
from ..worker import worker

router = APIRouter(prefix="/api", tags=["transactions"])


def _load(db: Session, c: Ctx, tx_id: int, lock: bool = False) -> Transaction:
    q = (
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(JournalLine.account), selectinload(Transaction.receipts),
                 selectinload(Transaction.merchant))
        .where(Transaction.id == tx_id, Transaction.ledger_id == c.ledger.id)
    )
    if lock:
        q = q.with_for_update(of=Transaction)
    tx = db.scalar(q)
    if tx is None:
        raise HTTPException(404, "Transaction not found")
    return tx


def to_out(tx: Transaction, cls=TransactionOut):
    lines = [
        LineOut(
            id=ln.id,
            account_id=ln.account_id,
            account_code=ln.account.code,
            account_name=ln.account.name,
            account_kind=ln.account.kind,
            amount=ln.amount,
            memo=ln.memo,
            orig_amount=ln.orig_amount,
            orig_currency=ln.orig_currency,
            bank_entry_id=ln.bank_entry_id,
            invoice_id=ln.invoice_id,
        )
        for ln in tx.lines
    ]
    inv_line = next((ln for ln in tx.lines if ln.invoice_id), None)
    invoice = None
    if inv_line is not None and inv_line.invoice is not None:
        i = inv_line.invoice
        invoice = InvoiceBrief(id=i.id, issuer=i.issuer, number=i.number, status=i.status, due_date=i.due_date,
                               amount=i.amount, role="booking" if i.transaction_id == tx.id else "payment")
    pay = [ln for ln in tx.lines if ln.account.kind in acc_svc.PAYMENT_KINDS]
    data = dict(
        id=tx.id,
        date=tx.date,
        description=tx.description,
        counterparty=tx.counterparty,
        notes=tx.notes,
        source=tx.source,
        state=tx.state,
        ai_status=tx.ai_status,
        ai_confidence=tx.ai_confidence,
        ai_reason=tx.ai_reason,
        lines=lines,
        receipts=[ReceiptBrief.model_validate(r) for r in tx.receipts],
        reconciled=bool(pay) and all(ln.bank_entry_id for ln in pay),
        merchant=brief(tx.merchant),
        merchant_locked=tx.merchant_locked,
        stats_hidden=tx.stats_hidden,
        invoice=invoice,
        created_at=tx.created_at,
        updated_at=tx.updated_at,
    )
    if cls is TransactionDetail:
        data["bank_entries"] = [BankEntryOut.model_validate(ln.bank_entry) for ln in tx.lines if ln.bank_entry]
    return cls(**data)


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    account_id: int | None = None,
    state: list[str] = Query(default=[]),
    source: str | None = None,
    q: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    unreconciled: bool = False,
    amount: Decimal | None = None,
    merchant_id: int | None = None,
    stats_hidden: bool | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    c: Ctx = Depends(ctx),
    db: Session = Depends(get_db),
):
    stmt = select(Transaction).where(Transaction.ledger_id == c.ledger.id)
    if account_id:
        ids = [account_id] + _descendants(db, c.ledger.id, account_id)
        stmt = stmt.where(
            exists().where(JournalLine.transaction_id == Transaction.id, JournalLine.account_id.in_(ids))
        )
    if state:
        stmt = stmt.where(Transaction.state.in_(state))
    if merchant_id:
        stmt = stmt.where(Transaction.merchant_id == merchant_id)
    if stats_hidden is not None:
        stmt = stmt.where(Transaction.stats_hidden.is_(stats_hidden))
    if source:
        stmt = stmt.where(Transaction.source == source)
    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    if amount is not None:
        stmt = stmt.where(
            exists().where(JournalLine.transaction_id == Transaction.id, func.abs(JournalLine.amount) == abs(amount))
        )
    if q:
        # accent-insensitive: "electro" finds "Électronique"
        like = func.unaccent(f"%{q}%")
        stmt = stmt.where(
            or_(*(func.unaccent(col).ilike(like) for col in (Transaction.description, Transaction.counterparty, Transaction.notes)))
        )
    if unreconciled:
        stmt = stmt.where(
            exists()
            .where(JournalLine.transaction_id == Transaction.id, JournalLine.bank_entry_id.is_(None))
            .where(JournalLine.account_id.in_(select(Account.id).where(Account.kind.in_(acc_svc.PAYMENT_KINDS))))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.options(
            selectinload(Transaction.lines).selectinload(JournalLine.account), selectinload(Transaction.receipts),
            selectinload(Transaction.merchant),
        )
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return TransactionPage(items=[to_out(t) for t in rows], total=total)


def _descendants(db: Session, ledger_id: int, account_id: int) -> list[int]:
    accounts = db.execute(select(Account.id, Account.parent_id).where(Account.ledger_id == ledger_id)).all()
    children: dict[int, list[int]] = {}
    for aid, pid in accounts:
        if pid:
            children.setdefault(pid, []).append(aid)
    out, stack = [], [account_id]
    while stack:
        for ch in children.get(stack.pop(), []):
            out.append(ch)
            stack.append(ch)
    return out


@router.post("/transactions/apply-rules")
def apply_rules_now(c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    n = apply_rules_to_unclassified(db, c.ledger.id)
    db.commit()
    return {"classified": n}


@router.get("/transactions/stats")
def stats(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Transaction.state, func.count()).where(Transaction.ledger_id == c.ledger.id).group_by(Transaction.state)
    ).all()
    pending_ai = db.scalar(
        select(func.count()).where(Transaction.ledger_id == c.ledger.id, Transaction.ai_status == "pending")
    )
    return {"by_state": dict(rows), "ai_pending": pending_ai}


@router.get("/transactions/{tx_id}", response_model=TransactionDetail)
def get_transaction(tx_id: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return to_out(_load(db, c, tx_id), TransactionDetail)


def _lines(body: TransactionIn) -> list[LineIn]:
    return [
        LineIn(ln.account_id, ln.amount, ln.memo, ln.orig_amount, ln.orig_currency, ln.bank_entry_id)
        for ln in body.lines
    ]


@router.post("/transactions", response_model=TransactionDetail)
def create(body: TransactionIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    if any(ln.bank_entry_id for ln in body.lines):
        raise HTTPException(422, "Bank links are created by imports only")
    try:
        tx = acc_svc.create_transaction(
            db,
            c.ledger.id,
            body.date,
            body.description,
            _lines(body),
            source="manual",
            state="confirmed",
            counterparty=body.counterparty,
            notes=body.notes,
            user_id=c.user.id,
        )
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    Matcher(db, c.ledger).detect(tx)
    db.commit()
    return to_out(_load(db, c, tx.id), TransactionDetail)


@router.put("/transactions/{tx_id}", response_model=TransactionDetail)
def update(tx_id: int, body: TransactionIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    tx = _load(db, c, tx_id, lock=True)
    if tx.source == "opening":
        raise HTTPException(409, "Edit opening balances from the account settings")
    # statement-backed lines are immutable: same account, same amount, still present
    linked = {ln.bank_entry_id: ln for ln in tx.lines if ln.bank_entry_id}
    incoming = {ln.bank_entry_id: ln for ln in body.lines if ln.bank_entry_id}
    if set(incoming) != set(linked):
        raise HTTPException(422, "Lines coming from a bank statement cannot be removed or added")
    for be_id, ln in linked.items():
        new = incoming[be_id]
        if new.account_id != ln.account_id or acc_svc.q(new.amount) != ln.amount:
            raise HTTPException(422, "Account and amount of statement lines cannot be changed")
    invoices = {ln.invoice_id for ln in tx.lines if ln.invoice_id}
    try:
        acc_svc.set_lines(db, tx, _lines(body))
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    _refresh_invoices(db, invoices)
    text_changed = (tx.description, tx.counterparty) != (body.description, body.counterparty)
    tx.date = body.date
    tx.description = body.description
    tx.counterparty = body.counterparty
    tx.notes = body.notes
    if text_changed or tx.merchant_id is None:
        db.flush()
        db.refresh(tx, ["lines"])
        Matcher(db, c.ledger).detect(tx)
    suspense_ids = set(
        db.scalars(select(Account.id).where(Account.ledger_id == c.ledger.id, Account.kind == "suspense"))
    )
    if any(ln.account_id in suspense_ids for ln in body.lines):
        tx.state = "unclassified"
    else:
        tx.state = "confirmed" if body.confirm else "auto"
    if tx.ai_status == "pending":
        tx.ai_status = "done"
    db.commit()
    return to_out(_load(db, c, tx.id), TransactionDetail)


@router.post("/transactions/confirm")
def confirm_many(ids: list[int], c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    txs = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(JournalLine.account))
        .where(Transaction.ledger_id == c.ledger.id, Transaction.id.in_(ids))
    ).all()
    n = 0
    for tx in txs:
        if tx.state == "auto" and not any(ln.account.kind == "suspense" for ln in tx.lines):
            tx.state = "confirmed"
            n += 1
    db.commit()
    return {"confirmed": n}


class BulkAccountIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    account_id: int
    # None: set the counter side (everything except the bank/card side), single-line counter sides only.
    # Given: replace this account by account_id on every line that uses it (works inside splits too).
    from_account_id: int | None = None
    confirm: bool = True


@router.post("/transactions/bulk-account")
def bulk_account(body: BulkAccountIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Move many entries to another account at once. Statement-backed lines are never changed."""
    target = db.get(Account, body.account_id)
    if target is None or target.ledger_id != c.ledger.id or target.is_group or not target.is_active:
        raise HTTPException(422, "Invalid target account")
    txs = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(JournalLine.account))
        .where(Transaction.ledger_id == c.ledger.id, Transaction.id.in_(body.ids))
        .with_for_update(of=Transaction)
    ).all()
    updated, skipped = 0, []
    for tx in txs:
        if tx.source == "opening":
            skipped.append({"id": tx.id, "reason": "opening"})
            continue
        if body.from_account_id is not None:
            lines = [ln for ln in tx.lines if ln.account_id == body.from_account_id]
            if not lines:
                skipped.append({"id": tx.id, "reason": "account_not_used"})
                continue
            if any(ln.bank_entry_id for ln in lines):
                skipped.append({"id": tx.id, "reason": "statement_line"})
                continue
        else:
            src, _ = split_lines(tx)
            others = [ln for ln in tx.lines if ln is not src and not ln.bank_entry_id]
            if src is None or len(others) != 1:
                skipped.append({"id": tx.id, "reason": "split" if src is not None else "no_payment_side"})
                continue
            lines = others
        if any(ln.account_id == target.id for ln in tx.lines if ln not in lines):
            # would put both sides on the same account
            skipped.append({"id": tx.id, "reason": "same_account"})
            continue
        for ln in lines:
            ln.account_id = target.id
            ln.account = target
        if any(ln.account.kind == "suspense" for ln in tx.lines):
            tx.state = "unclassified"
        elif body.confirm:
            tx.state = "confirmed"
        elif tx.state == "unclassified":
            tx.state = "auto"
        if tx.ai_status == "pending":
            tx.ai_status = "done"
        updated += 1
    db.commit()
    return {"updated": updated, "skipped": skipped}


class StatsHiddenIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    hidden: bool = True


@router.post("/transactions/stats-hidden")
def set_stats_hidden(body: StatsHiddenIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Hide entries from the insights lists (largest expenses, merchants); totals keep counting them."""
    txs = db.scalars(select(Transaction).where(Transaction.ledger_id == c.ledger.id, Transaction.id.in_(body.ids)))
    n = 0
    for tx in txs:
        tx.stats_hidden = body.hidden
        n += 1
    db.commit()
    return {"updated": n}


@router.post("/transactions/{tx_id}/reclassify", response_model=TransactionDetail)
def reclassify(tx_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Put the counter side back on suspense and queue the AI again."""
    tx = _load(db, c, tx_id, lock=True)
    src, _ = split_lines(tx)
    if src is None:
        raise HTTPException(422, "Only transactions with a payment account can be re-classified")
    susp = acc_svc.suspense_account(db, c.ledger.id)
    lines = [LineIn(src.account_id, src.amount, src.memo, src.orig_amount, src.orig_currency, src.bank_entry_id),
             LineIn(susp.id, -src.amount)]
    acc_svc.set_lines(db, tx, lines)
    tx.state = "unclassified"
    tx.ai_status = "pending"
    tx.ai_attempts = 0
    tx.ai_reason = None
    db.commit()
    worker.wake()
    return to_out(_load(db, c, tx.id), TransactionDetail)


@router.delete("/transactions/{tx_id}")
def delete(tx_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    tx = _load(db, c, tx_id, lock=True)
    if any(ln.bank_entry_id for ln in tx.lines):
        raise HTTPException(
            409, "This transaction is backed by a bank statement; unlink the statement line or delete the import"
        )
    if tx.source == "opening":
        raise HTTPException(409, "Remove opening balances from the account settings")
    for r in tx.receipts:
        r.transaction_id = None
        r.status = "unmatched" if r.extracted else "manual"
    invoices = {ln.invoice_id for ln in tx.lines if ln.invoice_id}
    for inv in db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)):
        inv.transaction_id = None  # deleting the booking turns the invoice back into a draft
    db.delete(tx)
    db.flush()
    _refresh_invoices(db, invoices)
    db.commit()
    return {"ok": True}


def _refresh_invoices(db: Session, ids: set[int]) -> None:
    from ..invoices import refresh

    for inv in db.scalars(select(Invoice).where(Invoice.id.in_(ids or {0}))):
        refresh(db, inv)


@router.post("/transactions/{tx_id}/lines/{line_id}/unlink", response_model=TransactionDetail)
def unlink(tx_id: int, line_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Undo a wrong automatic match: the statement entry gets its own new transaction."""
    tx = _load(db, c, tx_id, lock=True)
    ln = next((x for x in tx.lines if x.id == line_id), None)
    if ln is None or ln.bank_entry_id is None:
        raise HTTPException(404, "Line is not linked to a statement entry")
    be = db.get(BankEntry, ln.bank_entry_id)
    if tx.source in ("bank", "csv") and sum(1 for x in tx.lines if x.bank_entry_id) == 1:
        raise HTTPException(409, "This transaction was created from that statement entry; nothing to unlink")
    ln.bank_entry_id = None
    db.flush()
    _, new_tx = book_entry(db, c.ledger, db.get(Account, be.account_id), be, c.user.id, allow_match=False)
    db.commit()
    worker.wake()
    return to_out(_load(db, c, new_tx.id), TransactionDetail)


@router.post("/transactions/{tx_id}/rule", response_model=RuleOut)
def rule_from_tx(tx_id: int, body: RuleIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    _load(db, c, tx_id)
    return create_rule(body, c, db)


# ---------------------------------------------------------------- unbooked statement entries
@router.get("/bank-entries/unbooked", response_model=list[BankEntryOut])
def unbooked(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return db.scalars(
        select(BankEntry)
        .outerjoin(JournalLine, JournalLine.bank_entry_id == BankEntry.id)
        .where(BankEntry.ledger_id == c.ledger.id, JournalLine.id.is_(None))
        .order_by(BankEntry.booking_date)
    ).all()


@router.post("/bank-entries/{entry_id}/book", response_model=TransactionDetail)
def book(entry_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    be = db.get(BankEntry, entry_id)
    if be is None or be.ledger_id != c.ledger.id:
        raise HTTPException(404, "Entry not found")
    if be.line is not None:
        raise HTTPException(409, "Entry already booked")
    _, tx = book_entry(db, c.ledger, db.get(Account, be.account_id), be, c.user.id)
    db.commit()
    worker.wake()
    return to_out(_load(db, c, tx.id), TransactionDetail)


@router.post("/bank-entries/{entry_id}/link/{line_id}", response_model=TransactionDetail)
def link_entry(entry_id: int, line_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Manually reconcile a statement entry with an existing unreconciled line."""
    be = db.get(BankEntry, entry_id)
    ln = db.get(JournalLine, line_id)
    if be is None or ln is None or be.ledger_id != c.ledger.id or ln.ledger_id != c.ledger.id:
        raise HTTPException(404, "Not found")
    if ln.bank_entry_id or ln.account_id != be.account_id or ln.amount != be.amount:
        raise HTTPException(422, "Line must be on the same account, same amount, and not reconciled yet")
    # if the entry is currently booked on its own auto-created transaction, drop that one
    if be.line is not None:
        old_tx = be.line.transaction
        if old_tx.source in ("bank", "csv") and sum(1 for x in old_tx.lines if x.bank_entry_id) == 1:
            be.line.bank_entry_id = None
            db.flush()
            db.delete(old_tx)
            db.flush()
        else:
            raise HTTPException(409, "Entry is already reconciled with another transaction")
    ln.bank_entry_id = be.id
    db.commit()
    return to_out(_load(db, c, ln.transaction_id), TransactionDetail)


@router.get("/transactions/{tx_id}/match-candidates")
def match_candidates(tx_id: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """For a manual transaction: statement entries that could reconcile its payment line(s)."""
    tx = _load(db, c, tx_id)
    out = []
    for ln in tx.lines:
        if ln.bank_entry_id or ln.account.kind not in acc_svc.PAYMENT_KINDS:
            continue
        entries = db.scalars(
            select(BankEntry)
            .where(
                BankEntry.account_id == ln.account_id,
                BankEntry.amount == ln.amount,
                BankEntry.booking_date.between(tx.date - dt.timedelta(days=30), tx.date + dt.timedelta(days=30)),
            )
            .order_by(func.abs(BankEntry.booking_date - tx.date))
            .limit(10)
        ).all()
        out.append({"line_id": ln.id, "entries": [BankEntryOut.model_validate(e) for e in entries]})
    return out


# ---------------------------------------------------------------- rules
@router.get("/rules", response_model=list[RuleOut])
def list_rules(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return db.scalars(
        select(ClassificationRule)
        .where(ClassificationRule.ledger_id == c.ledger.id)
        .order_by(ClassificationRule.priority, ClassificationRule.id)
    ).all()


@router.post("/rules", response_model=RuleOut)
def create_rule(body: RuleIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    target = db.get(Account, body.target_account_id)
    if target is None or target.ledger_id != c.ledger.id or target.is_group:
        raise HTTPException(422, "Invalid target account")
    if body.direction not in ("any", "in", "out"):
        raise HTTPException(422, "Invalid direction")
    r = ClassificationRule(ledger_id=c.ledger.id, **body.model_dump())
    db.add(r)
    db.flush()
    apply_rules_to_unclassified(db, c.ledger.id)
    db.commit()
    return r


@router.put("/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    r = db.get(ClassificationRule, rule_id)
    if r is None or r.ledger_id != c.ledger.id:
        raise HTTPException(404, "Rule not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    db.commit()
    return r


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    r = db.get(ClassificationRule, rule_id)
    if r is None or r.ledger_id != c.ledger.id:
        raise HTTPException(404, "Rule not found")
    db.delete(r)
    db.commit()
    return {"ok": True}
