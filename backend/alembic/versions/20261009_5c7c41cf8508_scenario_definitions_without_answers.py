"""scenario definitions without answers

A scenario could say «an answer belongs only to the question asked» (`answers: asked`): a
client's message was then checked against the last question only, and what the client said
about the other questions in the same message was asked again. That setting is removed —
every message is checked against all open questions — and the definitions saved with the
`answers` key lose it: the scenario format takes no unknown keys.

Revision ID: 5c7c41cf8508
Revises: 4c97f46f4c0d
Create Date: 2026-10-09 20:10:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = '5c7c41cf8508'
down_revision: str | Sequence[str] | None = '4c97f46f4c0d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE scenarios SET definition = definition - 'answers' "
        "WHERE jsonb_exists(definition, 'answers')"
    )


def downgrade() -> None:
    # which scenarios had «asked» is not kept; without the key the older code takes «any»
    pass
