"""partial unique index for active outreach messages (QA D1)

DATABASE_DESIGN §2.9 specifies one *active* message per prospect via a
partial unique index, but phase 2 created a plain unique index on
``prospect_id`` — so a prospect could never have a second message version:
the deactivate-then-insert in ``create_draft`` still violated the index and
every second draft / "start new outreach" crashed with IntegrityError.

Revision ID: 3b7e5d9a1c42
Revises: 9d4f8a1c2e7b
Create Date: 2026-11-10 14:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3b7e5d9a1c42'
down_revision: str | None = '9d4f8a1c2e7b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX ux_outreach_messages_active")
    op.execute(
        "CREATE UNIQUE INDEX ux_outreach_messages_active "
        "ON outreach_messages (prospect_id) WHERE active = 1"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_outreach_messages_active")
    op.execute(
        "CREATE UNIQUE INDEX ux_outreach_messages_active "
        "ON outreach_messages (prospect_id)"
    )
