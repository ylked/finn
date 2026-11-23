"""merchants

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ledger_id", sa.Integer, sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("domain", sa.String(253)),
        sa.Column("patterns", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("catalog_key", sa.String(50)),
        sa.Column("logo", sa.LargeBinary),
        sa.Column("logo_mime", sa.String(50)),
        sa.Column("logo_status", sa.String(10), nullable=False, server_default="none"),
        sa.Column("logo_checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ledger_id", "catalog_key", name="uq_merchant_catalog"),
    )
    op.create_index("ix_merchants_ledger_id", "merchants", ["ledger_id"])
    op.add_column("transactions", sa.Column("merchant_id", sa.Integer,
                                            sa.ForeignKey("merchants.id", ondelete="SET NULL")))
    op.add_column("transactions", sa.Column("merchant_locked", sa.Boolean, nullable=False, server_default="false"))
    op.create_index("ix_transactions_merchant_id", "transactions", ["merchant_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_merchant_id", "transactions")
    op.drop_column("transactions", "merchant_locked")
    op.drop_column("transactions", "merchant_id")
    op.drop_table("merchants")
