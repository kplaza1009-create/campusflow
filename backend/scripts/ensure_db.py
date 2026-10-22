"""Create the database named in CAMPUSFLOW_DATABASE_URL if it doesn't exist yet.

Usage (from backend/):  uv run python -m scripts.ensure_db && uv run alembic upgrade head

Connects to the server's "postgres" maintenance database with the same
credentials, so the role needs CREATEDB. Never drops or changes existing data.
"""

from sqlalchemy import create_engine, make_url, text

from app.config import get_settings


def main() -> None:
    url = make_url(get_settings().database_url)
    name = url.database
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
        if exists:
            print(f"Database {name!r} already exists.")
        else:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            print(f"Created database {name!r}.")
    admin.dispose()


if __name__ == "__main__":
    main()
