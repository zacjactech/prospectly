from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  register all models with Base.metadata
from alembic import context
from app.config import settings
from app.db import Base

# The config object itself
config = context.config

# Ensure the SQLite directory exists before any connection attempt.
settings.ensure_dirs()

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# URL comes from typed app settings (never from alembic.ini / secrets in git).
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

# Model metadata for autogenerate (empty until Phase 1 models land).
target_metadata = Base.metadata

# SQLite needs batch mode for most table alterations (DATABASE_DESIGN §5).
run_args = {
    "target_metadata": target_metadata,
    "render_as_batch": True,
    "compare_type": True,
}


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DB connection)."""
    context.configure(url=settings.database_url, literal_binds=True, **run_args)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = config.attributes.get("connection", None)
    if connectable is None:
        engine = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with engine.connect() as connection:
            _run(connection)
        engine.dispose()
    else:
        _run(connectable)


def _run(connection: object) -> None:
    context.configure(connection=connection, **run_args)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
