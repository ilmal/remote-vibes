"""Add dev_server_port to agent_sessions

Revision ID: 0002_add_dev_server_port
Revises: 0001_initial
Create Date: 2026-02-26 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_dev_server_port"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("dev_server_port", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "dev_server_port")
