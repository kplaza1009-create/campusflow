"""FastAPI application: routers, CORS, the Origin check, security headers, the health
endpoint, and (when CAMPUSFLOW_FRONTEND_DIST is set) the built frontend."""

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.deps import UNSAFE_METHODS
from app.routers import account, assignments, auth, courses, events, notifications

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("campusflow.http")

# The interactive docs load scripts from a CDN, which the CSP below forbids: dev only.
app = FastAPI(
    title="CampusFlow API",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

SECURITY_HEADERS = {
    # Everything loads from this origin. Inline styles are allowed because the toast
    # library injects a <style> tag; no inline or third-party script runs anywhere.
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
if settings.is_production:
    SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000"


@app.middleware("http")
async def origin_check(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Reject state-changing requests from origins we don't trust.

    Browsers attach an Origin header to cross-site POST/PUT/PATCH/DELETE requests.
    This blocks cross-site form posts even on endpoints that don't have a session
    yet (login and registration), where the CSRF token can't help.
    """
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        allowed = set(settings.allowed_origins) | {str(request.base_url).rstrip("/")}
        if origin is not None and origin not in allowed:
            log.warning("blocked cross-origin request origin=%s path=%s", origin, request.url.path)
            return JSONResponse({"detail": "Request origin not allowed."}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def access_log(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    # Path only: query strings can contain search text, and we never log cookies or bodies.
    log.info("%s %s %s %.1fms", request.method, request.url.path, response.status_code, ms)
    return response


# Registered last, so it wraps the others and every response gets the headers.
@app.middleware("http")
async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# Only needed when the frontend is served from a different origin than the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
    expose_headers=["Idempotent-Replayed"],
)

app.include_router(auth.router)
app.include_router(account.router)
app.include_router(courses.router)
app.include_router(assignments.router)
app.include_router(events.router)
app.include_router(notifications.router)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


CACHE_FOREVER = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


def serve_frontend(target: FastAPI, dist: Path) -> None:
    """Serve the built app: real files as they are, any other path as index.html so the
    client-side router can handle it. Unknown /api paths stay JSON 404s."""
    dist = dist.resolve()
    index = dist / "index.html"
    if not index.is_file():
        raise RuntimeError(f"CAMPUSFLOW_FRONTEND_DIST has no index.html: {dist}")

    # HEAD too: uptime checks and `curl -I` use it, and it must match GET minus the body.
    @target.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist):
            # Vite fingerprints everything under assets/, so those files never change.
            cache = CACHE_FOREVER if path.startswith("assets/") else REVALIDATE
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": REVALIDATE})


if settings.frontend_dist:
    serve_frontend(app, Path(settings.frontend_dist))
