import datetime as dt
import hashlib
import uuid
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import AccountingError, LineIn, create_transaction, get_setting, q, suspense_account
from ..ai.receipts import apply_receipt_to_transaction
from ..auth import Ctx, ctx, writer
from ..config import get_settings
from ..db import get_db
from ..matching import receipt_candidates
from ..models import Account, Receipt
from ..schemas import ReceiptOut
from ..worker import worker
from .transactions import _load, to_out

router = APIRouter(prefix="/api/receipts", tags=["receipts"])

ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
MAX_SIZE = 25 * 1024 * 1024


def _get(db: Session, c: Ctx, rid: int) -> Receipt:
    r = db.get(Receipt, rid)
    if r is None or r.ledger_id != c.ledger.id:
        raise HTTPException(404, "Receipt not found")
    return r


def _sniff(content: bytes, declared: str | None, name: str) -> str | None:
    if content[:4] == b"%PDF":
        return "application/pdf"
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc", b"ftypmsf1"):
        return "image/heic"
    return None


@router.post("", response_model=list[ReceiptOut])
async def upload(
    files: list[UploadFile] = File(...),
    payment_account_id: int | None = Form(None),
    transaction_id: int | None = Form(None),
    c: Ctx = Depends(writer),
    db: Session = Depends(get_db),
):
    s = get_settings()
    base = Path(s.upload_dir) / str(c.ledger.id) / "receipts"
    base.mkdir(parents=True, exist_ok=True)
    tx = _load(db, c, transaction_id) if transaction_id else None
    out = []
    for f in files:
        content = await f.read(MAX_SIZE + 1)
        if len(content) > MAX_SIZE:
            raise HTTPException(413, f"{f.filename}: too large")
        mime = _sniff(content, f.content_type, f.filename or "")
        if mime not in ALLOWED:
            raise HTTPException(415, f"{f.filename}: unsupported file type (JPG, PNG, HEIC, WebP, PDF)")
        path = base / f"{uuid.uuid4().hex}{ALLOWED[mime]}"
        path.write_bytes(content)
        r = Receipt(
            ledger_id=c.ledger.id,
            filename=(f.filename or path.name)[:500],
            storage_path=str(path),
            mime_type=mime,
            sha256=hashlib.sha256(content).hexdigest(),
            status="pending" if s.vision_enabled else "manual",
            payment_account_id=payment_account_id,
            created_by=c.user.id,
        )
        if tx is not None:
            r.transaction_id = tx.id
            r.status = "matched"
        db.add(r)
        out.append(r)
    db.commit()
    worker.wake()
    return out


@router.get("", response_model=list[ReceiptOut])
def list_receipts(status: str | None = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    stmt = select(Receipt).where(Receipt.ledger_id == c.ledger.id)
    if status:
        stmt = stmt.where(Receipt.status.in_(status.split(",")))
    return db.scalars(stmt.order_by(Receipt.created_at.desc()).limit(300)).all()


@router.get("/{rid}", response_model=ReceiptOut)
def get_receipt(rid: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return _get(db, c, rid)


@router.get("/{rid}/file")
def get_file(rid: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    r = _get(db, c, rid)
    return FileResponse(r.storage_path, media_type=r.mime_type, filename=r.filename,
                        content_disposition_type="inline")


@router.get("/{rid}/candidates")
def candidates(rid: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    r = _get(db, c, rid)
    tol = int(get_setting(c.ledger, "match_tolerance_days", 7)) * 2
    return [to_out(t) for t in receipt_candidates(db, r, tol, limit=10)]


class LinkIn(BaseModel):
    transaction_id: int
    apply_splits: bool = True


@router.post("/{rid}/link", response_model=ReceiptOut)
def link(rid: int, body: LinkIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    r = _get(db, c, rid)
    tx = _load(db, c, body.transaction_id)
    if body.apply_splits:
        apply_receipt_to_transaction(db, r, tx)
    else:
        r.transaction_id, r.status = tx.id, "matched"
    db.commit()
    return r


@router.post("/{rid}/unlink", response_model=ReceiptOut)
def unlink(rid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    r = _get(db, c, rid)
    r.transaction_id = None
    r.status = "unmatched" if r.extracted else "manual"
    db.commit()
    return r


@router.post("/{rid}/retry", response_model=ReceiptOut)
def retry(rid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    r = _get(db, c, rid)
    if not get_settings().vision_enabled:
        raise HTTPException(409, "No vision model configured")
    r.status, r.attempts, r.error = "pending", 0, None
    db.commit()
    worker.wake()
    return r


class ReceiptDataIn(BaseModel):
    merchant: str | None = None
    date: dt.date | None = None
    total: Decimal | None = None
    currency: str | None = None
    splits: list[dict] | None = None


@router.patch("/{rid}", response_model=ReceiptOut)
def edit(rid: int, body: ReceiptDataIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Correct or enter receipt data manually (e.g. when AI is unavailable)."""
    r = _get(db, c, rid)
    data = dict(r.extracted or {})
    for k, v in body.model_dump(exclude_unset=True).items():
        data[k] = str(v) if isinstance(v, (Decimal, dt.date)) else v
    r.extracted = data
    if r.status in ("manual", "failed") and data.get("total"):
        r.status = "unmatched"
    db.commit()
    return r


class CreateTxIn(BaseModel):
    payment_account_id: int
    splits: list[dict] | None = None  # [{account_id, amount, memo}]


@router.post("/{rid}/transaction")
def create_tx(rid: int, body: CreateTxIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Book a receipt as an expected expense. It will reconcile with the bank line on import."""
    r = _get(db, c, rid)
    ex = r.extracted or {}
    if not ex.get("total"):
        raise HTTPException(422, "Receipt total unknown; edit the receipt first")
    pay = db.get(Account, body.payment_account_id)
    if pay is None or pay.ledger_id != c.ledger.id or pay.is_group:
        raise HTTPException(422, "Invalid payment account")
    total = q(ex["total"])
    lines = [LineIn(pay.id, -total)]
    if body.splits:
        for s_ in body.splits:
            lines.append(LineIn(int(s_["account_id"]), q(s_["amount"]), s_.get("memo")))
    elif ex.get("splits"):
        codes = {s_["account_code"]: s_ for s_ in ex["splits"]}
        accs = db.scalars(select(Account).where(Account.ledger_id == c.ledger.id, Account.code.in_(codes))).all()
        for a in accs:
            lines.append(LineIn(a.id, q(codes[a.code]["amount"]), codes[a.code].get("label")))
    if len(lines) < 2:
        lines.append(LineIn(suspense_account(db, c.ledger.id).id, total))
    try:
        date = dt.date.fromisoformat(str(ex.get("date"))[:10])
    except ValueError:
        date = dt.date.today()
    try:
        tx = create_transaction(
            db, c.ledger.id, date, str(ex.get("merchant") or r.filename), lines,
            source="receipt", state="confirmed", counterparty=ex.get("merchant"), user_id=c.user.id,
        )
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    if any(db.get(Account, ln.account_id).kind == "suspense" for ln in lines):
        tx.state = "unclassified"
    r.transaction_id, r.status = tx.id, "matched"
    db.commit()
    return to_out(_load(db, c, tx.id))


@router.delete("/{rid}")
def delete(rid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    r = _get(db, c, rid)
    try:
        Path(r.storage_path).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(r)
    db.commit()
    return {"ok": True}


