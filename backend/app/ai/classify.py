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
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, object_session, selectinload

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
    from ..matching import merge_mirror

    db = object_session(tx)
    if db is not None:
        merge_mirror(db, tx)
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


@dataclass
class _Past:
    """A confirmed statement entry, as evidence for classifying new ones."""

    id: int
    date: str
    text: str
    words: set[str]
    merchant_id: int | None
    merchant: str | None
    amount: str
    accounts: list[str]
    lines: list[tuple[int, str, Decimal]]  # (account id, "code name", amount): to read transfers from either side

    def seen_from(self, account_id: int) -> tuple[str, list[str]]:
        """(amount, counter accounts) as seen from ``account_id`` when the entry touches it."""
        mine = [x for x in self.lines if x[0] == account_id]
        if not mine:
            return self.amount, self.accounts
        return str(sum(x[2] for x in mine)), [x[1] for x in self.lines if x[0] != account_id]


_HISTORY: dict[int, tuple[float, list[_Past]]] = {}
_HISTORY_TTL = 120.0
_HISTORY_SIZE = 2000


def _history(db: Session, ledger_id: int) -> list[_Past]:
    """The user's latest confirmed classifications (cached briefly: the worker classifies in batches)."""
    from ..matching import tokens

    hit = _HISTORY.get(ledger_id)
    if hit and time.monotonic() - hit[0] < _HISTORY_TTL:
        return hit[1]
    rows = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(JournalLine.account), selectinload(Transaction.merchant))
        .where(Transaction.ledger_id == ledger_id, Transaction.state == "confirmed",
               Transaction.source.in_(["bank", "csv"]))
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(_HISTORY_SIZE)
    ).all()
    out = []
    for r in rows:
        src, _ = split_lines(r)
        others = [ln for ln in r.lines if ln is not src]
        if src is None or not others:
            continue
        text = f"{r.counterparty or ''} | {r.description}"[:200]
        out.append(_Past(r.id, str(r.date), text, tokens(r.counterparty, r.description), r.merchant_id,
                         r.merchant.name if r.merchant else None, str(src.amount),
                         [f"{ln.account.code} {ln.account.name}" for ln in others],
                         [(ln.account_id, f"{ln.account.code} {ln.account.name}", ln.amount) for ln in r.lines]))
    _HISTORY[ledger_id] = (time.monotonic(), out)
    return out


def _evidence(db: Session, tx: Transaction, source: JournalLine, limit: int = 12
              ) -> tuple[list[dict], list[str], dict[str, list[str]]]:
    """(similar past entries, this merchant's past categories, typical merchants per account code)."""
    from ..matching import similarity, tokens

    past = [p for p in _history(db, tx.ledger_id) if p.id != tx.id]
    words = tokens(tx.counterparty, tx.description)
    same = [p for p in past if tx.merchant_id and p.merchant_id == tx.merchant_id]
    # a merchant paid from this same account says more (a prepaid card is topped up by card, then spent on its own)
    out_ = source.amount < 0
    same = [p for p in same if (Decimal(p.seen_from(source.account_id)[0]) < 0) == out_]  # purchases vs refunds
    here = [p for p in same if any(x[0] == source.account_id for x in p.lines)]
    same = here or same
    # recurring payments (rent, insurance…) often have a bare text: the exact amount is the clue
    amount = [p for p in past if p not in same and p.seen_from(source.account_id)[0] == str(source.amount)
              and similarity(words, p.words) > 0][:4]
    scored = sorted(((similarity(words, p.words), i, p) for i, p in enumerate(past) if p not in same and p not in amount),
                    key=lambda x: (-x[0], x[1]))
    similar = same[:6] + amount + [p for sc, _, p in scored if sc >= 0.5]
    examples = []
    for p in similar[:limit]:
        amt, accs = p.seen_from(source.account_id)
        examples.append({"text": p.text, "merchant": p.merchant, "amount": amt, "accounts": accs})

    by_merchant = Counter(" + ".join(p.seen_from(source.account_id)[1]) for p in same)
    merchant_hist = [f"{acc}: {n}x" for acc, n in by_merchant.most_common(4)]

    typical: dict[str, Counter] = {}
    for p in past:
        if p.merchant and len(p.accounts) == 1:
            typical.setdefault(p.accounts[0].split(" ")[0], Counter())[p.merchant] += 1
    return examples, merchant_hist, {code: [m for m, _ in c.most_common(6)] for code, c in typical.items()}


