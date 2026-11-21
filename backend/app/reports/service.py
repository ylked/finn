"""Financial reports: balance sheet, income statement, general ledger, journal, monthly expenses."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session, selectinload

from ..accounting import balances, natural, rollup
from ..models import Account, JournalLine, Ledger, Transaction

ZERO = Decimal(0)


def _tree_rows(accounts: list[Account], bal: dict[int, Decimal], types: tuple[str, ...], hide_zero=True) -> list[dict]:
    by_parent: dict[int | None, list[Account]] = {}
    for a in accounts:
        by_parent.setdefault(a.parent_id, []).append(a)
    rows: list[dict] = []

    def walk(a: Account, depth: int):
        v = bal.get(a.id, ZERO)
        if hide_zero and v == 0 and not _has_nonzero_descendant(a):
            return
        rows.append({"id": a.id, "code": a.code, "name": a.name, "depth": depth, "group": a.is_group,
                     "amount": v, "type": a.type})
        for ch in sorted(by_parent.get(a.id, []), key=lambda x: x.code):
            walk(ch, depth + 1)

    def _has_nonzero_descendant(a: Account) -> bool:
        return any(bal.get(ch.id, ZERO) != 0 or _has_nonzero_descendant(ch) for ch in by_parent.get(a.id, []))

    for root in sorted(by_parent.get(None, []), key=lambda x: x.code):
        if root.type in types:
            walk(root, 0)
    return rows


def balance_sheet(db: Session, ledger: Ledger, at: dt.date) -> dict:
    accounts = list(db.scalars(select(Account).where(Account.ledger_id == ledger.id)))
    raw = balances(db, ledger.id, end=at)
    bal = rollup(accounts, raw)
    total_assets = sum((natural(raw.get(a.id, ZERO), a.type) for a in accounts if a.type == "asset"), ZERO)
    total_liab = sum((natural(raw.get(a.id, ZERO), a.type) for a in accounts if a.type == "liability"), ZERO)
    income = sum((natural(raw.get(a.id, ZERO), a.type) for a in accounts if a.type == "income"), ZERO)
    expense = sum((natural(raw.get(a.id, ZERO), a.type) for a in accounts if a.type == "expense"), ZERO)
    result = income - expense  # cumulative result not yet closed into equity
    return {
        "at": at,
        "assets": _tree_rows(accounts, bal, ("asset",)),
        "liabilities": _tree_rows(accounts, bal, ("liability",)),
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "result": result,
        "total_passive": total_liab + result,
        "balanced": total_assets == total_liab + result,
        "net_worth": total_assets - sum(
            (natural(raw.get(a.id, ZERO), a.type) for a in accounts if a.type == "liability" and a.kind != "equity"),
            ZERO,
        ),
    }


def income_statement(db: Session, ledger: Ledger, start: dt.date, end: dt.date) -> dict:
    accounts = list(db.scalars(select(Account).where(Account.ledger_id == ledger.id)))
    raw = balances(db, ledger.id, start=start, end=end)
    bal = rollup(accounts, raw)
    income = sum((natural(raw.get(a.id, ZERO), a.type) for a in accounts if a.type == "income"), ZERO)
    expense = sum((natural(raw.get(a.id, ZERO), a.type) for a in accounts if a.type == "expense"), ZERO)
    return {
        "start": start,
        "end": end,
        "income": _tree_rows(accounts, bal, ("income",)),
        "expenses": _tree_rows(accounts, bal, ("expense",)),
        "total_income": income,
        "total_expenses": expense,
        "result": income - expense,
    }


def general_ledger(db: Session, ledger: Ledger, account_ids: list[int], start: dt.date, end: dt.date) -> list[dict]:
    out = []
    accounts = db.scalars(
        select(Account).where(Account.ledger_id == ledger.id, Account.id.in_(account_ids)).order_by(Account.code)
    ).all()
    for acc in accounts:
        opening_raw = db.scalar(
            select(func.coalesce(func.sum(JournalLine.amount), 0))
            .join(Transaction)
            .where(JournalLine.account_id == acc.id, Transaction.date < start)
        )
        lines = db.scalars(
            select(JournalLine)
            .join(Transaction)
            .options(selectinload(JournalLine.transaction).selectinload(Transaction.lines).selectinload(JournalLine.account))
            .where(JournalLine.account_id == acc.id, Transaction.date >= start, Transaction.date <= end)
            .order_by(Transaction.date, Transaction.id, JournalLine.position)
        ).all()
        running = natural(Decimal(opening_raw), acc.type)
        rows = []
        tot_d = tot_c = ZERO
        for ln in lines:
            tx = ln.transaction
            counter = [f"{x.account.code}" for x in tx.lines if x.id != ln.id]
            running += natural(ln.amount, acc.type)
            d = ln.amount if ln.amount > 0 else ZERO
            c = -ln.amount if ln.amount < 0 else ZERO
            tot_d += d
            tot_c += c
            rows.append({
                "date": tx.date, "tx_id": tx.id, "description": ln.memo or tx.description,
                "counterparty": tx.counterparty, "counter": ", ".join(counter),
                "debit": d, "credit": c, "balance": running, "state": tx.state,
            })
        out.append({
            "account": {"id": acc.id, "code": acc.code, "name": acc.name, "type": acc.type},
            "opening": natural(Decimal(opening_raw), acc.type),
            "rows": rows, "total_debit": tot_d, "total_credit": tot_c, "closing": running,
        })
    return out


def journal(db: Session, ledger: Ledger, start: dt.date, end: dt.date) -> list[dict]:
    txs = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(JournalLine.account))
        .where(Transaction.ledger_id == ledger.id, Transaction.date >= start, Transaction.date <= end)
        .order_by(Transaction.date, Transaction.id)
    ).all()
    return [
        {
            "id": t.id, "date": t.date, "description": t.description, "counterparty": t.counterparty,
            "state": t.state,
            "lines": [
                {"code": ln.account.code, "name": ln.account.name, "memo": ln.memo,
                 "debit": ln.amount if ln.amount > 0 else None, "credit": -ln.amount if ln.amount < 0 else None}
                for ln in t.lines
            ],
        }
        for t in txs
    ]


def monthly(db: Session, ledger: Ledger, start: dt.date, end: dt.date, types=("expense", "income")) -> dict:
    """Totals per (account, month) for income/expense accounts, natural sign."""
    y = extract("year", Transaction.date)
    m = extract("month", Transaction.date)
    rows = db.execute(
        select(Account.id, Account.code, Account.name, Account.type, y, m, func.sum(JournalLine.amount))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(Transaction, Transaction.id == JournalLine.transaction_id)
        .where(Account.ledger_id == ledger.id, Account.type.in_(types), Transaction.date.between(start, end))
        .group_by(Account.id, Account.code, Account.name, Account.type, y, m)
    ).all()
    months = []
    d = dt.date(start.year, start.month, 1)
    while d <= end:
        months.append(f"{d.year}-{d.month:02d}")
        d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    table: dict[int, dict] = {}
    for aid, code, name, typ, yy, mm, s in rows:
        r = table.setdefault(aid, {"id": aid, "code": code, "name": name, "type": typ, "months": {}, "total": ZERO})
        v = natural(Decimal(s), typ)
        r["months"][f"{int(yy)}-{int(mm):02d}"] = v
        r["total"] += v
    return {"months": months, "rows": sorted(table.values(), key=lambda r: r["code"])}


def receivables(db: Session, ledger: Ledger, at: dt.date | None = None) -> list[dict]:
    accs = db.scalars(
        select(Account).where(Account.ledger_id == ledger.id, Account.kind == "receivable", Account.is_group.is_(False))
        .order_by(Account.code)
    ).all()
    raw = balances(db, ledger.id, end=at)
    out = []
    for a in accs:
        v = raw.get(a.id, ZERO)
        # positive = they owe me ; negative = I owe them (regardless of account type)
        out.append({"id": a.id, "code": a.code, "name": a.name, "type": a.type, "balance": v})
    return out
