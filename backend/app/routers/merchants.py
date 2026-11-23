"""Merchants: list / edit / merge, logos, and assigning a merchant to transactions."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload, undefer

from ..auth import Ctx, ctx, current_user, writer
from ..db import get_db
from ..merchants import (
    IMAGE_TYPES,
    MAX_LOGO,
    Matcher,
    apply_pattern,
    brief,
    clean_domain,
    detect_all,
    ensure_logo,
    fold,
    learn_pattern,
    logo_url,
    normalize_patterns,
)
from ..models import Account, JournalLine, LedgerMember, Merchant, Transaction, User

router = APIRouter(prefix="/api", tags=["merchants"])


def spend_filter():
    """Lines that count as spending: expense accounts, and money waiting on the suspense account."""
    return or_(Account.type == "expense", (Account.kind == "suspense") & (JournalLine.amount > 0))


class MerchantIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    domain: str | None = None
    patterns: list[str] | None = None


class MerchantOut(BaseModel):
    id: int
    name: str
    domain: str | None
    patterns: list[str]
    catalog_key: str | None
    logo_url: str | None
    logo_status: str
    tx_count: int = 0
    total: Decimal = Decimal(0)
    last_date: dt.date | None = None


def _out(m: Merchant, stats: dict | None = None) -> MerchantOut:
    s = stats or {}
    return MerchantOut(id=m.id, name=m.name, domain=m.domain, patterns=list(m.patterns or []),
                       catalog_key=m.catalog_key, logo_url=logo_url(m), logo_status=m.logo_status,
                       tx_count=s.get("count", 0), total=s.get("total", Decimal(0)), last_date=s.get("last"))


def _get(db: Session, c: Ctx, mid: int) -> Merchant:
    m = db.get(Merchant, mid)
    if m is None or m.ledger_id != c.ledger.id:
        raise HTTPException(404, "Merchant not found")
    return m


def _stats(db: Session, ledger_id: int, ids: list[int] | None = None) -> dict[int, dict]:
    counts = select(Transaction.merchant_id, func.count(), func.max(Transaction.date)).where(
        Transaction.ledger_id == ledger_id, Transaction.merchant_id.is_not(None)
    )
    totals = (
        select(Transaction.merchant_id, func.sum(JournalLine.amount))
        .join(JournalLine, JournalLine.transaction_id == Transaction.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Transaction.ledger_id == ledger_id, Transaction.merchant_id.is_not(None), spend_filter())
    )
    if ids is not None:
        counts = counts.where(Transaction.merchant_id.in_(ids))
        totals = totals.where(Transaction.merchant_id.in_(ids))
    out: dict[int, dict] = {}
    for mid, n, last in db.execute(counts.group_by(Transaction.merchant_id)):
        out[mid] = {"count": n, "last": last, "total": Decimal(0)}
    for mid, total in db.execute(totals.group_by(Transaction.merchant_id)):
        out.setdefault(mid, {"count": 0, "last": None})["total"] = total or Decimal(0)
    return out


@router.get("/merchants", response_model=list[MerchantOut])
def list_merchants(q: str | None = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    stmt = select(Merchant).where(Merchant.ledger_id == c.ledger.id)
    if q:
        stmt = stmt.where(func.unaccent(Merchant.name).ilike(func.unaccent(f"%{q}%")))
    ms = db.scalars(stmt).all()
    st = _stats(db, c.ledger.id)
    res = [_out(m, st.get(m.id)) for m in ms]
    res.sort(key=lambda m: (-m.total, -m.tx_count, fold(m.name)))
    return res


@router.post("/merchants", response_model=MerchantOut)
def create_merchant(body: MerchantIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    if not body.name:
        raise HTTPException(422, "Name required")
    m = Merchant(ledger_id=c.ledger.id, name=body.name.strip(), domain=clean_domain(body.domain),
                 patterns=normalize_patterns(body.patterns if body.patterns is not None else [body.name]))
    db.add(m)
    db.commit()
    return _out(m)


@router.get("/merchants/{mid}", response_model=MerchantOut)
def get_merchant(mid: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    m = _get(db, c, mid)
    return _out(m, _stats(db, c.ledger.id, [m.id]).get(m.id))


@router.patch("/merchants/{mid}", response_model=MerchantOut)
def update_merchant(mid: int, body: MerchantIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    m = _get(db, c, mid)
    applied = 0
    if body.name is not None:
        m.name = body.name.strip()
    if body.domain is not None:
        d = clean_domain(body.domain) if body.domain.strip() else None
        if body.domain.strip() and d is None:
            raise HTTPException(422, "Invalid domain")
        if d != m.domain:
            m.domain = d
            if m.logo_status != "custom":
                m.logo, m.logo_mime, m.logo_status, m.logo_checked_at = None, None, "none", None
    if body.patterns is not None:
        old = set(m.patterns or [])
        m.patterns = normalize_patterns(body.patterns)
        db.flush()
        for p in m.patterns:
            if p not in old:
                applied += apply_pattern(db, c.ledger, m, p)
    db.commit()
    return _out(m, _stats(db, c.ledger.id, [m.id]).get(m.id))


def _ignore_catalog(c: Ctx, key: str | None) -> None:
    if key:
        ign = list((c.ledger.settings or {}).get("merchant_catalog_ignored", []))
        if key not in ign:
            c.ledger.settings = {**(c.ledger.settings or {}), "merchant_catalog_ignored": [*ign, key]}


@router.delete("/merchants/{mid}")
def delete_merchant(mid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Unlink its transactions (kept without merchant, not re-detected) and forget it."""
    m = _get(db, c, mid)
    for tx in db.scalars(select(Transaction).where(Transaction.merchant_id == m.id)):
        tx.merchant_id = None
        tx.merchant_locked = True
    _ignore_catalog(c, m.catalog_key)
    db.delete(m)
    db.commit()
    return {"ok": True}


