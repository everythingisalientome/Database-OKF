"""Opening a read-only connection from config.

Driver imports are lazy and per-engine: a PostgreSQL crawl in an OpenShift
job should not need a Teradata driver installed to start. Install what a run
needs through the extras — ``pip install -e .[postgres]`` and friends.

Passwords come from the environment, named by ``connection.password_env``.
A config file that carries a password is a config file that gets committed.
"""

from __future__ import annotations

import os

from .config import CrawlConfig
from .errors import AdapterError, ConfigError

#: Config keys handled here rather than passed to the driver.
META_KEYS = ("password_env",)


def _password(settings: dict) -> str | None:
    env_name = settings.get("password_env")
    if not env_name:
        return None
    try:
        return os.environ[env_name]
    except KeyError:
        raise ConfigError(
            f"connection.password_env names ${env_name}, which is not set"
        ) from None


def _driver_kwargs(settings: dict) -> dict:
    return {k: v for k, v in settings.items() if k not in META_KEYS}


def connect(config: CrawlConfig):
    """Open a connection for ``config``. The caller closes it."""
    settings = dict(config.connection)
    password = _password(settings)
    kwargs = _driver_kwargs(settings)

    if config.engine == "postgres":
        import psycopg  # noqa: PLC0415 — lazy on purpose

        return psycopg.connect(password=password, **kwargs)

    if config.engine == "sqlserver":
        import pymssql  # noqa: PLC0415

        return pymssql.connect(password=password, **kwargs)

    if config.engine == "teradata":
        import teradatasql  # noqa: PLC0415

        return teradatasql.connect(password=password, **kwargs)

    raise AdapterError(f"no connector for engine {config.engine!r}")
