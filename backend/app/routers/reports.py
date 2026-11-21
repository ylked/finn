import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..accounting import balances, natural
from ..auth import Ctx, ctx
from ..db import get_db
from ..models import Account, Transaction
from ..reports import service
from ..reports.render import LABELS, csv_bytes, fmt_date, pdf

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _lang(c: Ctx, lang: str | None) -> str:
    return lang if lang in ("fr", "en") else (c.user.locale or c.ledger.locale or "fr")


def _file(content: bytes, name: str, mime: str) -> Response:
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _period(lang, start, end):
    t = LABELS[lang]
    return f"{t['period']} {fmt_date(start, lang)} {t['to']} {fmt_date(end, lang)}"


@router.get("/balance-sheet")
def balance_sheet(at: dt.date | None = None, format: str = "json", lang: str | None = None,
                  c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    at = at or dt.date.today()
    d = service.balance_sheet(db, c.ledger, at)
    lg = _lang(c, lang)
    if format == "pdf":
        sub = f"{LABELS[lg]['at']} {fmt_date(at, lg)} · {c.ledger.base_currency}"
        return _file(pdf("balance_sheet", lang=lg, title_key="balance_sheet", subtitle=sub, data=d,
                         ledger_name=c.ledger.name, ccy=c.ledger.base_currency), f"bilan_{at}.pdf", "application/pdf")
    if format == "csv":
        rows = [["A", r["code"], r["name"], r["amount"]] for r in d["assets"]]
        rows += [["P", r["code"], r["name"], r["amount"]] for r in d["liabilities"]]
        rows.append(["P", "", LABELS[lg]["result"], d["result"]])
        return _file(csv_bytes(["side", "code", "name", "amount"], rows), f"bilan_{at}.csv", "text/csv")
    return d


@router.get("/income-statement")
def income_statement(start: dt.date, end: dt.date, format: str = "json", lang: str | None = None,
                     c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    d = service.income_statement(db, c.ledger, start, end)
    lg = _lang(c, lang)
    if format == "pdf":
        return _file(pdf("income_statement", lang=lg, title_key="income_statement",
                         subtitle=_period(lg, start, end) + f" · {c.ledger.base_currency}", data=d,
                         ledger_name=c.ledger.name, ccy=c.ledger.base_currency),
                     f"resultat_{start}_{end}.pdf", "application/pdf")
    if format == "csv":
        rows = [[r["type"], r["code"], r["name"], r["amount"]] for r in d["income"] + d["expenses"]]
        return _file(csv_bytes(["type", "code", "name", "amount"], rows), f"resultat_{start}_{end}.csv", "text/csv")
    return d


@router.get("/ledger")
def ledger(start: dt.date, end: dt.date, account_id: list[int] = Query(default=[]), format: str = "json",
           lang: str | None = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    if not account_id:
        # every non-group account with movements
        account_id = list(db.scalars(select(Account.id).where(Account.ledger_id == c.ledger.id, Account.is_group.is_(False))))
    d = [a for a in service.general_ledger(db, c.ledger, account_id, start, end) if a["rows"] or a["opening"]]
    lg = _lang(c, lang)
    if format == "pdf":
        return _file(pdf("ledger", lang=lg, title_key="ledger", subtitle=_period(lg, start, end), data=d,
                         ledger_name=c.ledger.name, ccy=c.ledger.base_currency),
                     f"grand-livre_{start}_{end}.pdf", "application/pdf")
    if format == "csv":
        rows = []
        for a in d:
            for r in a["rows"]:
                rows.append([a["account"]["code"], a["account"]["name"], r["date"], r["tx_id"], r["description"],
                             r["counterparty"], r["counter"], r["debit"] or None, r["credit"] or None, r["balance"]])
        return _file(csv_bytes(["account", "name", "date", "tx", "description", "counterparty", "counter",
                                "debit", "credit", "balance"], rows), f"grand-livre_{start}_{end}.csv", "text/csv")
    return d


@router.get("/journal")
def journal(start: dt.date, end: dt.date, format: str = "json", lang: str | None = None,
            c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    d = service.journal(db, c.ledger, start, end)
    lg = _lang(c, lang)
    if format == "pdf":
        return _file(pdf("journal", lang=lg, title_key="journal", subtitle=_period(lg, start, end), data=d,
                         ledger_name=c.ledger.name, ccy=c.ledger.base_currency),
                     f"journal_{start}_{end}.pdf", "application/pdf")
    if format == "csv":
        rows = [[t["date"], t["id"], t["description"], t["counterparty"], ln["code"], ln["name"], ln["memo"],
                 ln["debit"], ln["credit"], t["state"]] for t in d for ln in t["lines"]]
        return _file(csv_bytes(["date", "tx", "description", "counterparty", "account", "name", "memo",
                                "debit", "credit", "state"], rows), f"journal_{start}_{end}.csv", "text/csv")
    return d


@router.get("/monthly")
def monthly(start: dt.date, end: dt.date, format: str = "json", lang: str | None = None,
            c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    if (end - start).days > 3 * 366:
        raise HTTPException(422, "Period too long")
    d = service.monthly(db, c.ledger, start, end)
    lg = _lang(c, lang)
    if format == "pdf":
        return _file(pdf("monthly", lang=lg, title_key="monthly", subtitle=_period(lg, start, end), data=d,
                         ledger_name=c.ledger.name, ccy=c.ledger.base_currency),
                     f"mensuel_{start}_{end}.pdf", "application/pdf")
    if format == "csv":
        rows = [[r["code"], r["name"], r["type"], *[r["months"].get(m) for m in d["months"]], r["total"]]
                for r in d["rows"]]
        return _file(csv_bytes(["code", "name", "type", *d["months"], "total"], rows),
                     f"mensuel_{start}_{end}.csv", "text/csv")
    return d


@router.get("/receivables")
def receivables(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return service.receivables(db, c.ledger)


@router.get("/dashboard")
def dashboard(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    today = dt.date.today()
    month_start = today.replace(day=1)
    accounts = list(db.scalars(select(Account).where(Account.ledger_id == c.ledger.id, Account.is_group.is_(False))))
    raw = balances(db, c.ledger.id)
    zero = Decimal(0)
    money_accounts = [
        {"id": a.id, "code": a.code, "name": a.name, "kind": a.kind, "type": a.type,
         "balance": natural(raw.get(a.id, zero), a.type)}
        for a in accounts
        if a.kind in ("bank", "cash", "credit_card") and a.is_active and (raw.get(a.id) or a.iban)
    ]
    people = [
        {"id": a.id, "name": a.name, "balance": raw.get(a.id, zero)}
        for a in accounts if a.kind == "receivable" and raw.get(a.id, zero) != 0
    ]
    mth = service.income_statement(db, c.ledger, month_start, today)
    y, m = (today.year, today.month - 11) if today.month == 12 else (today.year - 1, today.month + 1)
    yr = service.monthly(db, c.ledger, dt.date(y, max(m, 1), 1), today)  # last 12 months
    top = sorted((r for r in mth["expenses"] if not r["group"] and r["amount"] > 0), key=lambda r: -r["amount"])[:8]
    counts = dict(db.execute(
        select(Transaction.state, func.count()).where(Transaction.ledger_id == c.ledger.id).group_by(Transaction.state)
    ).all())
    bs = service.balance_sheet(db, c.ledger, today)
    series = []
    for m in yr["months"]:
        inc = sum((r["months"].get(m, zero) for r in yr["rows"] if r["type"] == "income"), zero)
        exp = sum((r["months"].get(m, zero) for r in yr["rows"] if r["type"] == "expense"), zero)
        series.append({"month": m, "income": inc, "expenses": exp})
    return {
        "accounts": money_accounts,
        "people": people,
        "month": {"income": mth["total_income"], "expenses": mth["total_expenses"], "result": mth["result"],
                  "top_expenses": top},
        "series": series,
        "to_review": counts.get("auto", 0),
        "unclassified": counts.get("unclassified", 0),
        "net_worth": bs["net_worth"],
    }
