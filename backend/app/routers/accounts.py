import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..accounting import AccountingError, balances, create_person_account, get_setting, rollup, set_opening_balance
from ..ai.classify import apply_rules_to_unclassified
from ..auth import Ctx, ctx, writer
from ..config import get_settings
from ..db import get_db
from ..importers.service import redate_bank_transactions
from ..models import ACCOUNT_KINDS, ACCOUNT_TYPES, Account, ClassificationRule, JournalLine
from ..schemas import AccountIn, AccountOut, AccountPatch, LedgerSettingsIn, OpeningIn, PersonIn

router = APIRouter(prefix="/api", tags=["accounts"])


def _get(db: Session, c: Ctx, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if acc is None or acc.ledger_id != c.ledger.id:
        raise HTTPException(404, "Account not found")
    return acc


def _out(acc: Account, bal) -> AccountOut:
    o = AccountOut.model_validate(acc)
    o.balance = bal.get(acc.id, Decimal(0))
    return o


def _with_balance(db: Session, c: Ctx, acc: Account) -> AccountOut:
    accounts = db.scalars(select(Account).where(Account.ledger_id == c.ledger.id)).all()
    return _out(acc, rollup(list(accounts), balances(db, c.ledger.id)))


@router.get("/me")
def me(c: Ctx = Depends(ctx)):
    s = get_settings()
    return {
        "user": {"id": c.user.id, "name": c.user.name, "email": c.user.email, "locale": c.user.locale},
        "ledger": {
            "id": c.ledger.id,
            "name": c.ledger.name,
            "base_currency": c.ledger.base_currency,
            "locale": c.ledger.locale,
            "settings": c.ledger.settings,
        },
        "role": c.role,
        "ledgers": [{"id": m.ledger_id, "name": m.ledger.name, "role": m.role} for m in c.user.memberships],
        "features": {"ai": s.ai_enabled, "vision": s.vision_enabled, "auth": s.auth_mode},
    }


@router.patch("/me")
def patch_me(body: dict, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    if body.get("locale") in ("fr", "en"):
        c.user.locale = body["locale"]
    db.commit()
    return {"ok": True}


@router.patch("/ledger")
def patch_ledger(body: LedgerSettingsIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    if c.role != "owner":
        raise HTTPException(403, "Only the owner can change ledger settings")
    if body.name:
        c.ledger.name = body.name
    if body.locale in ("fr", "en"):
        c.ledger.locale = body.locale
    redated = None
    if body.settings is not None:
        if body.settings.get("bank_date", "value") not in ("value", "booking"):
            raise HTTPException(422, "bank_date must be 'value' or 'booking'")
        before = get_setting(c.ledger, "bank_date")
        c.ledger.settings = {**(c.ledger.settings or {}), **body.settings}
        if get_setting(c.ledger, "bank_date") != before:
            redated = redate_bank_transactions(db, c.ledger)
    db.commit()
    return {"ok": True, "settings": c.ledger.settings, "redated": redated}


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(at: dt.date | None = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).where(Account.ledger_id == c.ledger.id).order_by(Account.code)).all()
    bal = rollup(list(accounts), balances(db, c.ledger.id, end=at))
    return [_out(a, bal) for a in accounts]


def _check(body, db: Session, c: Ctx, acc: Account | None = None):
    typ = getattr(body, "type", None) or (acc.type if acc else None)
    if typ not in ACCOUNT_TYPES:
        raise HTTPException(422, "Invalid account type")
    if body.kind is not None and body.kind not in ACCOUNT_KINDS:
        raise HTTPException(422, "Invalid account kind")
    if body.parent_id is not None:
        parent = _get(db, c, body.parent_id)
        if not parent.is_group:
            raise HTTPException(422, "Parent must be a group account")
        if parent.type != typ:
            raise HTTPException(422, "Parent account must have the same type")
        p = parent
        while p is not None:  # no cycles
            if acc is not None and p.id == acc.id:
                raise HTTPException(422, "Cycle in account hierarchy")
            p = p.parent
    if body.code is not None:
        dup = db.scalar(select(Account.id).where(Account.ledger_id == c.ledger.id, Account.code == body.code))
        if dup and (acc is None or dup != acc.id):
            raise HTTPException(409, f"Code {body.code} already exists")


@router.post("/accounts", response_model=AccountOut)
def create_account(body: AccountIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    _check(body, db, c)
    acc = Account(ledger_id=c.ledger.id, **body.model_dump())
    if acc.iban:
        acc.iban = acc.iban.replace(" ", "").upper()
    db.add(acc)
    db.commit()
    return _out(acc, {})


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, body: AccountPatch, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    acc = _get(db, c, account_id)
    _check(body, db, c, acc)
    data = body.model_dump(exclude_unset=True)
    if "iban" in data and data["iban"]:
        data["iban"] = data["iban"].replace(" ", "").upper()
    for k, v in data.items():
        setattr(acc, k, v)
    db.commit()
    return _with_balance(db, c, acc)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    acc = _get(db, c, account_id)
    used = db.scalar(select(func.count(JournalLine.id)).where(JournalLine.account_id == acc.id))
    children = db.scalar(select(func.count(Account.id)).where(Account.parent_id == acc.id))
    if used or children:
        raise HTTPException(409, "Account has postings or sub-accounts; deactivate it instead")
    db.execute(ClassificationRule.__table__.delete().where(ClassificationRule.target_account_id == acc.id))
    db.delete(acc)
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/opening", response_model=AccountOut)
def opening(account_id: int, body: OpeningIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    acc = _get(db, c, account_id)
    if acc.type not in ("asset", "liability") or acc.is_group:
        raise HTTPException(422, "Opening balances apply to asset / liability accounts")
    try:
        set_opening_balance(db, acc, body.amount, body.date, c.user.id)
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return _with_balance(db, c, acc)


@router.post("/accounts/persons", response_model=AccountOut)
def create_person(body: PersonIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    try:
        acc = create_person_account(db, c.ledger.id, body.name.strip(), body.owed_to_me)
    except AccountingError as e:
        raise HTTPException(422, str(e)) from e
    apply_rules_to_unclassified(db, c.ledger.id)
    db.commit()
    return _out(acc, {})
