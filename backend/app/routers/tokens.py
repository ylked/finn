"""Personal access tokens, used by MCP clients (Claude, Cursor, ...) to reach /mcp.

The plain token is shown once at creation; only its SHA-256 hash is stored.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Ctx, ctx
from ..config import get_settings
from ..db import get_db
from ..models import ApiToken

router = APIRouter(prefix="/api/tokens", tags=["tokens"])
PREFIX = "finn_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    can_write: bool = False
    expires_days: int | None = Field(default=None, ge=1, le=3650)


class TokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    can_write: bool
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    expires_at: dt.datetime | None
    model_config = {"from_attributes": True}


class TokenCreated(TokenOut):
    token: str


@router.get("", response_model=list[TokenOut])
def list_tokens(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    return db.scalars(
        select(ApiToken).where(ApiToken.user_id == c.user.id, ApiToken.ledger_id == c.ledger.id).order_by(ApiToken.id)
    ).all()


@router.post("", response_model=TokenCreated)
def create_token(body: TokenIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    if body.can_write and not c.can_write:
        raise HTTPException(403, "Read-only access to this ledger")
    token = PREFIX + secrets.token_urlsafe(32)
    t = ApiToken(
        user_id=c.user.id,
        ledger_id=c.ledger.id,
        name=body.name.strip(),
        token_hash=hash_token(token),
        prefix=token[:12],
        can_write=body.can_write,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=body.expires_days) if body.expires_days else None,
    )
    db.add(t)
    db.commit()
    return TokenCreated(**TokenOut.model_validate(t).model_dump(), token=token)


@router.delete("/{token_id}")
def revoke(token_id: int, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    t = db.get(ApiToken, token_id)
    if t is None or t.user_id != c.user.id:
        raise HTTPException(404, "Token not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.get("/info")
def mcp_info():
    s = get_settings()
    return {
        "enabled": s.mcp_enabled,
        "url": s.app_base_url.rstrip("/") + "/mcp",
        "oauth": s.mcp_enabled and s.mcp_oauth and s.auth_mode == "oidc",
    }
