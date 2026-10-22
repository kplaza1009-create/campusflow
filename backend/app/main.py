"""FastAPI application: routers, CORS, the Origin check, and the health endpoint."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.deps import UNSAFE_METHODS
from app.routers import account, assignments, auth, courses

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("campusflow.http")

app = FastAPI(title="CampusFlow API", version="0.1.0")


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


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
