"""merge heads: contact_person fix + phase2/company_profile branch

Revision ID: f1a2b3c4d5e6
Revises: 9d4f7b2e1c6a, 3e5f7a9c1b2d
Create Date: 2026-07-05 11:00:00.000000

Two divergent migration branches formed because CP's Phase 2 work
(0d8e2c5f4a7b, create_order_sheet_master) was chained from an EARLIER
point in history (9e2f5b8c1d3a) instead of the actual latest head at
the time (6c1e4f8a2d5b) — most likely because CP's context/knowledge
of the migration history was incomplete or based on a stale snapshot.

This is a pure merge point — it makes no schema changes itself, it
only unifies the two heads so `flask db upgrade` can proceed linearly
again.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = ('9d4f7b2e1c6a', '3e5f7a9c1b2d')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
