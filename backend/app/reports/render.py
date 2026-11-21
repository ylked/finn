"""PDF (WeasyPrint) and CSV rendering of reports."""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html"])
)

LABELS = {
    "fr": {
        "balance_sheet": "Bilan", "income_statement": "Compte de résultat", "ledger": "Grand livre",
        "journal": "Journal", "monthly": "Charges et produits par mois", "assets": "Actifs",
        "liabilities": "Passifs", "total_assets": "Total des actifs", "total_liabilities": "Total des passifs",
        "result": "Résultat", "net_worth": "Fortune nette", "income": "Produits", "expenses": "Charges",
        "total_income": "Total des produits", "total_expenses": "Total des charges", "date": "Date",
        "description": "Libellé", "counter": "Contrepartie", "debit": "Débit", "credit": "Crédit",
        "balance": "Solde", "opening": "Solde à nouveau", "closing": "Solde final", "account": "Compte",
        "total": "Total", "at": "Au", "period": "Période du", "to": "au", "generated": "Généré le",
        "unbalanced": "Attention : le bilan n'est pas équilibré.", "state_auto": "à valider",
        "state_unclassified": "non classée", "state_confirmed": "validée",
    },
    "en": {
        "balance_sheet": "Balance sheet", "income_statement": "Income statement", "ledger": "General ledger",
        "journal": "Journal", "monthly": "Income and expenses by month", "assets": "Assets",
        "liabilities": "Liabilities & equity", "total_assets": "Total assets",
        "total_liabilities": "Total liabilities & equity", "result": "Net result", "net_worth": "Net worth",
        "income": "Income", "expenses": "Expenses", "total_income": "Total income",
        "total_expenses": "Total expenses", "date": "Date", "description": "Description", "counter": "Counter",
        "debit": "Debit", "credit": "Credit", "balance": "Balance", "opening": "Opening balance",
        "closing": "Closing balance", "account": "Account", "total": "Total", "at": "As of",
        "period": "Period from", "to": "to", "generated": "Generated on",
        "unbalanced": "Warning: the balance sheet does not balance.", "state_auto": "to review",
        "state_unclassified": "unclassified", "state_confirmed": "confirmed",
    },
}


def money(v) -> str:
    if v is None:
        return ""
    d = Decimal(v).quantize(Decimal("0.01"))
    s = f"{abs(d):,.2f}".replace(",", "’")
    return f"-{s}" if d < 0 else s


def fmt_date(d: dt.date, lang: str = "fr") -> str:
    return d.strftime("%d.%m.%Y") if lang == "fr" else d.isoformat()


def pdf(template: str, *, lang: str, title_key: str, subtitle: str, data, ledger_name: str, ccy: str) -> bytes:
    from weasyprint import HTML

    t = LABELS.get(lang, LABELS["fr"])
    html = _env.get_template(f"{template}.html").render(
        t=t, d=data, money=money, date=lambda x: fmt_date(x, lang), lang=lang, ccy=ccy,
        title=t[title_key], subtitle=subtitle, ledger_name=ledger_name,
        generated=f"{t['generated']} {fmt_date(dt.date.today(), lang)}",
    )
    return HTML(string=html).write_pdf()


def csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else (f"{v:.2f}" if isinstance(v, Decimal) else v) for v in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")
