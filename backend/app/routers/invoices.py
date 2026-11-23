"""Invoices: upload (AI reading), manual entry, debt booking, payment linking."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import invoices as svc
from ..accounting import AccountingError, q
from ..auth import Ctx, ctx, writer
from ..config import get_settings
from ..db import get_db
from ..merchants import brief, clean_domain
from ..models import Account, Invoice
from ..schemas import ReceiptOut
from ..worker import worker
from .receipts import store_upload
from .transactions import _load, to_out

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _get(db: Session, c: Ctx, iid: int) -> Invoice:
    inv = db.get(Invoice, iid)
    if inv is None or inv.ledger_id != c.ledger.id:
        raise HTTPException(404, "Invoice not found")
    return inv


def _out(db: Session, inv: Invoice, rem: Decimal | None = None, detail: bool = False) -> dict:
    rem = svc.remaining(db, inv) if rem is None else rem
    today = dt.date.today()
    reading = any(d.status == "pending" for d in inv.documents)
    out = {
        "id": inv.id,
        "status": inv.status,
        "reading": reading,
        "issuer": inv.issuer,
        "merchant": brief(inv.merchant),
        "number": inv.number,
        "reference": inv.reference,
        "iban": inv.iban,
        "issue_date": inv.issue_date,
        "due_date": inv.due_date,
        "amount": inv.amount,
        "currency": inv.currency,
        "account_id": inv.account_id,
        "account": {"id": inv.account.id, "code": inv.account.code, "name": inv.account.name} if inv.account else None,
        "description": inv.description,
        "transaction_id": inv.transaction_id,
        # booked over several categories: edit the split in the entry
        "split": bool(inv.transaction) and sum(
            1 for ln in inv.transaction.lines if ln.invoice_id != inv.id and ln.amount > 0) > 1,
        "paid_date": inv.paid_date,
        "remaining": rem if inv.status != "draft" else inv.amount,
        "overdue": inv.status == "open" and inv.due_date is not None and inv.due_date < today,
        "days_left": (inv.due_date - today).days if inv.due_date and inv.status == "open" else None,
        "error": inv.error,
        "documents": [ReceiptOut.model_validate(d).model_dump() for d in inv.documents],
        "created_at": inv.created_at,
    }
    if detail:
        out["payments"] = [
            {"transaction": to_out(_load_tx(db, ln.transaction_id)), "amount": ln.amount}
            for ln in svc.payment_lines(db, inv)
        ]
    return out


def _load_tx(db: Session, tx_id: int):
    from sqlalchemy.orm import selectinload as sl

    from ..models import JournalLine, Transaction

    return db.scalar(select(Transaction).options(
        sl(Transaction.lines).selectinload(JournalLine.account), sl(Transaction.receipts), sl(Transaction.merchant),
    ).where(Transaction.id == tx_id))


@router.get("")
def list_invoices(status: str | None = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    invs = list(db.scalars(
        select(Invoice)
        .options(selectinload(Invoice.documents), selectinload(Invoice.merchant), selectinload(Invoice.account))
        .where(Invoice.ledger_id == c.ledger.id)
    ))
    rems = svc.remaining_many(db, [i.id for i in invs])
    changed = False
    for inv in invs:
        # keep the stored status in line with the books (a booking or payment may have been edited)
        before = inv.status
        if inv.transaction_id is None:
            inv.status = "draft"
        else:
            r = rems.get(inv.id, Decimal(0))
            inv.status = "paid" if r <= 0 else "open"
        if inv.status != before:
            svc.refresh(db, inv, rems.get(inv.id))
            changed = True
    if changed:
        db.commit()
    if status:
        wanted = set(status.split(","))
        invs = [i for i in invs if i.status in wanted]
    far = dt.date(9999, 1, 1)
    # open: soonest deadline first; paid: most recent first
    invs.sort(key=lambda i: ({"draft": 0, "open": 1, "paid": 2}[i.status],
                             (i.due_date or far) if i.status == "open" else far,
                             -(i.paid_date or i.issue_date or dt.date.min).toordinal(), -i.id))
    return [_out(db, i, rems.get(i.id, i.amount or Decimal(0))) for i in invs]


@router.get("/summary")
def summary(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return invoice_summary(db, c.ledger.id)


def invoice_summary(db: Session, ledger_id: int) -> dict:
    invs = list(db.scalars(select(Invoice).where(Invoice.ledger_id == ledger_id, Invoice.status.in_(["open", "draft"]))))
    rems = svc.remaining_many(db, [i.id for i in invs if i.status == "open"])
    today = dt.date.today()
    open_ = [i for i in invs if i.status == "open"]
    over = [i for i in open_ if i.due_date and i.due_date < today]
    soon = [i for i in open_ if i.due_date and today <= i.due_date <= today + dt.timedelta(days=7)]
    tot = lambda xs: sum((rems.get(i.id, Decimal(0)) for i in xs), Decimal(0))  # noqa: E731
    upcoming = sorted(open_, key=lambda i: i.due_date or dt.date(9999, 1, 1))[:5]
    return {
        "open_count": len(open_), "open_total": tot(open_),
        "overdue_count": len(over), "overdue_total": tot(over),
        "due_soon_count": len(soon), "due_soon_total": tot(soon),
        "drafts": len(invs) - len(open_),
        "upcoming": [{"id": i.id, "issuer": i.issuer, "number": i.number, "due_date": i.due_date,
                      "remaining": rems.get(i.id, Decimal(0)), "overdue": bool(i.due_date and i.due_date < today),
                      "merchant": brief(i.merchant)} for i in upcoming],
    }


@router.get("/{iid}")
def get_invoice(iid: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    inv = _get(db, c, iid)
    return _out(db, inv, detail=True)


class InvoiceIn(BaseModel):
    issuer: str | None = Field(None, max_length=255)
    number: str | None = Field(None, max_length=100)
    reference: str | None = Field(None, max_length=64)
    iban: str | None = Field(None, max_length=34)
    issue_date: dt.date | None = None
    due_date: dt.date | None = None
    amount: Decimal | None = Field(None, gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    account_id: int | None = None
    description: str | None = None
    website: str | None = None
    # an existing entry (expense / bills payable) that records this invoice
    transaction_id: int | None = None


def _apply(db: Session, c: Ctx, inv: Invoice, body: InvoiceIn) -> None:
    data = body.model_dump(exclude_unset=True)
    domain = data.pop("website", None)
    adopt_id = data.pop("transaction_id", None)
    if "account_id" in data and data["account_id"] is not None:
        a = db.get(Account, data["account_id"])
        if a is None or a.ledger_id != c.ledger.id or a.is_group:
            raise HTTPException(422, "Invalid account")
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip() or None
        if k == "amount" and v is not None:
            v = q(v)
        if k == "currency":
            v = (v or c.ledger.base_currency).upper()
        if k == "issuer" and v != inv.issuer:
            inv.merchant_id = None  # re-detect the payee
        setattr(inv, k, v)
    if inv.issue_date and inv.due_date and inv.due_date < inv.issue_date:
        raise HTTPException(422, "The due date is before the invoice date")
    try:
        if adopt_id and adopt_id != inv.transaction_id:
            if inv.transaction_id is not None:
                raise AccountingError("The invoice is already booked")
            svc.adopt(db, c.ledger, inv, _load(db, c, adopt_id, lock=True))
        was_booked = inv.transaction_id is not None
        svc.book(db, c.ledger, inv, user_id=c.user.id, domain=clean_domain(domain) if domain else None)
        if inv.transaction_id is not None:
            inv.error = None
            if not was_booked:
                svc.after_booking(db, c.ledger, inv)
            elif inv.transaction.state == "auto":
                inv.transaction.state = "confirmed"  # the user has checked it
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e


@router.post("")
def create(body: InvoiceIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Enter an invoice by hand. It is booked as soon as the amount is known."""
    inv = Invoice(ledger_id=c.ledger.id, currency=c.ledger.base_currency, created_by=c.user.id)
    db.add(inv)
    db.flush()
    _apply(db, c, inv, body)
    db.commit()
    return _out(db, inv, detail=True)