class MergeIn(BaseModel):
    into_id: int


@router.post("/merchants/{mid}/merge", response_model=MerchantOut)
def merge_merchant(mid: int, body: MergeIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Move everything from merchant ``mid`` to ``into_id`` (patterns included), then delete ``mid``."""
    src, dst = _get(db, c, mid), _get(db, c, body.into_id)
    if src.id == dst.id:
        raise HTTPException(422, "Same merchant")
    for tx in db.scalars(select(Transaction).where(Transaction.merchant_id == src.id)):
        tx.merchant_id = dst.id
    dst.patterns = normalize_patterns([*(dst.patterns or []), *(src.patterns or [])])
    if not dst.domain and src.domain:
        dst.domain = src.domain
    _ignore_catalog(c, src.catalog_key)
    db.delete(src)
    db.commit()
    return _out(dst, _stats(db, c.ledger.id, [dst.id]).get(dst.id))


# ------------------------------------------------------------------ logos
@router.get("/merchants/{mid}/logo")
def get_logo(mid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # <img> requests carry no X-Ledger-Id header: check membership of the merchant's ledger instead
    m = db.scalar(select(Merchant).options(undefer(Merchant.logo)).where(Merchant.id == mid))
    if m is None or not db.get(LedgerMember, (m.ledger_id, user.id)):
        raise HTTPException(404, "Not found")
    if not ensure_logo(db, m) or not m.logo:
        return Response(status_code=404, headers={"Cache-Control": "private, max-age=3600"})
    return Response(m.logo, media_type=m.logo_mime or "image/png", headers={
        "Cache-Control": "private, max-age=2592000, immutable",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        "X-Content-Type-Options": "nosniff",
    })


@router.put("/merchants/{mid}/logo", response_model=MerchantOut)
async def upload_logo(mid: int, file: UploadFile = File(...), c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    m = _get(db, c, mid)
    data = await file.read(MAX_LOGO + 1)
    mime = (file.content_type or "").lower()
    if len(data) > MAX_LOGO:
        raise HTTPException(413, "Logo too large (max 512 KB)")
    if mime not in IMAGE_TYPES:
        raise HTTPException(415, "Unsupported image type")
    m.logo, m.logo_mime, m.logo_status = data, mime, "custom"
    m.logo_checked_at = dt.datetime.now(dt.UTC)
    db.commit()
    return _out(m)


@router.delete("/merchants/{mid}/logo", response_model=MerchantOut)
def reset_logo(mid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Forget the stored logo; it will be fetched again from the domain."""
    m = _get(db, c, mid)
    m.logo, m.logo_mime, m.logo_status, m.logo_checked_at = None, None, "none", None
    db.commit()
    return _out(m)


# ------------------------------------------------------------------ detection & assignment
class DetectIn(BaseModel):
    redo: bool = False  # also re-detect transactions that already have a (non-locked) merchant


@router.post("/merchants/detect")
def detect(body: DetectIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    n = detect_all(db, c.ledger, only_missing=not body.redo)
    db.commit()
    return {"updated": n}


class TxMerchantIn(BaseModel):
    merchant_id: int | None = None
    name: str | None = Field(default=None, max_length=120)  # create a new merchant with this name
    learn: bool = False  # recognise similar transactions as this merchant from now on


def _resolve(db: Session, c: Ctx, body: TxMerchantIn) -> Merchant | None:
    if body.merchant_id:
        return _get(db, c, body.merchant_id)
    if body.name and body.name.strip():
        return Matcher(db, c.ledger).named(body.name.strip())
    return None


@router.put("/transactions/{tx_id}/merchant")
def set_tx_merchant(tx_id: int, body: TxMerchantIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    tx = db.scalar(select(Transaction).options(selectinload(Transaction.lines).selectinload(JournalLine.account))
                   .where(Transaction.id == tx_id, Transaction.ledger_id == c.ledger.id))
    if tx is None:
        raise HTTPException(404, "Transaction not found")
    m = _resolve(db, c, body)
    tx.merchant_id = m.id if m else None
    tx.merchant_locked = True
    applied = 0
    if body.learn and m is not None:
        p = learn_pattern(tx)
        if p:
            m.patterns = normalize_patterns([*(m.patterns or []), p])
            db.flush()
            applied = apply_pattern(db, c.ledger, m, p)
    db.commit()
    return {"merchant": brief(m), "applied": applied}


class BulkMerchantIn(TxMerchantIn):
    ids: list[int] = Field(min_length=1, max_length=1000)


@router.post("/transactions/bulk-merchant")
def bulk_merchant(body: BulkMerchantIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    m = _resolve(db, c, body)
    txs = db.scalars(select(Transaction).where(Transaction.ledger_id == c.ledger.id, Transaction.id.in_(body.ids))).all()
    for tx in txs:
        tx.merchant_id = m.id if m else None
        tx.merchant_locked = True
    db.commit()
    return {"updated": len(txs), "merchant": brief(m)}


@router.post("/transactions/{tx_id}/merchant/reset")
def reset_tx_merchant(tx_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Back to automatic detection."""
    tx = db.scalar(select(Transaction).options(selectinload(Transaction.lines).selectinload(JournalLine.account))
                   .where(Transaction.id == tx_id, Transaction.ledger_id == c.ledger.id))
    if tx is None:
        raise HTTPException(404, "Transaction not found")
    tx.merchant_locked = False
    tx.merchant_id = None
    m = Matcher(db, c.ledger).detect(tx)
    db.commit()
    return {"merchant": brief(m), "applied": 0}
