"""auth accounts + sessions (full accounts + roles, owner decision 2026-11-18)

SECURITY_AND_PRIVACY §3 amendment: the access-token gate becomes a *switch*
(the token is no longer the credential); credentials live in ``users``
(scrypt-hashed passwords, role, optional disabled state, password-reset
token hash). Sessions move from the stateless HMAC cookie to revocable rows
in ``user_sessions`` (logout-all, disable-user and password-reset all kill
sessions server-side).

``prospects.owner_id`` was a Phase-6 placeholder typed Text with every value
NULL — converted to Integer FK semantics (still nullable, no backfill needed)
so ownership scoping can reference ``users.id``.

Revision ID: d8f21c9a7e3b
Revises: 3b7e5d9a1c42
Create Date: 2026-11-18 17:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8f21c9a7e3b'
down_revision: str | None = '3b7e5d9a1c42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default=sa.text("'member'")),
        sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.text("'0'")),
        sa.Column('reset_token_hash', sa.Text(), nullable=True),
        sa.Column('reset_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_user_sessions_token'), 'user_sessions', ['token'], unique=True)
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)

    # Phase-6 placeholder: Text, always NULL so far — retype to Integer so it
    # can reference users.id (ownership scoping).
    with op.batch_alter_table('prospects', schema=None) as batch_op:
        batch_op.alter_column(
            'owner_id',
            existing_type=sa.Text(),
            type_=sa.Integer(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table('prospects', schema=None) as batch_op:
        batch_op.alter_column(
            'owner_id',
            existing_type=sa.Integer(),
            type_=sa.Text(),
            existing_nullable=True,
        )
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_index(op.f('ix_user_sessions_token'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')
