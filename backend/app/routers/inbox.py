"""Document inbox for scripts and Apple Shortcuts: send a PDF or photo with a personal access token.

    POST /api/inbox?kind=receipt|invoice
    Authorization: Bearer finn_...        (a token with write access, from Settings)

The body is either a multipart form (any field holding a file; several files allowed) or the
raw file itself (Shortcuts "Request Body: File"), named by ``?filename=`` if given.

- ``receipt`` (default): goes to the receipts queue; the AI reads it and matches it to an entry.
- ``invoice``: becomes an invoice to pay; the AI reads amount, due date and category.
"""

from __future__ import annotations

import datetime as dt
import io

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile as StarletteUpload

from ..auth import Ctx
from ..config import get_settings
from ..db import get_db
from ..models import ApiToken, Invoice, Ledger, LedgerMember, User
from ..worker import worker
from .receipts import MAX_SIZE, store_upload
from .tokens import PREFIX, hash_token

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


def token_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """Context from a personal access token with write access (no session, no CSRF header needed)."""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token.startswith(PREFIX):
        raise HTTPException(401, "Missing token: send 'Authorization: Bearer finn_…'")
    t = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    now = dt.datetime.now(dt.UTC)
    if t is None or (t.expires_at and t.expires_at < now):
        raise HTTPException(401, "Invalid or expired token")
    if not t.can_write:
        raise HTTPException(403, "This token is read-only; create one with write access")
    m = db.scalar(select(LedgerMember).where(LedgerMember.user_id == t.user_id, LedgerMember.ledger_id == t.ledger_id))
    if m is None or m.role not in ("owner", "editor"):
        raise HTTPException(403, "No write access to this ledger")
    t.last_used_at = now
    return Ctx(user=db.get(User, t.user_id), ledger=db.get(Ledger, t.ledger_id), role=m.role)


async def _files(request: Request, filename: str | None) -> list[UploadFile]:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form(max_files=20)
        files = [v for _, v in form.multi_items() if isinstance(v, StarletteUpload)]
        if not files:
            raise HTTPException(422, "The form has no file")
        return files
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_SIZE:
            raise HTTPException(413, "File too large")
    if not body:
        raise HTTPException(422, "Empty body: send a file")
    name = filename or f"shortcut-{dt.datetime.now():%Y%m%d-%H%M%S}"
    return [StarletteUpload(io.BytesIO(bytes(body)), filename=name, headers=Headers({"content-type": ctype}))]


@router.post("")
async def inbox(request: Request, kind: str = "receipt", filename: str | None = None,
                c: Ctx = Depends(token_ctx), db: Session = Depends(get_db)):
    if kind not in ("receipt", "invoice"):
        raise HTTPException(422, "kind must be 'receipt' or 'invoice'")
    s = get_settings()
    files = await _files(request, filename)
    ids = []
    for f in files:
        if kind == "invoice":
            inv = Invoice(ledger_id=c.ledger.id, currency=c.ledger.base_currency, created_by=c.user.id)
            db.add(inv)
            db.flush()
            r = await store_upload(db, c, f, kind="invoice")
            r.invoice_id = inv.id
            ids.append(inv.id)
        else:
            r = await store_upload(db, c, f, kind="receipt")
            db.flush()
            ids.append(r.id)
    db.commit()
    worker.wake()
    n = len(ids)
    base = s.app_base_url.rstrip("/")
    if kind == "invoice":
        msg = f"{n} facture(s) ajoutée(s) / invoice(s) added"
        url = f"{base}/invoices?open={ids[0]}" if n == 1 else f"{base}/invoices?tab=draft"
    else:
        msg = f"{n} justificatif(s) ajouté(s) / receipt(s) added"
        url = f"{base}/receipts?status=pending"
    if s.vision_enabled:
        msg += " · lecture IA en cours / AI reading"
    return {"ok": True, "kind": kind, "count": n, "ids": ids, "message": msg, "url": url}
