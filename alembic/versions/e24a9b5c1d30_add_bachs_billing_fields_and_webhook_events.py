"""add Bachs billing fields and webhook idempotency

Revision ID: e24a9b5c1d30
Revises: c7e7b3a4d912
Create Date: 2026-10-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e24a9b5c1d30"
down_revision: Union[str, Sequence[str], None] = "c7e7b3a4d912"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bachs_customer_id", sa.String(), nullable=True))
    op.add_column("users", sa.Column("bachs_subscription_id", sa.String(), nullable=True))
    op.create_unique_constraint("uq_users_bachs_customer_id", "users", ["bachs_customer_id"])
    op.create_unique_constraint("uq_users_bachs_subscription_id", "users", ["bachs_subscription_id"])
    op.create_table(
        "bachs_webhook_events",
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("bachs_webhook_events")
    op.drop_constraint("uq_users_bachs_subscription_id", "users", type_="unique")
    op.drop_constraint("uq_users_bachs_customer_id", "users", type_="unique")
    op.drop_column("users", "bachs_subscription_id")
    op.drop_column("users", "bachs_customer_id")
