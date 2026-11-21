"""Authentication (OIDC authorization code + PKCE) and request context.

A signed session cookie holds the user id. Each request resolves the active ledger from the
``X-Ledger-Id`` header (optional) or the user's first membership, which keeps the door open
for multiple / shared ledgers.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Ledger, LedgerMember, User
from .seed import create_ledger

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
oauth = OAuth()
_registered = False


def _oidc():
    global _registered
    s = get_settings()
    if not _registered:
        issuer = s.oidc_issuer.rstrip("/") + "/"
        oauth.register(
            "oidc",
            client_id=s.oidc_client_id,
            client_secret=s.oidc_client_secret,
            server_metadata_url=issuer + ".well-known/openid-configuration",
            client_kwargs={"scope": s.oidc_scopes, "code_challenge_method": "S256"},
        )
        _registered = True
    return oauth.oidc


def upsert_user(db: Session, sub: str, email: str | None, name: str | None) -> User:
    s = get_settings()
    user = db.scalar(select(User).where(User.oidc_sub == sub))
    if user is None:
        user = User(oidc_sub=sub, email=email, name=name, locale=s.default_locale)
        db.add(user)
        db.flush()
    else:
        user.email, user.name = email or user.email, name or user.name
    user.last_login_at = dt.datetime.now(dt.UTC)
    if not db.scalar(select(LedgerMember).where(LedgerMember.user_id == user.id)):
        ledger = create_ledger(db, name=f"{name or email or 'Finn'}", locale=user.locale, currency=s.base_currency)
        db.add(LedgerMember(ledger_id=ledger.id, user_id=user.id, role="owner"))
    db.commit()
    return user


@router.get("/login")
async def login(request: Request):
    s = get_settings()
    if s.auth_mode == "disabled":
        return RedirectResponse("/")
    redirect_uri = s.app_base_url.rstrip("/") + "/api/auth/callback"
    return await _oidc().authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    try:
        token = await _oidc().authorize_access_token(request)
    except OAuthError as e:
        log.warning("OIDC error: %s", e)
        raise HTTPException(401, f"Authentication failed: {e.error}") from e
    info = token.get("userinfo") or await _oidc().userinfo(token=token)
    email = (info.get("email") or "").lower() or None
    groups = info.get("groups") or []
    if s.allowed_groups and not set(groups) & set(s.allowed_groups):
        raise HTTPException(403, "Your account is not allowed to use this application (group)")
    if s.allowed_emails and email not in s.allowed_emails:
        raise HTTPException(403, "Your account is not allowed to use this application (email)")
    user = upsert_user(db, info["sub"], email, info.get("name") or info.get("preferred_username"))
    request.session.clear()
    request.session["uid"] = user.id
    request.session["id_token"] = token.get("id_token")
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    s = get_settings()
    id_token = request.session.get("id_token")
    request.session.clear()
    if s.auth_mode == "oidc" and s.oidc_end_session:
        try:
            meta = await _oidc().load_server_metadata()
            end = meta.get("end_session_endpoint")
            if end:
                params = {"post_logout_redirect_uri": s.app_base_url.rstrip("/") + "/"}
                if id_token:
                    params["id_token_hint"] = id_token
                return RedirectResponse(f"{end}?{urlencode(params)}")
        except Exception:
            log.exception("could not load end_session_endpoint")
    return RedirectResponse("/")


@dataclass
class Ctx:
    user: User
    ledger: Ledger
    role: str

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "editor")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    s = get_settings()
    if s.auth_mode == "disabled":
        user = db.scalar(select(User).where(User.oidc_sub == "local"))
        return user or upsert_user(db, "local", "local@localhost", "Local user")
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


def ctx(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Ctx:
    wanted = request.headers.get("x-ledger-id")
    q = select(LedgerMember).where(LedgerMember.user_id == user.id)
    if wanted and wanted.isdigit():
        q = q.where(LedgerMember.ledger_id == int(wanted))
    m = db.scalar(q.order_by(LedgerMember.ledger_id).limit(1))
    if m is None:
        raise HTTPException(403, "No access to this ledger")
    return Ctx(user=user, ledger=db.get(Ledger, m.ledger_id), role=m.role)


def writer(c: Ctx = Depends(ctx)) -> Ctx:
    if not c.can_write:
        raise HTTPException(403, "Read-only access")
    return c
