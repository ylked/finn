import io
import json
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import next_sibling_code
from ..auth import Ctx, ctx, writer
from ..db import get_db
from ..importers import camt053, csv_import
from ..importers.service import DuplicateFile, import_entries, reconciliation, sha256
from ..models import Account, BankEntry, CsvProfile, ImportBatch, JournalLine, Transaction
from ..schemas import BatchOut, CsvProfileIn, CsvProfileOut
from ..worker import worker

router = APIRouter(prefix="/api", tags=["imports"])

MAX_FILE = 20 * 1024 * 1024
MAX_ZIP_MEMBERS = 500


def _is_xml(content: bytes) -> bool:
    head = content[:2000].lstrip(b"\xef\xbb\xbf \r\n\t")
    return head.startswith(b"<?xml") or head.startswith(b"<Document") or b"BkToCstmrStmt" in content[:5000]


async def _expand(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    out = []
    for f in files:
        content = await f.read(MAX_FILE + 1)
        if len(content) > MAX_FILE:
            raise HTTPException(413, f"{f.filename}: file too large")
        name = f.filename or "upload"
        if name.lower().endswith(".zip") or content[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                members = [m for m in z.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX")]
                if len(members) > MAX_ZIP_MEMBERS:
                    raise HTTPException(413, "Too many files in archive")
                for m in members:
                    if m.file_size > MAX_FILE:
                        raise HTTPException(413, f"{m.filename}: file too large")
                    if m.filename.lower().endswith((".xml", ".csv", ".txt")):
                        out.append((m.filename.rsplit("/", 1)[-1], z.read(m)))
        else:
            out.append((name, content))
    return out


def _account(db: Session, c: Ctx, account_id: int | None) -> Account | None:
    if account_id is None:
        return None
    acc = db.get(Account, account_id)
    if acc is None or acc.ledger_id != c.ledger.id or acc.is_group:
        raise HTTPException(422, "Invalid target account")
    return acc


def _new_bank_account(db: Session, c: Ctx, stmt: camt053.ParsedStatement) -> Account:
    banks = db.scalars(
        select(Account)
        .where(Account.ledger_id == c.ledger.id, Account.kind == "bank", Account.is_group.is_(False))
        .order_by(Account.code)
    ).all()
    # reuse the seeded, never-used "current account" if it has no IBAN yet
    for b in banks:
        if not b.iban and b.code == "1020" and not db.scalar(select(JournalLine.id).where(JournalLine.account_id == b.id).limit(1)):
            b.iban = stmt.iban
            b.name = f"{stmt.servicer or b.name} {stmt.iban[-4:]}" if stmt.iban else b.name
            return b
    base = banks[-1] if banks else None
    parent_id = base.parent_id if base else None
    code = next_sibling_code(db, c.ledger.id, base) if base else "1020"
    acc = Account(
        ledger_id=c.ledger.id,
        parent_id=parent_id,
        code=code,
        name=f"{stmt.servicer or 'Banque'} {(stmt.iban or '')[-4:]}".strip(),
        type="asset",
        kind="bank",
        currency=stmt.currency or c.ledger.base_currency,
        iban=stmt.iban,
    )
    db.add(acc)
    db.flush()
    return acc


@router.post("/imports")
async def upload(
    files: list[UploadFile] = File(...),
    account_id: int | None = Form(None),
    profile_id: int | None = Form(None),
    mapping: str | None = Form(None),
    iban_map: str | None = Form(None),
    c: Ctx = Depends(writer),
    db: Session = Depends(get_db),
):
    target = _account(db, c, account_id)
    imap: dict = json.loads(iban_map) if iban_map else {}
    items = await _expand(files)

    # ---- parse everything first
    camt_items: list[tuple[str, bytes, int, camt053.ParsedStatement]] = []
    csv_items: list[tuple[str, bytes]] = []
    results: list[dict] = []
    for name, content in items:
        if _is_xml(content):
            try:
                for i, st in enumerate(camt053.parse(content)):
                    camt_items.append((name, content, i, st))
            except camt053.CamtError as e:
                results.append({"filename": name, "status": "error", "error": str(e)})
        else:
            csv_items.append((name, content))

    # ---- resolve accounts for camt statements
    unknown: dict[str, dict] = {}
    resolved: list[tuple[str, bytes, int, camt053.ParsedStatement, Account]] = []
    for name, content, i, st in camt_items:
        iban = (st.iban or st.account_other_id or "").replace(" ", "").upper()
        acc = target
        if acc is None and iban:
            acc = db.scalar(select(Account).where(Account.ledger_id == c.ledger.id, Account.iban == iban))
        if acc is None and iban in imap:
            choice = imap[iban]
            if choice == "new":
                acc = _new_bank_account(db, c, st)
                imap[iban] = acc.id
            else:
                acc = _account(db, c, int(choice))
                if not acc.iban:
                    acc.iban = iban
        if acc is None:
            u = unknown.setdefault(iban, {"iban": iban, "servicer": st.servicer, "owner": st.owner,
                                          "currency": st.currency, "files": []})
            u["files"].append(name)
            continue
        resolved.append((name, content, i, st, acc))

    db.commit()  # persist accounts created / IBANs assigned above, independently of file outcomes

    # oldest statements first so opening balances and matching are consistent
    resolved.sort(key=lambda x: (x[4].id, x[3].from_date or x[3].to_date))
    for name, content, i, st, acc in resolved:
        h = sha256(content) + (f":{i}" if i else "")
        try:
            with db.begin_nested():
                if st.currency and acc.currency and st.currency != acc.currency:
                    raise ValueError(f"Currency mismatch: statement {st.currency}, account {acc.currency}")
                r = import_entries(db, c.ledger, acc, kind="camt053", filename=name, file_hash=h,
                                   entries=st.entries, statement=st, user_id=c.user.id)
            db.commit()
            results.append({"filename": name, "status": "ok", "account_id": acc.id, **r.__dict__})
        except DuplicateFile:
            results.append({"filename": name, "status": "duplicate", "account_id": acc.id})
        except Exception as e:  # savepoint rolled back; keep importing other files
            results.append({"filename": name, "status": "error", "error": str(e)})

    # ---- csv
    if csv_items:
        if target is None:
            for name, _ in csv_items:
                results.append({"filename": name, "status": "error", "error": "Choose the target account for CSV files"})
        else:
            m = _mapping(db, c, profile_id, mapping)
            for name, content in csv_items:
                try:
                    entries, errors = csv_import.parse(content, m, target.currency)
                    with db.begin_nested():
                        r = import_entries(db, c.ledger, target, kind="csv", filename=name,
                                           file_hash=sha256(content), entries=entries, user_id=c.user.id)
                    db.commit()
                    r.errors = errors
                    results.append({"filename": name, "status": "ok", "account_id": target.id, **r.__dict__})
                except DuplicateFile:
                    results.append({"filename": name, "status": "duplicate", "account_id": target.id})
                except csv_import.CsvError as e:
                    results.append({"filename": name, "status": "error", "error": str(e)})
    db.commit()
    worker.wake()
    return {"results": results, "unknown_ibans": list(unknown.values())}


def _mapping(db: Session, c: Ctx, profile_id: int | None, mapping: str | None) -> csv_import.CsvMapping:
    try:
        if mapping:
            return csv_import.CsvMapping.model_validate_json(mapping)
        if profile_id:
            p = db.get(CsvProfile, profile_id)
            if p is None or p.ledger_id != c.ledger.id:
                raise HTTPException(404, "CSV profile not found")
            return csv_import.CsvMapping.model_validate(p.config)
    except ValidationError as e:
        raise HTTPException(422, f"Invalid CSV mapping: {e}") from e
    raise HTTPException(422, "A CSV mapping or profile is required")


@router.post("/imports/csv/preview")
async def csv_preview(
    file: UploadFile = File(...),
    mapping: str | None = Form(None),
    c: Ctx = Depends(ctx),
):
    content = await file.read(MAX_FILE + 1)
    m = csv_import.CsvMapping.model_validate_json(mapping) if mapping else csv_import.CsvMapping()
    try:
        prev = csv_import.preview(content, m)
    except csv_import.CsvError as e:
        raise HTTPException(422, str(e)) from e
    parsed, errors = [], []
    if m.date_column and (m.amount_column or (m.debit_column or m.credit_column)):
        try:
            entries, errors = csv_import.parse(content, m, c.ledger.base_currency)
            parsed = [
                {"date": e.booking_date, "amount": e.amount, "description": e.description,
                 "counterparty": e.counterparty, "orig": f"{e.orig_amount} {e.orig_currency}" if e.orig_amount else None}
                for e in entries[:15]
            ]
        except csv_import.CsvError as e:
            errors = [str(e)]
    return {**prev, "parsed": parsed, "errors": errors[:20]}


@router.get("/imports", response_model=list[BatchOut])
def list_batches(account_id: int | None = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    q = select(ImportBatch).where(ImportBatch.ledger_id == c.ledger.id)
    if account_id:
        q = q.where(ImportBatch.account_id == account_id)
    batches = db.scalars(q.order_by(ImportBatch.to_date.desc().nulls_last(), ImportBatch.id.desc()).limit(500)).all()
    out = []
    for b in batches:
        o = BatchOut.model_validate(b)
        if b.closing_balance is not None and b.to_date:
            rec = reconciliation(db, b.account, b.to_date)
            o.computed_balance = rec["statement_balance"]
            o.ledger_balance = rec["ledger_balance"]
            o.balance_ok = rec["statement_balance"] == b.closing_balance
        out.append(o)
    return out


@router.get("/accounts/{account_id}/reconciliation")
def account_reconciliation(account_id: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    acc = _account(db, c, account_id)
    last = db.scalar(
        select(ImportBatch)
        .where(ImportBatch.account_id == acc.id, ImportBatch.closing_balance.is_not(None))
        .order_by(ImportBatch.to_date.desc())
        .limit(1)
    )
    if last is None:
        return {"has_statement": False}
    rec = reconciliation(db, acc, last.to_date)
    # entries on this account that no statement line backs: the usual cause of a difference
    # (a transfer whose other half was booked elsewhere, an entry typed by hand or from a receipt)
    q = (
        select(JournalLine, Transaction)
        .join(Transaction)
        .where(JournalLine.account_id == acc.id, JournalLine.bank_entry_id.is_(None),
               Transaction.source != "opening", Transaction.date <= last.to_date)
    )
    if acc.opening_date:
        q = q.where(Transaction.date >= acc.opening_date)
    sign = 1 if acc.type in ("asset", "expense") else -1
    unbacked = [
        {"transaction_id": tx.id, "date": tx.date, "description": tx.description,
         "amount": sign * ln.amount, "state": tx.state}
        for ln, tx in db.execute(q.order_by(Transaction.date.desc()).limit(50))
    ]
    return {
        "has_statement": True,
        "statement_date": last.to_date,
        "closing_balance": last.closing_balance,
        **rec,
        "difference": rec["ledger_balance"] - last.closing_balance,
        "unbacked": unbacked,
    }


@router.delete("/imports/{batch_id}")
def delete_batch(batch_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Remove an import: transactions created from it are deleted, matched manual ones are unlinked."""
    b = db.get(ImportBatch, batch_id)
    if b is None or b.ledger_id != c.ledger.id:
        raise HTTPException(404, "Import not found")
    entry_ids = set(db.scalars(select(BankEntry.id).where(BankEntry.batch_id == b.id)))
    lines = db.scalars(select(JournalLine).where(JournalLine.bank_entry_id.in_(entry_ids or {0}))).all()
    deleted = unlinked = 0
    for ln in lines:
        tx = ln.transaction
        others = [x for x in tx.lines if x.bank_entry_id and x.bank_entry_id not in entry_ids]
        if tx.source in ("bank", "csv") and not others:
            for r in tx.receipts:
                r.transaction_id = None
                r.status = "unmatched" if r.extracted else "manual"
            db.delete(tx)
            deleted += 1
        else:
            ln.bank_entry_id = None
            unlinked += 1
    db.flush()
    db.delete(b)
    db.flush()
    # an opening balance from a now-missing statement stays; the user can edit it on the account
    db.commit()
    return {"deleted_transactions": deleted, "unlinked": unlinked}


# ---------------------------------------------------------------- csv profiles
@router.get("/csv-profiles", response_model=list[CsvProfileOut])
def profiles(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return db.scalars(select(CsvProfile).where(CsvProfile.ledger_id == c.ledger.id).order_by(CsvProfile.name)).all()


@router.post("/csv-profiles", response_model=CsvProfileOut)
def create_profile(body: CsvProfileIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    cfg = csv_import.CsvMapping.model_validate(body.config).model_dump()
    p = CsvProfile(ledger_id=c.ledger.id, name=body.name, config=cfg)
    db.add(p)
    db.commit()
    return p


@router.put("/csv-profiles/{pid}", response_model=CsvProfileOut)
def update_profile(pid: int, body: CsvProfileIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    p = db.get(CsvProfile, pid)
    if p is None or p.ledger_id != c.ledger.id:
        raise HTTPException(404, "Profile not found")
    p.name = body.name
    p.config = csv_import.CsvMapping.model_validate(body.config).model_dump()
    db.commit()
    return p


@router.delete("/csv-profiles/{pid}")
def delete_profile(pid: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    p = db.get(CsvProfile, pid)
    if p is None or p.ledger_id != c.ledger.id:
        raise HTTPException(404, "Profile not found")
    db.delete(p)
    db.commit()
    return {"ok": True}

