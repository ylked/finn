"""Spending insights: where the money goes, by category, merchant, month and weekday.

Spending = lines on expense accounts (net of refunds). Money still waiting on the suspense
account is reported separately (``unclassified``), and counts for merchants (a Migros purchase is
a Migros purchase even before it is categorised). Amounts paid for other people (receivables) are
not spending: only the user's own share is.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Ctx, ctx, writer
from ..db import get_db
from ..merchants import brief
from ..models import Account, JournalLine, Merchant, Transaction

router = APIRouter(prefix="/api/insights", tags=["insights"])
ZERO = Decimal(0)


def _months(start: dt.date, end: dt.date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _period(db: Session, c: Ctx, start: dt.date | None, end: dt.date | None) -> tuple[dt.date, dt.date]:
    end = end or dt.date.today()
    if start is None:
        start = db.scalar(select(func.min(Transaction.date)).where(
            Transaction.ledger_id == c.ledger.id, Transaction.source != "opening")) or end.replace(day=1)
    if start > end:
        raise HTTPException(422, "start after end")
    if (end - start).days > 20 * 366:
        raise HTTPException(422, "Period too long")
    return start, end


def _rows(db: Session, ledger_id: int, start: dt.date, end: dt.date, merchant_id: int | None = None):
    """(tx_id, date, account_id, amount, type, kind, merchant_id, description, counterparty, stats_hidden) of
    P&L / suspense lines."""
    q = (
        select(Transaction.id, Transaction.date, JournalLine.account_id, JournalLine.amount, Account.type, Account.kind,
               Transaction.merchant_id, Transaction.description, Transaction.counterparty, Transaction.stats_hidden)
        .join(JournalLine, JournalLine.transaction_id == Transaction.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Transaction.ledger_id == ledger_id, Transaction.date >= start, Transaction.date <= end,
               Transaction.source != "opening")
        .where((Account.type.in_(["expense", "income"])) | (Account.kind == "suspense"))
    )
    if merchant_id is not None:
        q = q.where(Transaction.merchant_id == merchant_id)
    return db.execute(q).all()


def _groups(db: Session, ledger_id: int) -> tuple[dict[int, Account], dict[int, int]]:
    """Accounts by id, and leaf -> its top-level category (the child of the root 'Charges' group)."""
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.ledger_id == ledger_id))}
    group_of: dict[int, int] = {}
    for a in accounts.values():
        node = a
        while node.parent_id and accounts.get(node.parent_id) and accounts[node.parent_id].parent_id:
            node = accounts[node.parent_id]
        group_of[a.id] = node.id
    return accounts, group_of


def _acc_brief(a: Account) -> dict:
    return {"id": a.id, "code": a.code, "name": a.name}


def _hidden_accounts(ledger) -> list[int]:
    return [int(x) for x in (ledger.settings or {}).get("stats_hidden_accounts", []) if str(x).isdigit()]


def _spend(amount: Decimal, type_: str, kind: str) -> Decimal:
    if type_ == "expense":
        return amount
    if kind == "suspense" and amount > 0:
        return amount
    return ZERO


@router.get("")
def overview(start: dt.date | None = None, end: dt.date | None = None, c: Ctx = Depends(ctx),
             db: Session = Depends(get_db)):
    start, end = _period(db, c, start, end)
    months = _months(start, end)
    days = (end - start).days + 1
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=days - 1)
    accounts, group_of = _groups(db, c.ledger.id)

    exp_by_acc: dict[int, Decimal] = defaultdict(lambda: ZERO)
    acc_months: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    acc_tx: dict[int, set[int]] = defaultdict(set)
    month_exp: dict[str, Decimal] = defaultdict(lambda: ZERO)
    month_inc: dict[str, Decimal] = defaultdict(lambda: ZERO)
    weekday = [ZERO] * 7
    weekday_n = [set() for _ in range(7)]
    tx_spend: dict[int, Decimal] = defaultdict(lambda: ZERO)
    tx_info: dict[int, tuple] = {}
    merch: dict[int, Decimal] = defaultdict(lambda: ZERO)
    merch_tx: dict[int, set[int]] = defaultdict(set)
    hidden_spend: dict[int, Decimal] = defaultdict(lambda: ZERO)
    tx_acc: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    income = unclassified = hidden_with_merchant = ZERO
    # entries the user hid from the lists (rent...): totals count them, the largest / merchant lists do not
    hidden_accounts = set(_hidden_accounts(c.ledger))
    rows = _rows(db, c.ledger.id, start, end)
    hidden_tx = {r[0] for r in rows if r[9] or (r[4] == "expense" and r[2] in hidden_accounts)}
    for tx_id, date, acc_id, amount, type_, kind, mid, desc, cp, _ in rows:
        mk = date.strftime("%Y-%m")
        if tx_id in hidden_tx:
            if mid and type_ == "expense":
                hidden_with_merchant += amount
            mid = None
        if type_ == "income":
            income -= amount
            month_inc[mk] -= amount
            continue
        if kind == "suspense":
            if amount > 0:
                unclassified += amount
            if mid and amount > 0:
                merch[mid] += amount
                merch_tx[mid].add(tx_id)
            continue
        # expense
        exp_by_acc[acc_id] += amount
        acc_months[acc_id][mk] += amount
        acc_tx[acc_id].add(tx_id)
        month_exp[mk] += amount
        weekday[date.weekday()] += amount
        weekday_n[date.weekday()].add(tx_id)
        if tx_id in hidden_tx:
            hidden_spend[tx_id] += amount
        else:
            tx_spend[tx_id] += amount
            tx_info[tx_id] = (date, desc, cp, mid)
            tx_acc[tx_id][acc_id] += amount
        if mid:
            merch[mid] += amount
            merch_tx[mid].add(tx_id)

    prev_exp = ZERO
    prev_by_acc: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for _, _, acc_id, amount, type_, _, _, _, _, _ in _rows(db, c.ledger.id, prev_start, prev_end):
        if type_ == "expense":
            prev_exp += amount
            prev_by_acc[acc_id] += amount

    def item(a: Account, amount: Decimal, prev: Decimal, mon: dict[str, Decimal], n: int, **extra) -> dict:
        return {"id": a.id, "code": a.code, "name": a.name, "amount": amount, "prev": prev, "count": n,
                "months": {m: mon.get(m, ZERO) for m in months}, **extra}

    categories = []
    groups: dict[int, dict] = {}
    for acc_id in set(exp_by_acc) | set(prev_by_acc):
        a = accounts[acc_id]
        g = accounts[group_of[acc_id]]
        if exp_by_acc[acc_id] or acc_tx[acc_id]:
            categories.append(item(a, exp_by_acc[acc_id], prev_by_acc[acc_id], acc_months[acc_id], len(acc_tx[acc_id]),
                                   group_id=g.id))
        gi = groups.setdefault(g.id, {"a": g, "amount": ZERO, "prev": ZERO, "months": defaultdict(lambda: ZERO),
                                      "tx": set()})
        gi["amount"] += exp_by_acc[acc_id]
        gi["prev"] += prev_by_acc[acc_id]
        gi["tx"] |= acc_tx[acc_id]
        for m, v in acc_months[acc_id].items():
            gi["months"][m] += v
    group_list = [item(g["a"], g["amount"], g["prev"], g["months"], len(g["tx"])) for g in groups.values()
                  if g["amount"] or g["tx"]]
    categories.sort(key=lambda x: -x["amount"])
    group_list.sort(key=lambda x: -x["amount"])

    merchants = {m.id: m for m in db.scalars(select(Merchant).where(Merchant.id.in_(list(merch) or [0])))}
    top_merchants = sorted(
        ({**brief(merchants[mid]), "amount": v, "count": len(merch_tx[mid])} for mid, v in merch.items()
         if mid in merchants and v > 0),
        key=lambda x: -x["amount"],
    )[:25]
    with_merchant = sum((v for t, v in tx_spend.items() if tx_info[t][3]), ZERO) + hidden_with_merchant

    largest = sorted(((v, t) for t, v in tx_spend.items() if v > 0), key=lambda x: -x[0])[:8]
    expenses = sum(exp_by_acc.values(), ZERO)
    n_months = max(1.0, days / 30.4375)
    return {
        "period": {"start": start, "end": end, "days": days, "months": months, "prev_start": prev_start,
                   "prev_end": prev_end},
        "totals": {
            "expenses": expenses,
            "income": income,
            "net": income - expenses,
            "savings_rate": float((income - expenses) / income) if income > 0 else None,
            "prev_expenses": prev_exp,
            "per_month": (expenses / Decimal(str(n_months))).quantize(Decimal("0.01")),
            "per_day": (expenses / days).quantize(Decimal("0.01")),
            "count": sum(1 for v in tx_spend.values() if v > 0) + sum(1 for v in hidden_spend.values() if v > 0),
            "unclassified": unclassified,
            "with_merchant": with_merchant,
        },
        "monthly": [{"month": m, "expenses": month_exp.get(m, ZERO), "income": month_inc.get(m, ZERO)} for m in months],
        "groups": group_list,
        "categories": categories,
        "merchants": top_merchants,
        "weekday": [{"day": i, "amount": weekday[i], "count": len(weekday_n[i])} for i in range(7)],
        "hidden": {
            "count": sum(1 for v in hidden_spend.values() if v > 0),
            "amount": sum((v for v in hidden_spend.values() if v > 0), ZERO),
            "accounts": [{"id": a.id, "code": a.code, "name": a.name} for a in
                         sorted((accounts[i] for i in hidden_accounts if i in accounts), key=lambda a: a.code)],
        },
        "largest": [
            {"id": t, "date": tx_info[t][0], "label": tx_info[t][2] or tx_info[t][1], "amount": v,
             "merchant": brief(merchants.get(tx_info[t][3])) if tx_info[t][3] in merchants else None,
             "account": _acc_brief(accounts[max(tx_acc[t].items(), key=lambda x: x[1])[0]])}
            for v, t in largest
        ],
    }


@router.get("/merchants/{mid}")
def merchant(mid: int, start: dt.date | None = None, end: dt.date | None = None, c: Ctx = Depends(ctx),
             db: Session = Depends(get_db)):
    m = db.get(Merchant, mid)
    if m is None or m.ledger_id != c.ledger.id:
        raise HTTPException(404, "Merchant not found")
    first, last = db.execute(select(func.min(Transaction.date), func.max(Transaction.date))
                             .where(Transaction.merchant_id == mid)).one()
    start, end = _period(db, c, start or first, end)
    months = _months(start, end)
    accounts, _ = _groups(db, c.ledger.id)

    def summarise(rows):
        per_tx: dict[int, Decimal] = defaultdict(lambda: ZERO)
        mon: dict[str, Decimal] = defaultdict(lambda: ZERO)
        cats: dict[int, Decimal] = defaultdict(lambda: ZERO)
        wd = [ZERO] * 7
        for tx_id, date, acc_id, amount, type_, kind, _, _, _, _ in rows:
            v = _spend(amount, type_, kind)
            if not v:
                continue
            per_tx[tx_id] += v
            mon[date.strftime("%Y-%m")] += v
            cats[acc_id] += v
            wd[date.weekday()] += v
        return per_tx, mon, cats, wd

    per_tx, mon, cats, wd = summarise(_rows(db, c.ledger.id, start, end, mid))
    total = sum(per_tx.values(), ZERO)
    count = sum(1 for v in per_tx.values() if v > 0)
    # all time, for "how much do I spend there generally"
    all_tx, all_mon, _, _ = summarise(_rows(db, c.ledger.id, first or start, dt.date.today(), mid)) if first else ({}, {}, None, None)
    all_total = sum(all_tx.values(), ZERO)
    all_months = len(_months(first, last)) if first and last else 0
    days = (end - start).days + 1
    n_months = max(1.0, days / 30.4375)
    tickets = sorted(v for v in per_tx.values() if v > 0)
    return {
        "merchant": {**brief(m), "domain": m.domain},
        "period": {"start": start, "end": end, "months": months},
        "totals": {
            "amount": total,
            "count": count,
            "avg_ticket": (total / count).quantize(Decimal("0.01")) if count else None,
            "median_ticket": tickets[len(tickets) // 2] if tickets else None,
            "max_ticket": tickets[-1] if tickets else None,
            "per_month": (total / Decimal(str(n_months))).quantize(Decimal("0.01")),
            "first_date": first,
            "last_date": last,
            "all_amount": all_total,
            "all_count": sum(1 for v in all_tx.values() if v > 0),
            "all_per_month": (all_total / all_months).quantize(Decimal("0.01")) if all_months else None,
        },
        "monthly": [{"month": k, "amount": mon.get(k, ZERO)} for k in months],
        "categories": sorted(
            ({"id": a, "code": accounts[a].code, "name": accounts[a].name, "amount": v} for a, v in cats.items() if v),
            key=lambda x: -x["amount"],
        ),
        "weekday": [{"day": i, "amount": wd[i]} for i in range(7)],
    }


class HideAccountIn(BaseModel):
    account_id: int
    hidden: bool = True


@router.post("/hidden-accounts")
def hide_account(body: HideAccountIn, c: Ctx = Depends(writer), db: Session = Depends(get_db)):
    """Hide a whole category (e.g. rent) from the insights lists, now and for future entries."""
    a = db.get(Account, body.account_id)
    if a is None or a.ledger_id != c.ledger.id:
        raise HTTPException(404, "Account not found")
    ids = set(_hidden_accounts(c.ledger))
    ids = ids | {a.id} if body.hidden else ids - {a.id}
    c.ledger.settings = {**(c.ledger.settings or {}), "stats_hidden_accounts": sorted(ids)}
    db.commit()
    return {"accounts": sorted(ids)}


# ------------------------------------------------------------------ balance over time

FORECAST_MONTHS = 6


def _scope_accounts(db: Session, ledger_id: int, scope: str) -> list[int]:
    stmt = select(Account.id).where(Account.ledger_id == ledger_id, Account.is_group.is_(False))
    if scope == "liquid":
        stmt = stmt.where(Account.kind.in_(["bank", "cash", "credit_card"]))
    elif scope == "net_worth":
        stmt = stmt.where(Account.type.in_(["asset", "liability"]), Account.kind != "equity")
    else:
        try:
            aid = int(scope)
        except ValueError:
            raise HTTPException(422, "scope: liquid, net_worth or an account id") from None
        stmt = stmt.where(Account.id == aid, Account.type.in_(["asset", "liability"]))
    ids = list(db.scalars(stmt))
    if not ids:
        raise HTTPException(404, "No account")
    return ids


def _month_end(d: dt.date) -> dt.date:
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


@router.get("/balance")
def balance(start: dt.date | None = None, end: dt.date | None = None, scope: str = "liquid",
            c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Balance of a set of accounts over time, and a projection for the next months.

    Balances use the asset sign (money you have is positive; a credit card balance is negative).
    The projection continues the median monthly change of the last 6 complete months - leaving out
    invoice payments - and subtracts the open invoices on their due dates."""
    from ..invoices import remaining_many
    from ..models import Invoice

    today = dt.date.today()
    ids = _scope_accounts(db, c.ledger.id, scope)
    first = db.scalar(select(func.min(Transaction.date)).join(JournalLine)
                      .where(JournalLine.account_id.in_(ids))) or today
    end = min(end or today, today)
    start = max(start or first, first)
    if start > end:
        start = end
    opening = db.scalar(select(func.coalesce(func.sum(JournalLine.amount), 0)).join(Transaction)
                        .where(JournalLine.account_id.in_(ids), Transaction.date < start)) or ZERO
    daily = dict(db.execute(
        select(Transaction.date, func.sum(JournalLine.amount)).join(Transaction)
        .where(JournalLine.account_id.in_(ids), Transaction.date.between(start, end))
        .group_by(Transaction.date)
    ).all())
    span = (end - start).days
    step = 1 if span <= 120 else 7 if span <= 3 * 366 else 30
    points, bal, lo, hi = [], Decimal(opening), None, None
    d = start
    while d <= end:
        bal += daily.get(d, ZERO)
        lo = bal if lo is None or bal < lo else lo
        hi = bal if hi is None or bal > hi else hi
        if (d - start).days % step == 0 or d == end:
            points.append({"date": d, "balance": bal})
        d += dt.timedelta(days=1)

    # ---- projection (from today's balance)
    forecast = None
    if end >= today - dt.timedelta(days=1):
        now_bal = db.scalar(select(func.coalesce(func.sum(JournalLine.amount), 0)).join(Transaction)
                            .where(JournalLine.account_id.in_(ids), Transaction.date <= today)) or ZERO
        last_full = today.replace(day=1) - dt.timedelta(days=1)
        months = []
        m_end = last_full
        for _ in range(6):
            m_start = m_end.replace(day=1)
            if m_end < first:
                break
            settles = select(JournalLine.transaction_id).where(JournalLine.invoice_id.is_not(None))
            flow = db.scalar(select(func.coalesce(func.sum(JournalLine.amount), 0)).join(Transaction)
                             .where(JournalLine.account_id.in_(ids), Transaction.date.between(m_start, m_end),
                                    Transaction.source != "opening", Transaction.id.not_in(settles))) or ZERO
            months.append(Decimal(flow))
            m_end = m_start - dt.timedelta(days=1)
        if months:
            srt = sorted(months)
            mid = len(srt) // 2
            trend = srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2
            trend = trend.quantize(Decimal("0.01"))
            invs = list(db.scalars(select(Invoice).where(Invoice.ledger_id == c.ledger.id, Invoice.status == "open")))
            rems = remaining_many(db, [i.id for i in invs])
            # invoices settle on liquid accounts; for net worth they are already counted (booked as debt)
            dues = [(max(i.due_date or today, today), rems.get(i.id, ZERO)) for i in invs] if scope == "liquid" else []
            fpoints = [{"date": today, "balance": Decimal(now_bal)}]
            m = today
            for _ in range(FORECAST_MONTHS):
                m = _month_end(m + dt.timedelta(days=1))
                days = (m - today).days
                due = sum((v for d_, v in dues if d_ <= m), ZERO)
                fpoints.append({"date": m, "balance": (Decimal(now_bal) + trend * Decimal(days) / Decimal("30.4375")
                                                       - due).quantize(Decimal("0.01"))})
            forecast = {
                "monthly_trend": trend,
                "months_used": len(months),
                "open_invoices": sum((v for _, v in dues), ZERO),
                "points": fpoints,
            }
    return {
        "scope": scope,
        "start": start,
        "end": end,
        "opening": Decimal(opening),
        "closing": bal,
        "change": bal - Decimal(opening),
        "min": lo,
        "max": hi,
        "points": points,
        "forecast": forecast,
    }
