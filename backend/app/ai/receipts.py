"""Receipt reading (vision model) and matching to booked transactions."""

from __future__ import annotations

import base64
import io
import json
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import PAYMENT_KINDS, AccountingError, LineIn, get_setting, q, set_lines
from ..config import get_settings
from ..matching import receipt_candidates
from ..models import Account, Ledger, Receipt, Transaction
from .client import AIError, chat_json

log = logging.getLogger(__name__)

try:  # HEIC support (iPhone photos)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover
    pass

MAX_SIDE = 1600


def _image_to_jpeg_b64(img: Image.Image) -> str:
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def file_to_images(path: Path, mime: str) -> list[str]:
    """Return base64 JPEGs (max 3 pages for PDFs)."""
    if mime == "application/pdf" or path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        out = []
        for i in range(min(len(pdf), 3)):
            out.append(_image_to_jpeg_b64(pdf[i].render(scale=2).to_pil()))
        return out
    with Image.open(path) as img:
        return [_image_to_jpeg_b64(img)]


PROMPT = """Read this receipt / invoice and return JSON only:
{{"merchant": str, "date": "YYYY-MM-DD", "total": number (total paid, positive), "currency": "ISO code",
"payment_method": str|null, "items": [{{"description": str, "amount": number}}],
"splits": [{{"account_code": str, "amount": number, "label": str}}],
"confidence": 0..1}}
"splits" distributes the TOTAL over expense accounts from this chart (amounts must sum to total, \
use a single split when everything belongs to one category): {chart}
Default currency if not printed: {ccy}."""


def extract(db: Session, receipt: Receipt) -> dict:
    s = get_settings()
    ledger = db.get(Ledger, receipt.ledger_id)
    accounts = db.scalars(
        select(Account).where(
            Account.ledger_id == receipt.ledger_id,
            Account.is_group.is_(False),
            Account.is_active,
            Account.type.in_(["expense", "asset"]),
            Account.kind.in_(["other", "receivable"]),
        )
    ).all()
    chart = json.dumps([{"code": a.code, "name": a.name} for a in accounts], ensure_ascii=False)
    images = file_to_images(Path(receipt.storage_path), receipt.mime_type)
    content: list[dict] = [{"type": "text", "text": PROMPT.format(chart=chart, ccy=ledger.base_currency)}]
    for b64 in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    data = chat_json(s.ai_vision_model, [{"role": "user", "content": content}])
    # normalise
    try:
        data["total"] = str(q(data.get("total")))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise AIError("Receipt total not readable") from e
    valid_codes = {a.code for a in accounts}
    splits = []
    for sp in data.get("splits") or []:
        try:
            if sp.get("account_code") in valid_codes and q(sp.get("amount")) != 0:
                splits.append({"account_code": sp["account_code"], "amount": str(q(sp["amount"])),
                               "label": str(sp.get("label") or "")[:200]})
        except (InvalidOperation, TypeError, ValueError):
            continue
    if splits and sum(Decimal(s_["amount"]) for s_ in splits) != Decimal(data["total"]):
        # fix rounding on the largest split, drop if far off
        diff = Decimal(data["total"]) - sum(Decimal(s_["amount"]) for s_ in splits)
        if abs(diff) <= Decimal("0.05"):
            big = max(splits, key=lambda s_: abs(Decimal(s_["amount"])))
            big["amount"] = str(Decimal(big["amount"]) + diff)
        else:
            splits = []
    data["splits"] = splits
    data["currency"] = (data.get("currency") or ledger.base_currency).upper()[:3]
    return data


def apply_receipt_to_transaction(db: Session, receipt: Receipt, tx: Transaction, *, force: bool = False) -> bool:
    """Attach receipt and, if the transaction is not confirmed yet, apply the receipt's splits
    (ventilation) on the non-payment side. Returns True if lines were changed."""
    receipt.transaction_id = tx.id
    receipt.status = "matched"
    splits = (receipt.extracted or {}).get("splits") or []
    if not splits or (tx.state == "confirmed" and not force):
        return False
    pay_lines = [ln for ln in tx.lines if ln.account.kind in PAYMENT_KINDS or ln.bank_entry_id]
    if len(pay_lines) != 1:
        return False
    pay = pay_lines[0]
    total = -pay.amount  # positive for a purchase
    split_total = sum(Decimal(s["amount"]) for s in splits)
    if abs(split_total) != abs(total):
        return False
    sign = 1 if total > 0 else -1
    codes = {s["account_code"] for s in splits}
    accs = {a.code: a for a in db.scalars(
        select(Account).where(Account.ledger_id == tx.ledger_id, Account.code.in_(codes)))}
    lines = [LineIn(pay.account_id, pay.amount, pay.memo, pay.orig_amount, pay.orig_currency, pay.bank_entry_id)]
    for s in splits:
        acc = accs.get(s["account_code"])
        if acc is None:
            return False
        lines.append(LineIn(acc.id, sign * abs(Decimal(s["amount"])), s.get("label") or None))
    try:
        set_lines(db, tx, lines)
    except AccountingError:
        return False
    tx.state = "auto" if not force else tx.state
    merchant = (receipt.extracted or {}).get("merchant")
    tx.ai_reason = f"Reçu / receipt: {merchant or receipt.filename}"
    if merchant and not tx.counterparty:
        tx.counterparty = str(merchant)[:255]
    return True


def try_match(db: Session, receipt: Receipt) -> Transaction | None:
    ledger = db.get(Ledger, receipt.ledger_id)
    if not get_setting(ledger, "auto_match", True):
        return None
    tol = int(get_setting(ledger, "match_tolerance_days", 7))
    cands = receipt_candidates(db, receipt, tol, limit=2)
    if not cands:
        return None
    apply_receipt_to_transaction(db, receipt, cands[0])
    return cands[0]


def rematch_unmatched(db: Session, ledger_id: int) -> int:
    n = 0
    for r in db.scalars(select(Receipt).where(Receipt.ledger_id == ledger_id, Receipt.status == "unmatched")):
        if try_match(db, r):
            n += 1
    return n
