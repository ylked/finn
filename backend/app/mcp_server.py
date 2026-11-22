"""MCP (Model Context Protocol) server, served at ``/mcp`` (streamable HTTP, stateless).

Lets AI assistants (Claude, ChatGPT, Cursor, ...) read the books and, with a write-enabled
token, record and classify entries. Tools reuse the REST handlers, so the same validation and
accounting rules apply.

Authentication (``Authorization: Bearer <token>``):
- personal access tokens created in Settings (``finn_...``), bound to one ledger, read-only or read/write;
- optionally (``MCP_OAUTH=true``) access tokens issued by the OIDC provider for this app, which lets
  OAuth-capable clients (claude.ai connectors) log in through authentik.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from decimal import Decimal
from typing import Annotated, Any

import anyio
import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, OctKey
from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.applications import Starlette

from . import schemas
from .accounting import PAYMENT_KINDS
from .ai.classify import split_lines
from .auth import Ctx
from .config import get_settings
from .db import SessionLocal
from .models import Account, ApiToken, Ledger, LedgerMember, User
from .reports import service as reports
from .routers import accounts as accounts_api
from .routers import reports as reports_api
from .routers import transactions as tx_api
from .routers.tokens import PREFIX, hash_token

log = logging.getLogger(__name__)

INSTRUCTIONS = """Finn is a personal double-entry ledger (comptabilité en partie double), base currency usually CHF.
Accounts have a code (Swiss-style numbering), a type (asset, liability, income, expense) and a kind
(bank, credit_card, cash, receivable = money a person owes, equity, suspense = not yet classified, other).
Journal line amounts are signed: positive = debit, negative = credit; the lines of a transaction sum to 0.
An expense paid from the bank is: expense +X, bank -X. Income: bank +X, income -X.
Imported bank lines start on the suspense account (state "unclassified"), get classified by rules or AI
(state "auto", to be reviewed) and become "confirmed" after review.
Always refer to accounts by code; call list_accounts first if you do not know the chart."""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


# ------------------------------------------------------------------ authentication
class FinnTokenVerifier:
    """Validates personal access tokens, and OIDC access tokens when MCP_OAUTH is on."""

    def __init__(self) -> None:
        self._jwks: tuple[float, Any] | None = None
        self._jwks_uri: str | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.startswith(PREFIX):
            return await anyio.to_thread.run_sync(self._verify_pat, token)
        s = get_settings()
        if s.mcp_oauth and s.auth_mode == "oidc" and token.count(".") == 2:
            try:
                return await self._verify_jwt(token)
            except Exception as e:  # noqa: BLE001 - any failure means "not authenticated"
                log.info("MCP OAuth token rejected: %s", e)
        return None

    @staticmethod
    def _verify_pat(token: str) -> AccessToken | None:
        with SessionLocal() as db:
            t = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
            now = dt.datetime.now(dt.UTC)
            if t is None or (t.expires_at and t.expires_at < now):
                return None
            if t.last_used_at is None or now - t.last_used_at > dt.timedelta(minutes=5):
                t.last_used_at = now
                db.commit()
            return AccessToken(
                token=token,
                client_id=f"pat:{t.id}",
                scopes=["finn:read", "finn:write"] if t.can_write else ["finn:read"],
                expires_at=int(t.expires_at.timestamp()) if t.expires_at else None,
                subject=str(t.user_id),
                claims={"uid": t.user_id, "ledger_id": t.ledger_id, "write": t.can_write},
            )

    async def _keys(self, force: bool = False):
        s = get_settings()
        if self._jwks and not force and time.monotonic() - self._jwks[0] < 3600:
            return self._jwks[1]
        async with httpx.AsyncClient(timeout=10) as client:
            if self._jwks_uri is None:
                meta = (await client.get(s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration")).json()
                self._jwks_uri = meta["jwks_uri"]
            keys = KeySet.import_key_set((await client.get(self._jwks_uri)).json())
        self._jwks = (time.monotonic(), keys)
        return keys

    async def _verify_jwt(self, token: str) -> AccessToken | None:
        s = get_settings()
        audience = s.mcp_oauth_audience or s.oidc_client_id
        keys = await self._keys()

        def decode(keys):
            def key_for(obj):
                # HS256 tokens (provider without a signing key) are signed with the client secret
                if obj.headers().get("alg") == "HS256":
                    return OctKey.import_key(s.oidc_client_secret)
                return keys

            return jwt.decode(token, key_for, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "HS256"])

        try:
            tok = decode(keys)
        except JoseError:
            tok = decode(await self._keys(force=True))  # signing key rotated
        claims = tok.claims
        jwt.JWTClaimsRegistry(
            leeway=30,
            exp={"essential": True},
            iss={"essential": True, "values": [s.oidc_issuer, s.oidc_issuer.rstrip("/") + "/", s.oidc_issuer.rstrip("/")]},
        ).validate(claims)
        aud = claims.get("aud")
        if audience not in (aud if isinstance(aud, list) else [aud]):
            return None
        sub = claims.get("sub")
        uid = await anyio.to_thread.run_sync(self._user_for_sub, sub)
        if uid is None:
            log.info("MCP OAuth token for unknown user (log in to the web app once first)")
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or audience),
            scopes=["finn:read", "finn:write"],
            expires_at=claims.get("exp"),
            subject=sub,
            claims={"uid": uid, "ledger_id": None, "write": True},
        )

    @staticmethod
    def _user_for_sub(sub: str | None) -> int | None:
        if not sub:
            return None
        with SessionLocal() as db:
            return db.scalar(select(User.id).where(User.oidc_sub == sub))


def _ctx(db: Session, write: bool = False) -> Ctx:
    tok = get_access_token()
    if tok is None or not tok.claims:
        raise ToolError("Not authenticated")
    claims = tok.claims
    user = db.get(User, claims["uid"])
    q = select(LedgerMember).where(LedgerMember.user_id == claims["uid"])
    if claims.get("ledger_id"):
        q = q.where(LedgerMember.ledger_id == claims["ledger_id"])
    m = db.scalar(q.order_by(LedgerMember.ledger_id).limit(1))
    if user is None or m is None:
        raise ToolError("No access to this ledger")
    role = m.role if claims.get("write") else "viewer"
    c = Ctx(user=user, ledger=db.get(Ledger, m.ledger_id), role=role)
    if write and not c.can_write:
        raise ToolError("This token is read-only. Create a token with write access in Finn > Settings.")
    return c


class _Call:
    """Opens a DB session and the ledger context; turns API errors into tool errors."""

    def __init__(self, write: bool = False):
        self.write = write

    def __enter__(self) -> tuple[Session, Ctx]:
        self.db = SessionLocal()
        try:
            return self.db, _ctx(self.db, self.write)
        except BaseException:
            self.db.close()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                self.db.rollback()
        finally:
            self.db.close()
        if isinstance(exc, HTTPException):
            raise ToolError(str(exc.detail)) from exc
        return False


def _account(db: Session, c: Ctx, code: str, postable: bool = True) -> Account:
    acc = db.scalar(select(Account).where(Account.ledger_id == c.ledger.id, Account.code == code.strip()))
    if acc is None:
        raise ToolError(f"Unknown account code '{code}'. Use list_accounts to see the chart.")
    if postable and (acc.is_group or not acc.is_active):
        raise ToolError(f"Account {acc.code} {acc.name} is a group or inactive; use one of its sub-accounts.")
    return acc


def _tx(t: schemas.TransactionOut) -> dict:
    d = t.model_dump(
        mode="json", exclude={"created_at", "updated_at", "receipts"}, exclude_none=True
    )
    d["lines"] = [
        {k: v for k, v in ln.items() if k in ("id", "account_code", "account_name", "amount", "memo", "orig_amount",
                                               "orig_currency", "bank_entry_id") and v is not None}
        for ln in d["lines"]
    ]
    if isinstance(t, schemas.TransactionDetail):
        d["bank_entries"] = [
            {k: v for k, v in be.items() if k in ("id", "booking_date", "amount", "currency", "description",
                                                   "counterparty", "remittance_info", "orig_amount", "orig_currency")
             and v is not None}
            for be in d.get("bank_entries", [])
        ]
    if t.receipts:
        d["receipt_ids"] = [r.id for r in t.receipts]
    return d


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


class LineSpec(BaseModel):
    account_code: str = Field(description="Account code, e.g. '4200'")
    amount: Decimal = Field(description="Signed amount: positive = debit, negative = credit")
    memo: str | None = None


class SplitSpec(BaseModel):
    account_code: str = Field(description="Counter-account code (expense, income, person receivable, ...)")
    amount: Decimal = Field(description="Positive share of the transaction amount assigned to this account")
    memo: str | None = None


Date = Annotated[dt.date, Field(description="ISO date, YYYY-MM-DD")]


# ------------------------------------------------------------------ tools
def build_server() -> MCPServer:
    s = get_settings()
    base = s.app_base_url.rstrip("/")
    oauth = s.mcp_oauth and s.auth_mode == "oidc" and bool(s.oidc_issuer)
    mcp = MCPServer(
        name="finn",
        title="Finn accounting",
        instructions=INSTRUCTIONS,
        website_url=base,
        token_verifier=FinnTokenVerifier(),
        auth=AuthSettings(
            issuer_url=s.oidc_issuer if oauth else base,
            resource_server_url=base + "/mcp",
            validate_token_resource=False,  # the verifier checks the audience itself
        ),
    )

    @mcp.tool(annotations=READ)
    def ledger_overview() -> dict:
        """Snapshot of the books: money account balances, net worth, this month's income/expenses and top
        expense accounts, 12-month income/expense series, people who owe money, and how many entries
        wait for review."""
        with _Call() as (db, c):
            d = reports_api.dashboard(c, db)
            d["ledger"] = {"name": c.ledger.name, "currency": c.ledger.base_currency, "role": c.role}
            return _jsonable(d)

    @mcp.tool(annotations=READ)
    def list_accounts(include_inactive: bool = False, at: Date | None = None) -> list[dict]:
        """Chart of accounts with balances (natural sign: assets/expenses positive when debit).
        Group accounts aggregate their children. `at` gives balances at that date (default: all entries)."""
        with _Call() as (db, c):
            rows = accounts_api.list_accounts(at, c, db)
            out = []
            for a in rows:
                if not a.is_active and not include_inactive:
                    continue
                r = {"code": a.code, "name": a.name, "type": a.type, "kind": a.kind, "group": a.is_group,
                     "balance": str(a.balance)}
                if a.description:
                    r["description"] = a.description
                if a.iban:
                    r["iban"] = a.iban
                out.append(r)
            return out

    @mcp.tool(annotations=READ)
    def search_transactions(
        text: Annotated[str | None, Field(description="Matches description, counterparty or notes")] = None,
        account_code: Annotated[str | None, Field(description="Only entries touching this account (or its "
                                                                "sub-accounts)")] = None,
        state: Annotated[list[str] | None, Field(description="Any of: unclassified, auto, confirmed")] = None,
        date_from: Date | None = None,
        date_to: Date | None = None,
        amount: Annotated[Decimal | None, Field(description="Exact absolute amount of any line")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: int = 0,
    ) -> dict:
        """Find journal entries, newest first. Returns {total, items}."""
        with _Call() as (db, c):
            acc_id = _account(db, c, account_code, postable=False).id if account_code else None
            page = tx_api.list_transactions(
                account_id=acc_id, state=state or [], source=None, q=text, date_from=date_from, date_to=date_to,
                unreconciled=False, amount=amount, limit=limit, offset=offset, c=c, db=db,
            )
            return {"total": page.total, "items": [_tx(t) for t in page.items]}

    @mcp.tool(annotations=READ)
    def get_transaction(transaction_id: int) -> dict:
        """One journal entry with its lines, linked bank statement lines and AI reasoning."""
        with _Call() as (db, c):
            return _tx(tx_api.get_transaction(transaction_id, c, db))

    @mcp.tool(annotations=READ)
    def balance_sheet(at: Date | None = None) -> dict:
        """Balance sheet (bilan) at a date (default today): assets, liabilities, result, net worth."""
        with _Call() as (db, c):
            return _jsonable(reports.balance_sheet(db, c.ledger, at or dt.date.today()))

    @mcp.tool(annotations=READ)
    def income_statement(start: Date, end: Date) -> dict:
        """Income statement (compte de résultat) for a period: income and expenses per account."""
        with _Call() as (db, c):
            return _jsonable(reports.income_statement(db, c.ledger, start, end))

    @mcp.tool(annotations=READ)
    def monthly_report(start: Date, end: Date) -> dict:
        """Income and expense totals per account and month (max 3 years)."""
        with _Call() as (db, c):
            if (end - start).days > 3 * 366:
                raise ToolError("Period too long (max 3 years)")
            return _jsonable(reports.monthly(db, c.ledger, start, end))

    @mcp.tool(annotations=READ)
    def account_ledger(account_code: str, start: Date, end: Date) -> dict:
        """General ledger (grand livre) of one account: opening balance, movements with running balance, closing."""
        with _Call() as (db, c):
            acc = _account(db, c, account_code, postable=False)
            ids = [acc.id] + tx_api._descendants(db, c.ledger.id, acc.id)
            return _jsonable({"accounts": reports.general_ledger(db, c.ledger, ids, start, end)})

    @mcp.tool(annotations=READ)
    def receivables() -> list[dict]:
        """What each person owes the user (positive) or is owed by the user (negative)."""
        with _Call() as (db, c):
            return _jsonable(reports.receivables(db, c.ledger))

    @mcp.tool(annotations=READ)
    def list_rules() -> list[dict]:
        """Classification rules: text pattern -> account, applied to imported entries before the AI."""
        with _Call() as (db, c):
            accs = {a.id: a for a in db.scalars(select(Account).where(Account.ledger_id == c.ledger.id))}
            return [
                {"id": r.id, "pattern": r.pattern, "direction": r.direction, "account_code": accs[r.target_account_id].code,
                 "account_name": accs[r.target_account_id].name, "auto_confirm": r.confirm, "hits": r.hits}
                for r in tx_api.list_rules(c, db)
            ]

    # -------------------------------------------------------------- write tools
    @mcp.tool(annotations=WRITE)
    def create_transaction(
        date: Date,
        description: str,
        lines: Annotated[list[LineSpec], Field(min_length=2, description="Signed lines summing to 0")],
        counterparty: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Record a manual journal entry (e.g. a cash purchase, or an expense before the bank statement arrives;
        it will be reconciled automatically when the statement line is imported).
        Example, 42.50 groceries paid by bank: [{account_code:'4000', amount:42.50}, {account_code:'1020', amount:-42.50}]."""
        with _Call(write=True) as (db, c):
            body = schemas.TransactionIn(
                date=date, description=description, counterparty=counterparty, notes=notes,
                lines=[schemas.LineIn(account_id=_account(db, c, ln.account_code).id, amount=ln.amount, memo=ln.memo)
                       for ln in lines],
            )
            return _tx(tx_api.create(body, c, db))

    @mcp.tool(annotations=WRITE)
    def classify_transaction(
        transaction_id: int,
        account_code: Annotated[str | None, Field(description="Single counter-account for the whole amount")] = None,
        splits: Annotated[list[SplitSpec] | None, Field(description="Ventilation over several accounts instead of "
                                                                     "account_code; shares must add up to the amount")] = None,
        confirm: Annotated[bool, Field(description="Mark as confirmed (reviewed) instead of 'auto'")] = False,
    ) -> dict:
        """Set the counter-account(s) of an imported bank/card entry, replacing suspense or a previous
        classification. The bank/card side stays untouched. Use a person's receivable account for money spent
        on their behalf or received back from them."""
        if bool(account_code) == bool(splits):
            raise ToolError("Give either account_code or splits")
        with _Call(write=True) as (db, c):
            tx = tx_api._load(db, c, transaction_id)
            source, _ = split_lines(tx)
            if source is None:
                raise ToolError("This entry has no bank/card side; edit it with update_transaction_lines")
            keep = [ln for ln in tx.lines if ln.bank_entry_id or ln is source]
            total = -sum((ln.amount for ln in keep), Decimal(0))  # what the counter side must carry
            sign = 1 if total > 0 else -1
            parts = splits or [SplitSpec(account_code=account_code, amount=abs(total))]
            if sum((p.amount for p in parts), Decimal(0)) != abs(total):
                raise ToolError(f"Split amounts must add up to {abs(total)}")
            new_lines = [schemas.LineIn(account_id=ln.account_id, amount=ln.amount, memo=ln.memo,
                                        orig_amount=ln.orig_amount, orig_currency=ln.orig_currency,
                                        bank_entry_id=ln.bank_entry_id) for ln in keep]
            new_lines += [schemas.LineIn(account_id=_account(db, c, p.account_code).id, amount=sign * p.amount,
                                         memo=p.memo) for p in parts]
            body = schemas.TransactionIn(date=tx.date, description=tx.description, counterparty=tx.counterparty,
                                         notes=tx.notes, lines=new_lines, confirm=confirm)
            return _tx(tx_api.update(transaction_id, body, c, db))

    @mcp.tool(annotations=WRITE)
    def update_transaction_lines(
        transaction_id: int,
        lines: Annotated[list[LineSpec], Field(min_length=2, description="All lines, signed, summing to 0")],
        description: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Replace all lines of an entry. Lines linked to a bank statement must be kept with the same account and
        amount (they are matched by account and amount); prefer classify_transaction for imported entries."""
        with _Call(write=True) as (db, c):
            tx = tx_api._load(db, c, transaction_id)
            linked = {(ln.account_id, ln.amount): ln.bank_entry_id for ln in tx.lines if ln.bank_entry_id}
            new_lines = []
            for ln in lines:
                acc = _account(db, c, ln.account_code)
                be = linked.pop((acc.id, ln.amount), None)
                new_lines.append(schemas.LineIn(account_id=acc.id, amount=ln.amount, memo=ln.memo, bank_entry_id=be))
            body = schemas.TransactionIn(date=tx.date, description=description or tx.description,
                                         counterparty=tx.counterparty, notes=tx.notes, lines=new_lines, confirm=confirm)
            return _tx(tx_api.update(transaction_id, body, c, db))

    @mcp.tool(annotations=WRITE)
    def confirm_transactions(transaction_ids: list[int]) -> dict:
        """Mark reviewed entries (state 'auto') as confirmed. Entries still on suspense are skipped."""
        with _Call(write=True) as (db, c):
            return tx_api.confirm_many(transaction_ids, c, db)

    @mcp.tool(annotations=WRITE)
    def create_rule(
        pattern: Annotated[str, Field(min_length=2, description="Case-insensitive text found in the bank line")],
        account_code: str,
        direction: Annotated[str, Field(description="any, in (money received) or out (money spent)")] = "any",
        auto_confirm: bool = False,
    ) -> dict:
        """Always classify matching imported entries to an account. Applied right away to unclassified entries."""
        with _Call(write=True) as (db, c):
            r = tx_api.create_rule(
                schemas.RuleIn(pattern=pattern, direction=direction, target_account_id=_account(db, c, account_code).id,
                               confirm=auto_confirm), c, db)
            return {"id": r.id, "pattern": r.pattern, "account_code": account_code, "direction": r.direction}

    @mcp.tool(annotations=WRITE)
    def create_person(
        name: str,
        owed_to_me: Annotated[bool, Field(description="True: asset (they owe the user); False: liability")] = True,
    ) -> dict:
        """Create a per-person receivable account, used when paying for someone and when they pay back."""
        with _Call(write=True) as (db, c):
            a = accounts_api.create_person(schemas.PersonIn(name=name, owed_to_me=owed_to_me), c, db)
            return {"code": a.code, "name": a.name, "type": a.type}

    return mcp


# ------------------------------------------------------------------ ASGI wiring
class MCPHost:
    """Routes /mcp and the OAuth protected-resource metadata to the MCP app, everything else to FastAPI.

    The MCP server is rebuilt for every application lifespan because its session manager runs once."""

    PATHS = ("/mcp", "/.well-known/oauth-protected-resource")

    def __init__(self, api):
        self.api = api
        self.server: MCPServer | None = None
        self.app: Starlette | None = None

    def start(self):
        self.server = build_server()
        self.app = self.server.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            # access requires a bearer token, so DNS rebinding cannot reach anything;
            # host checks would break behind a reverse proxy
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        return self.server.session_manager.run()

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and self.app is not None
            and (scope["path"] in ("/mcp", "/mcp/") or scope["path"].startswith("/.well-known/oauth-protected-resource"))
        ):
            if scope["path"] == "/mcp/":
                scope = {**scope, "path": "/mcp"}
            await self.app(scope, receive, send)
            return
        await self.api(scope, receive, send)
