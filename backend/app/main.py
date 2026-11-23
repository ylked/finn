import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .config import get_settings
from .db import engine
from .mcp_server import MCPHost
from .routers import accounts, imports, inbox, insights, invoices, merchants, receipts, reports, tokens, transactions
from .worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("finn")
settings = get_settings()


@asynccontextmanager
async def lifespan(_api: FastAPI):
    if settings.secret_key in ("", "change-me") and settings.auth_mode != "disabled":
        raise RuntimeError("SECRET_KEY must be set")
    if settings.worker_enabled:
        worker.start()
    log.info("Finn started (auth=%s, ai=%s, vision=%s, mcp=%s)", settings.auth_mode, settings.ai_enabled,
             settings.vision_enabled, settings.mcp_enabled)
    try:
        if settings.mcp_enabled:
            async with app.start():
                yield
        else:
            yield
    finally:
        if settings.worker_enabled:
            worker.stop()


api = FastAPI(title="Finn", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@api.middleware("http")
async def csrf_and_headers(request: Request, call_next):
    # Mutating API calls must carry a custom header, which cross-site forms cannot set.
    if (
        request.url.path.startswith("/api/")
        and not request.url.path.startswith("/api/auth/")
        and request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.headers.get("x-finn") != "1"
        # token clients (Apple Shortcuts...): a bearer token is never sent by a browser on its own
        and not (request.url.path == "/api/inbox" and request.headers.get("authorization", "").lower().startswith("bearer "))
    ):
        return JSONResponse({"detail": "Missing X-Finn header"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


api.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="finn_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=settings.secure_cookies,
)

for r in (auth.router, accounts.router, merchants.router, transactions.router, imports.router, receipts.router,
          reports.router, insights.router, invoices.router, tokens.router, inbox.router):
    api.include_router(r)


@api.get("/api/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    return {"status": "ok"}


# ---------------------------------------------------------------- SPA
static = Path(settings.static_dir)


@api.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if static.is_dir():
        f = (static / path).resolve()
        if path and f.is_file() and static.resolve() in f.parents:
            headers = {}
            if path.startswith("assets/"):
                headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path in ("sw.js", "manifest.webmanifest", "registerSW.js"):
                headers["Cache-Control"] = "no-cache"
            return FileResponse(f, headers=headers)
        index = static / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built"}, status_code=404)


# ASGI entry point: /mcp goes to the MCP server, the rest to the FastAPI app
app = MCPHost(api)
