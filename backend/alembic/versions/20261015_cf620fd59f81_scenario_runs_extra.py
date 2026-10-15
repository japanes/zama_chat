"""scenario runs extra

A run of a scenario with `completion: by_caller` gets its own fields while it goes
(POST /v1/runs/{id}/fields), and a `choice` field with `options_from: caller` gets its
options with POST /v1/runs/start. Both live in the run: `extra` = {"fields": [field
definitions + added_at], "options": {field: [{key, label}]}}. The run's fields are the
scenario's plus these (scenarios/store.effective_definition).

Revision ID: cf620fd59f81
Revises: 5c7c41cf8508
Create Date: 2026-10-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = 'cf620fd59f81'
down_revision: str | Sequence[str] | None = '5c7c41cf8508'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'scenario_runs',
        sa.Column(
            'extra',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('scenario_runs', 'extra')