SYSTEM_PROMPT = """You are a meticulous accountant classifying personal bank and credit card transactions \
in a double-entry ledger (Swiss context, amounts in {ccy}).
Given ONE transaction from account "{source}" and the chart of accounts, choose the single best \
counter-account.

Weigh the evidence in this order:
1. "merchant_history": how the user classified this same merchant before. Follow the majority unless this \
transaction is clearly different (e.g. a refund, or an amount far from usual).
2. "similar_past": the user's past classifications of similar statement texts.
3. Each account's "group", "hint", "keywords" and "typical_merchants" (merchants the user put there).
4. Your knowledge of Swiss and international merchants, e.g. Migros / Coop / Denner / Aldi / Lidl -> groceries, \
SBB / CFF / TL / TPG -> public transport, Swisscom / Salt / Sunrise -> phone & internet, Galaxus / Digitec -> \
according to what is usually bought, pharmacies -> medical. Payment intermediaries (SumUp, Twint, PayPal, \
Stripe, Worldline, "SQ *") are not the merchant: classify by the name after them.

Rules:
- Money out (direction "out") to a merchant -> usually an expense (charge) account.
- Money in -> income (produit), a refund (same category as the purchase, or a refunds account if the chart has \
one), or repayment by a person (receivable account of that person).
- Transfers between the user's own accounts (bank, savings, Revolut, credit card payment, prepaid cards) -> the \
matching asset / liability account, NOT an expense.
- A payment to a credit card issuer settles the credit card liability account.
- If a person (individual name) sends money and a receivable account exists for that person, use it.
- The user (account holder) is: {owners}. Money from/to the user's own name is a transfer between the user's \
own accounts (e.g. online payment accounts, savings), never income or expense.
- TWINT, Apple Pay, debit card, e-banking are payment methods, not accounts: "Credit UBS TWINT // NAME" means NAME \
sent money to the user, "Payment UBS TWINT // SHOP" is a purchase at SHOP.
- Money in is never put on an expense account, except a refund of a purchase; selling an item is income.
- Prefer the most specific category; a "miscellaneous" account only when nothing else fits.
- confidence: 0.9 or more only when the merchant history or a near-identical past entry supports the choice; \
0.6-0.85 from the merchant's name and general knowledge; below 0.5 when guessing.
- Never invent account codes. Answer with JSON only, the reason first:
{{"reason": "<short reason in {lang}, citing the evidence used>", "account_code": "<code>", \
"confidence": <0..1>, "counterparty": "<clean merchant or person name>", "merchant": "<brand / shop name \
without branch, city or legal form, e.g. 'Migros' for 'Migros M Dorigny'; null for people and own-account \
transfers>", "merchant_domain": "<the merchant's main website domain if you are confident, e.g. 'migros.ch', \
else null>"}}"""


def pick_account(answer: object, accounts: list[Account]) -> Account | None:
    """The account the model named: its code, possibly followed by the name ("1100.05 Alysson Bilat")."""
    code = str(answer or "").strip()
    by_code = {a.code: a for a in accounts}
    if code in by_code:
        return by_code[code]
    first = code.split(" ")[0].strip(" :-–")
    return by_code.get(first)


def build_messages(db: Session, ledger: Ledger, tx: Transaction, source: JournalLine) -> tuple[list[dict], list[Account]]:
    """The classification prompt for ``tx`` and the accounts it may choose from (no side effect)."""
    accounts = _chart_for_prompt(db, tx.ledger_id, {source.account_id})
    groups = {a.id: a.name for a in db.scalars(select(Account).where(Account.ledger_id == tx.ledger_id, Account.is_group))}
    examples, merchant_hist, typical = _evidence(db, tx, source)
    chart = [
        {
            "code": a.code,
            "name": a.name,
            **({"group": groups[a.parent_id]} if a.parent_id in groups else {}),
            "type": a.type,
            "kind": a.kind,
            **({"hint": a.description} if a.description else {}),
            **({"keywords": a.keywords} if a.keywords else {}),
            **({"typical_merchants": typical[a.code]} if typical.get(a.code) else {}),
        }
        for a in accounts
    ]
    be = source.bank_entry
    payload = {
        "date": str(tx.date),
        "amount": str(source.amount),
        "direction": "out" if source.amount < 0 else "in",
        "currency": ledger.base_currency,
        "counterparty": tx.counterparty,
        "description": tx.description,
        "merchant": tx.merchant.name if tx.merchant else None,
        "remittance": be.remittance_info if be else None,
        "bank_code": be.tx_code if be else None,
        "original_amount": f"{be.orig_amount} {be.orig_currency}" if be and be.orig_amount else None,
    }
    lang = "French" if ledger.locale == "fr" else "English"
    system = SYSTEM_PROMPT.format(
        ccy=ledger.base_currency,
        source=source.account.name,
        lang=lang,
        owners=", ".join((ledger.settings or {}).get("owner_names", [])) or "unknown",
    )
    user = (
        "Chart of accounts:\n" + json.dumps(chart, ensure_ascii=False)
        + "\n\nmerchant_history:\n" + json.dumps(merchant_hist, ensure_ascii=False)
        + "\n\nsimilar_past:\n" + json.dumps(examples, ensure_ascii=False)
        + "\n\nTransaction:\n" + json.dumps(payload, ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], accounts


def classify_with_ai(db: Session, tx: Transaction) -> bool:
    settings = get_settings()
    ledger = db.get(Ledger, tx.ledger_id)
    source, suspense = split_lines(tx)
    if source is None or len(suspense) != 1:
        tx.ai_status = "skipped"
        return False
    messages, accounts = build_messages(db, ledger, tx, source)
    data = chat_json(settings.ai_text_model, messages)
    target = pick_account(data.get("account_code"), accounts)
    code = str(data.get("account_code", "")).strip()
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
        if sa_inspect(tx).was_deleted:
            continue  # merged into its mirror by an earlier one
        if apply_rules(db, tx):
            if tx.ai_status == "pending":
                tx.ai_status = "done"
            n += 1
    return n
