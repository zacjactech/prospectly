"""usage period columns to DATE (spec: DATABASE_DESIGN §2.13)

The model declared ``period: Mapped[dt.date]`` but the column was created as
DATETIME, so a ``date`` bound value did not match stored rows — the daily
upsert in ``_record_provider_usage`` missed the existing row and hit the
unique index (IntegrityError on the second call of a day). Revisions store
DATE to match the spec.

Uses an explicit move-and-copy instead of ``batch_alter_table``: alembic's
batch transfer casts across type affinities, and SQLite renders
``CAST('YYYY-MM-DD' AS DATE)`` as the integer year (e.g. 2026), corrupting
the data. substr() copies keep the date text verbatim.

Revision ID: 9d4f8a1c2e7b
Revises: 6f7e2a9b1c3d
Create Date: 2026-11-10 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d4f8a1c2e7b"
down_revision: str | None = "6f7e2a9b1c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- provider_usage ---
    op.execute("""
        CREATE TABLE provider_usage_new (
            id INTEGER NOT NULL,
            provider VARCHAR(40) NOT NULL,
            kind VARCHAR(30) NOT NULL,
            calls INTEGER NOT NULL,
            errors INTEGER NOT NULL,
            rate_limited INTEGER NOT NULL,
            period DATE NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        INSERT INTO provider_usage_new
            (id, provider, kind, calls, errors, rate_limited, period, updated_at)
        SELECT id, provider, kind, calls, errors, rate_limited,
               substr(period, 1, 10), updated_at
        FROM provider_usage
    """)
    op.execute("DROP TABLE provider_usage")
    op.execute("ALTER TABLE provider_usage_new RENAME TO provider_usage")
    op.execute("CREATE INDEX ix_provider_usage_provider ON provider_usage (provider)")
    op.execute("CREATE UNIQUE INDEX ux_provider_usage ON provider_usage (provider, kind, period)")

    # --- ai_usage ---
    op.execute("""
        CREATE TABLE ai_usage_new (
            id INTEGER NOT NULL,
            provider VARCHAR(40) NOT NULL,
            model VARCHAR(100),
            task VARCHAR(30) NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            calls INTEGER NOT NULL,
            ok_calls INTEGER NOT NULL,
            failed_calls INTEGER NOT NULL,
            period DATE NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        INSERT INTO ai_usage_new
            (id, provider, model, task, prompt_tokens, completion_tokens,
             calls, ok_calls, failed_calls, period, updated_at)
        SELECT id, provider, model, task, prompt_tokens, completion_tokens,
               calls, ok_calls, failed_calls, substr(period, 1, 10), updated_at
        FROM ai_usage
    """)
    op.execute("DROP TABLE ai_usage")
    op.execute("ALTER TABLE ai_usage_new RENAME TO ai_usage")
    op.execute("CREATE UNIQUE INDEX ux_ai_usage ON ai_usage (provider, model, task, period)")


def downgrade() -> None:
    # Back to DATETIME: date-only text ('YYYY-MM-DD') is valid to read back
    # as a DateTime-typed column (SQLite stores it as TEXT).
    op.execute("""
        CREATE TABLE provider_usage_old (
            id INTEGER NOT NULL,
            provider VARCHAR(40) NOT NULL,
            kind VARCHAR(30) NOT NULL,
            calls INTEGER NOT NULL,
            errors INTEGER NOT NULL,
            rate_limited INTEGER NOT NULL,
            period DATETIME NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        INSERT INTO provider_usage_old
            (id, provider, kind, calls, errors, rate_limited, period, updated_at)
        SELECT id, provider, kind, calls, errors, rate_limited, period, updated_at
        FROM provider_usage
    """)
    op.execute("DROP TABLE provider_usage")
    op.execute("ALTER TABLE provider_usage_old RENAME TO provider_usage")
    op.execute("CREATE INDEX ix_provider_usage_provider ON provider_usage (provider)")
    op.execute("CREATE UNIQUE INDEX ux_provider_usage ON provider_usage (provider, kind, period)")

    op.execute("""
        CREATE TABLE ai_usage_old (
            id INTEGER NOT NULL,
            provider VARCHAR(40) NOT NULL,
            model VARCHAR(100),
            task VARCHAR(30) NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            calls INTEGER NOT NULL,
            ok_calls INTEGER NOT NULL,
            failed_calls INTEGER NOT NULL,
            period DATETIME NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        INSERT INTO ai_usage_old
            (id, provider, model, task, prompt_tokens, completion_tokens,
             calls, ok_calls, failed_calls, period, updated_at)
        SELECT id, provider, model, task, prompt_tokens, completion_tokens,
               calls, ok_calls, failed_calls, period, updated_at
        FROM ai_usage
    """)
    op.execute("DROP TABLE ai_usage")
    op.execute("ALTER TABLE ai_usage_old RENAME TO ai_usage")
    op.execute("CREATE UNIQUE INDEX ux_ai_usage ON ai_usage (provider, model, task, period)")
