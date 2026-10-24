"""scenario runs dialogue

A scenario with `background_answers` writes the answers of its text fields in the
background (job `scenario_answers`): the turn only marks which questions the client's words
answer, and the job reads those words from the run's own copy of the dialogue, as the client
wrote it — `dialogue` = [{"turn", "bot", "client", "rewritten"?, "messages", "at"}].

Revision ID: a3d9b6e1c2f4
Revises: cf620fd59f81
Create Date: 2026-10-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = 'a3d9b6e1c2f4'
down_revision: str | Sequence[str] | None = 'cf620fd59f81'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'scenario_runs',
        sa.Column(
            'dialogue',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('scenario_runs', 'dialogue')
