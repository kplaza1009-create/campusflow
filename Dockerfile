# CampusFlow in one container: the API serves the built frontend, so the browser sees a
# single origin (first-party cookies, no CORS). Used by Render and by CI's smoke test.

# --- 1. Build the frontend --------------------------------------------------------
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. The API, plus the built files ---------------------------------------------
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH \
    CAMPUSFLOW_FRONTEND_DIST=/app/static
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev
COPY backend/ ./
COPY --from=web /web/dist ./static
RUN useradd --system --uid 10001 campusflow
USER campusflow
EXPOSE 8000
# Migrate, make sure the demo exists, then serve. --proxy-headers trusts the host's
# X-Forwarded-Proto (so the app knows it's on HTTPS); client IPs for rate limits come
# from CAMPUSFLOW_TRUSTED_PROXY_HOPS instead, which can't be spoofed.
CMD alembic upgrade head \
 && python -m app.demo --if-missing \
 && exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
