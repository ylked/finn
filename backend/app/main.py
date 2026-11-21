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
from .routers import accounts, imports, receipts, reports, transactions
from .worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("finn")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.secret_key in ("", "change-me") and settings.auth_mode != "disabled":
        raise RuntimeError("SECRET_KEY must be set")
    worker.start()
    log.info("Finn started (auth=%s, ai=%s, vision=%s)", settings.auth_mode, settings.ai_enabled, settings.vision_enabled)
    yield
    worker.stop()


app = FastAPI(title="Finn", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def csrf_and_headers(request: Request, call_next):
    # Mutating API calls must carry a custom header, which cross-site forms cannot set.
    if (
        request.url.path.startswith("/api/")
        and not request.url.path.startswith("/api/auth/")
        and request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.headers.get("x-finn") != "1"
    ):
        return JSONResponse({"detail": "Missing X-Finn header"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="finn_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=settings.secure_cookies,
)

for r in (auth.router, accounts.router, transactions.router, imports.router, receipts.router, reports.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    return {"status": "ok"}


# ---------------------------------------------------------------- SPA
static = Path(settings.static_dir)


@app.get("/{path:path}", include_in_schema=False)
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
