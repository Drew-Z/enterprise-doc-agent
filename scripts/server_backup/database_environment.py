"""Reuse repository libpq environment normalization.

Source SHA256: c4110d0aa77bfa49f50c4bb97f0fa8479367fb5d8161c51d341563a8fb48811d.
"""

import os
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

POSTGRES_CONNECTION_ENV_KEYS = frozenset(
    {
        "PGAPPNAME",
        "PGDATABASE",
        "PGHOST",
        "PGPASSFILE",
        "PGPASSWORD",
        "PGPORT",
        "PGSERVICE",
        "PGSSLCERT",
        "PGSSLKEY",
        "PGSSLROOTCERT",
        "PGSSLMODE",
        "PGUSER",
    }
)


def normalize_postgres_url(value: str) -> str:
    parsed = urlsplit(value)
    scheme = parsed.scheme.split("+", maxsplit=1)[0]
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError("database URL must use PostgreSQL")
    return urlunsplit(("postgresql", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def postgres_process_environment(database_url: str) -> dict[str, str]:
    """Build a libpq environment without placing credentials in argv."""
    parsed = urlsplit(normalize_postgres_url(database_url))
    environment = os.environ.copy()
    for key in POSTGRES_CONNECTION_ENV_KEYS:
        environment.pop(key, None)
    if parsed.hostname:
        environment["PGHOST"] = parsed.hostname
    if parsed.port is not None:
        environment["PGPORT"] = str(parsed.port)
    if parsed.username:
        environment["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)
    database = parsed.path.lstrip("/")
    if database:
        environment["PGDATABASE"] = unquote(database)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query_env = {
        "sslmode": "PGSSLMODE",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "sslrootcert": "PGSSLROOTCERT",
        "application_name": "PGAPPNAME",
    }
    for query_name, env_name in query_env.items():
        values = query.get(query_name)
        if values and values[-1]:
            environment[env_name] = unquote(values[-1])
    return environment
