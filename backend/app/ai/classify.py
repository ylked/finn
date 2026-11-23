"""Classification of imported transactions: user rules first, then the AI text model.

An imported entry is booked as   <bank account>  <->  <suspense account>.
Classification replaces the suspense side with the chosen account and marks the
transaction ``auto`` so it appears in the review queue (or ``confirmed`` for rules
flagged as such).
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..accounting import PAYMENT_KINDS, get_setting
from ..config import get_settings
from ..models import Account, ClassificationRule, JournalLine, Ledger, Transaction
from .client import AIError, chat_json

log = logging.getLogger(__name__)


def tx_text(tx: Transaction) -> str:
    parts = [tx.counterparty or "", tx.description or ""]
    for ln in tx.lines:
        if ln.bank_entry is not None:
            parts += [ln.bank_entry.description or "", ln.bank_entry.remittance_info or ""]
    return " ".join(p for p in parts if p)


def split_lines(tx: Transaction) -> tuple[JournalLine | None, list[JournalLine]]:
    """Return (source line i.e. the bank/card side, suspense lines)."""
    source = next((ln for ln in tx.lines if ln.bank_entry_id is not None), None)
    if source is None:
        source = next((ln for ln in tx.lines if ln.account.kind in PAYMENT_KINDS), None)
    suspense = [ln for ln in tx.lines if ln.account.kind == "suspense"]
    return source, suspense


def apply_target(tx: Transaction, account: Account, state: str, reason: str, confidence: float | None) -> bool:
    _, suspense = split_lines(tx)
    if len(suspense) != 1:
        return False
    suspense[0].account_id = account.id
    suspense[0].account = account
    tx.state = state
    tx.ai_reason = reason[:2000]
    tx.ai_confidence = confidence
    return True


def apply_rules(db: Session, tx: Transaction) -> bool:
    source, suspense = split_lines(tx)
    if source is None or len(suspense) != 1:
        return False
    text = tx_text(tx).lower()
    direction = "in" if source.amount > 0 else "out"
    rules = db.scalars(
        select(ClassificationRule)
        .where(
            ClassificationRule.ledger_id == tx.ledger_id,
            ClassificationRule.direction.in_(["any", direction]),
            or_(
                ClassificationRule.source_account_id.is_(None),
                ClassificationRule.source_account_id == source.account_id,
            ),
        )
        .order_by(ClassificationRule.priority, ClassificationRule.id)
    ).all()
    for r in rules:
        if r.pattern.lower() in text:
            acc = r.target_account
            if acc.is_group or not acc.is_active:
                continue
            r.hits += 1
            return apply_target(tx, acc, "confirmed" if r.confirm else "auto", f"Règle / rule: « {r.pattern} »", 1.0)
    # per-person receivable keywords (e.g. TWINT reimbursements)
    persons = db.scalars(
        select(Account).where(
            Account.ledger_id == tx.ledger_id,
            Account.kind == "receivable",
            Account.is_group.is_(False),
            Account.is_active,
            Account.keywords.is_not(None),
        )
    ).all()
    words = set(re.findall(r"\w+", text))
    for p in persons:
        kw = set(re.findall(r"\w+", (p.keywords or "").lower()))
        if not kw:
            continue
        # all name tokens must appear; a single-token name only matches incoming money
        if kw <= words and (len(kw) >= 2 or direction == "in"):
            return apply_target(tx, p, "auto", f"Personne reconnue / person match: {p.name}", 0.9)
    return False


def _chart_for_prompt(db: Session, ledger_id: int, exclude: set[int]) -> list[Account]:
    return db.scalars(
        select(Account)
        .where(
            Account.ledger_id == ledger_id,
            Account.is_group.is_(False),
            Account.is_active,
            Account.kind.notin_(["suspense", "equity", "payable"]),
            Account.id.notin_(exclude or {0}),
        )
        .order_by(Account.code)
    ).all()


def _examples(db: Session, tx: Transaction, limit: int = 12) -> list[dict]:
    """Recent confirmed classifications of similar transactions (few-shot learning from the user)."""
    key = (tx.counterparty or "").split(" ")[0] if tx.counterparty else None
    q = (
        select(Transaction)
        .where(Transaction.ledger_id == tx.ledger_id, Transaction.state == "confirmed", Transaction.id != tx.id)
        .where(Transaction.source.in_(["bank", "csv"]))
        .order_by(Transaction.date.desc())
    )
    rows: list[Transaction] = []
    if key and len(key) >= 3:
        rows = list(db.scalars(q.where(Transaction.counterparty.ilike(f"%{key}%")).limit(limit)))
    if len(rows) < limit:
        rows += [r for r in db.scalars(q.limit(limit * 2)) if r not in rows][: limit - len(rows)]
    out = []
    for r in rows:
        src, _ = split_lines(r)
        others = [ln for ln in r.lines if ln is not src]
        if src is None or not others:
            continue
        out.append(
            {
                "text": f"{r.counterparty or ''} | {r.description}"[:200],
                "amount": str(src.amount),
                "accounts": [f"{ln.account.code} {ln.account.name}" for ln in others],
            }
        )
    return out


SYSTEM_PROMPT = """You are a meticulous accountant classifying personal bank and credit card transactions \
in a double-entry ledger (Swiss context, amounts in {ccy}).
Given ONE transaction from account "{source}" and the chart of accounts, choose the single best \
counter-account. Rules:
- Money out (negative amount) to a merchant -> usually an expense (charge) account.
- Money in -> income (produit), a refund, or repayment by a person (receivable account of that person).
- Transfers between the user's own accounts (bank, savings, Revolut, credit card payment) -> the matching asset / \
liability account, NOT an expense.
- A payment to a credit card issuer settles the credit card liability account.
- If a person (individual name) sends money and a receivable account exists for that person, use it.
- The user (account holder) is: {owners}. Money from/to the user's own name is a transfer between the user's \
own accounts (e.g. online payment accounts, savings), never income or expense.
- Use the user's past classifications as strong evidence.
- Never invent account codes. Answer with JSON only:
{{"account_code": "<code>", "confidence": <0..1>, "reason": "<short reason in {lang}>", \
"counterparty": "<clean merchant or person name>", "merchant": "<brand / shop name without branch, city or \
legal form, e.g. 'Migros' for 'Migros M Dorigny'; null for people and own-account transfers>", \
"merchant_domain": "<the merchant's main website domain if you are confident, e.g. 'migros.ch', else null>"}}"""


def classify_with_ai(db: Session, tx: Transaction) -> bool:
    settings = get_settings()
    ledger = db.get(Ledger, tx.ledger_id)
    source, suspense = split_lines(tx)
    if source is None or len(suspense) != 1:
        tx.ai_status = "skipped"
        return False
    accounts = _chart_for_prompt(db, tx.ledger_id, {source.account_id})
    chart = [
        {
            "code": a.code,
            "name": a.name,
            "type": a.type,
            "kind": a.kind,
            **({"hint": a.description} if a.description else {}),
            **({"keywords": a.keywords} if a.keywords else {}),
        }
        for a in accounts
    ]
    be = source.bank_entry
    payload = {
        "date": str(tx.date),
        "amount": str(source.amount),
        "currency": ledger.base_currency,
        "counterparty": tx.counterparty,
        "description": tx.description,
        "remittance": be.remittance_info if be else None,
        "bank_code": be.tx_code if be else None,
        "original_amount": f"{be.orig_amount} {be.orig_currency}" if be and be.orig_amount else None,
    }
    lang = "French" if ledger.locale == "fr" else "English"
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                ccy=ledger.base_currency,
                source=source.account.name,
                lang=lang,
                owners=", ".join((ledger.settings or {}).get("owner_names", [])) or "unknown",
            ),
        },
        {
            "role": "user",
            "content": "Chart of accounts:\n"
            + json.dumps(chart, ensure_ascii=False)
            + "\n\nPast classifications by the user:\n"
            + json.dumps(_examples(db, tx), ensure_ascii=False)
            + "\n\nTransaction:\n"
            + json.dumps(payload, ensure_ascii=False),
        },
    ]
    data = chat_json(settings.ai_text_model, messages)
    code = str(data.get("account_code", "")).strip()
    target = next((a for a in accounts if a.code == code), None)
    if target is None:
        raise AIError(f"Model proposed unknown account '{code}'")
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    reason = str(data.get("reason") or "")
    cp = str(data.get("counterparty") or "").strip()
    if cp and not tx.counterparty:
        tx.counterparty = cp[:255]
    if tx.merchant_id is None and not tx.merchant_locked:
        from ..merchants import Matcher

        mname = data.get("merchant")
        Matcher(db, ledger).detect(tx, ai_name=str(mname)[:120] if isinstance(mname, str) else None,
                                   ai_domain=data.get("merchant_domain") if isinstance(data.get("merchant_domain"), str) else None)
    min_conf = float(get_setting(ledger, "ai_min_confidence", 0.0) or 0.0)
    if conf < min_conf:
        tx.ai_reason = f"Suggestion {target.code} {target.name} ({conf:.0%}): {reason}"
        tx.ai_confidence = conf
        return False
    return apply_target(tx, target, "auto", f"IA: {reason}", conf)


def amount_str(d: Decimal) -> str:
    return f"{d:.2f}"


def apply_rules_to_unclassified(db: Session, ledger_id: int) -> int:
    """Run rules / person matching on entries still sitting on the suspense account."""
    n = 0
    for tx in db.scalars(
        select(Transaction).where(Transaction.ledger_id == ledger_id, Transaction.state == "unclassified")
    ):
        if apply_rules(db, tx):
            if tx.ai_status == "pending":
                tx.ai_status = "done"
            n += 1
    return n