@router.patch("/{iid}")
def update(iid: int, body: InvoiceIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    inv = _get(db, c, iid)
    _apply(db, c, inv, body)
    db.commit()
    db.refresh(inv)
    return _out(db, inv, detail=True)


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...), c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """One invoice per file; the AI reads them in the background, then the debt is booked."""
    s = get_settings()
    out = []
    for f in files:
        inv = Invoice(ledger_id=c.ledger.id, currency=c.ledger.base_currency, created_by=c.user.id)
        db.add(inv)
        db.flush()
        r = await store_upload(db, c, f, kind="invoice")
        r.invoice_id = inv.id
        r.status = "pending" if s.vision_enabled else "manual"
        if not s.vision_enabled:
            inv.error = None
        out.append(inv)
    db.commit()
    worker.wake()
    return [_out(db, i) for i in out]


@router.post("/{iid}/documents", response_model=list[ReceiptOut])
async def add_documents(iid: int, files: list[UploadFile] = File(...), kind: str = Form("payment"),
                        c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """More documents: a proof of payment, a reminder, a second page..."""
    inv = _get(db, c, iid)
    if kind not in ("invoice", "payment", "other"):
        raise HTTPException(422, "Invalid kind")
    out = []
    for f in files:
        r = await store_upload(db, c, f, kind=kind)
        r.invoice_id, r.status = inv.id, "matched"
        out.append(r)
    db.commit()
    return out


@router.post("/{iid}/read")
def read_again(iid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Read the invoice document again with the AI (fills only empty fields)."""
    inv = _get(db, c, iid)
    if not get_settings().vision_enabled:
        raise HTTPException(409, "No vision model configured")
    doc = next((d for d in inv.documents if d.kind == "invoice"), None)
    if doc is None:
        raise HTTPException(422, "No invoice document")
    doc.status, doc.attempts, doc.error = "pending", 0, None
    inv.error = None
    db.commit()
    worker.wake()
    return _out(db, inv)


@router.get("/{iid}/candidates")
def payment_candidates(iid: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    inv = _get(db, c, iid)
    if inv.transaction_id is None:
        return []
    return [to_out(_load_tx(db, t.id)) for t in svc.candidates(db, c.ledger, inv)]


class PaymentIn(BaseModel):
    transaction_id: int


@router.post("/{iid}/payments")
def link_payment(iid: int, body: PaymentIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    inv = _get(db, c, iid)
    tx = _load(db, c, body.transaction_id, lock=True)
    try:
        svc.link_payment(db, c.ledger, inv, tx)
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return _out(db, inv, detail=True)


@router.delete("/{iid}/payments/{tx_id}")
def unlink_payment(iid: int, tx_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    inv = _get(db, c, iid)
    tx = _load(db, c, tx_id, lock=True)
    try:
        svc.unlink_payment(db, c.ledger, inv, tx)
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return _out(db, inv, detail=True)


class PayIn(BaseModel):
    account_id: int
    date: dt.date | None = None


@router.post("/{iid}/pay")
def pay(iid: int, body: PayIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Mark as paid from an account; the statement line will reconcile with it on import."""
    inv = _get(db, c, iid)
    try:
        svc.pay_from(db, c.ledger, inv, body.account_id, body.date or dt.date.today(), c.user.id)
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return _out(db, inv, detail=True)


@router.delete("/{iid}")
def delete(iid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Delete the invoice, its booking and its documents. Linked payments go back to 'to classify'."""
    inv = _get(db, c, iid)
    for ln in svc.payment_lines(db, inv):
        tx = _load(db, c, ln.transaction_id, lock=True)
        if tx.id != inv.transaction_id:
            svc.unlink_payment(db, c.ledger, inv, tx)
    if inv.transaction_id:
        tx = _load(db, c, inv.transaction_id, lock=True)
        for r in tx.receipts:
            r.transaction_id = None
        db.delete(tx)
    for d in inv.documents:
        try:
            Path(d.storage_path).unlink(missing_ok=True)
        except OSError:
            pass
        db.delete(d)
    db.flush()
    db.delete(inv)
    db.commit()
    return {"ok": True}
