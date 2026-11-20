"""Ponto de entrada do servidor EINSTEIN REMOTE.

Executar (desenvolvimento):
    uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8000

Produção (HTTPS/WSS obrigatório):
    uvicorn app.main:app --app-dir server --host 0.0.0.0 --port 8443 \
        --ssl-keyfile certs/server.key --ssl-certfile certs/server.crt
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .auth import router as auth_router
from .auth import sessions_router
from .config import Settings, load_settings
from .db import Database
from .devices import router as devices_router
from .hub import Hub
from .security import RateLimiter
from .ws import router as ws_router

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self' ws: wss:; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def housekeeping():
            while True:
                await asyncio.sleep(5)
                await app.state.hub.expire_pending()
                if int(time.time()) % 3600 < 5:
                    app.state.db.prune_metrics(time.time() - 30 * 24 * 3600)

        task = asyncio.create_task(housekeeping())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.db.close()

    app = FastAPI(title="EINSTEIN REMOTE", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    db = Database(settings.db_path)
    app.state.settings = settings
    app.state.db = db
    app.state.hub = Hub(db, settings)
    app.state.limiter = RateLimiter(settings.max_login_failures, settings.lockout_seconds)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "agents_online": len(app.state.hub.agents)}

    app.include_router(auth_router)
    app.include_router(sessions_router)
    app.include_router(devices_router)
    app.include_router(ws_router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Não encontrado"}, status_code=404)
        return FileResponse(settings.web_dir / "index.html")

    if settings.web_dir.exists():
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app


app = create_app()
