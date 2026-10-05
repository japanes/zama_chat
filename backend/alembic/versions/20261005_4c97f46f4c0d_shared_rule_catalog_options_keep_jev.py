"""shared rule catalog options keep jev

The check of the catalog options the bot lists with a scenario question (`opt:<n>`,
scenarios/catalog.py) keeps Jev's answer below the threshold: the candidates are picked
by their own "fits" threshold, and the LLM re-check only added a model call to the turn.
The rule is added once; after that it is the admin's (admin → Decisions).

Revision ID: 4c97f46f4c0d
Revises: ad7afddb56d0
Create Date: 2026-10-05 22:04:17.384954
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '4c97f46f4c0d'
down_revision: str | Sequence[str] | None = 'ad7afddb56d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PATTERN = "opt:*"
NOTE = "Catalog options of a scenario question: Jev's answer is used as is, no LLM re-check"


def upgrade() -> None:
    # a shared rule with this pattern made in the admin stays as it is
    op.execute(
        sa.text(
            "INSERT INTO decision_rules (id, tenant_id, pattern, mode, threshold, below, note) "
            "VALUES (gen_random_uuid(), NULL, :pattern, 'jev', 0.7, 'keep', :note) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(pattern=PATTERN, note=NOTE)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM decision_rules "
            "WHERE tenant_id IS NULL AND pattern = :pattern AND note = :note"
        ).bindparams(pattern=PATTERN, note=NOTE)
    )
