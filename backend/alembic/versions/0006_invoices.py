"""invoices (bills payable), document kinds, entries hidden from insights lists

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # account kind 'payable': where invoices wait until they are paid (default chart: 2000 Factures à payer)
    op.drop_constraint("ck_account_kind", "accounts", type_="check")
    op.create_check_constraint(
        "ck_account_kind", "accounts",
        "kind in ('bank','credit_card','cash','receivable','payable','equity','suspense','other')")
    op.execute("UPDATE accounts SET kind = 'payable' WHERE code = '2000' AND type = 'liability' AND kind = 'other' "
               "AND NOT is_group")

    op.drop_constraint("ck_tx_source", "transactions", type_="check")
    op.create_check_constraint("ck_tx_source", "transactions",
                               "source in ('bank','csv','manual','receipt','opening','invoice')")
    op.add_column("transactions", sa.Column("stats_hidden", sa.Boolean(), nullable=False, server_default="false"))

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ledger_id", sa.Integer(), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("issuer", sa.String(255)),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id", ondelete="SET NULL")),
        sa.Column("number", sa.String(100)),
        sa.Column("reference", sa.String(64)),
        sa.Column("iban", sa.String(34)),
        sa.Column("issue_date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CHF"),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text()),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="SET NULL"),
                  index=True),
        sa.Column("paid_date", sa.Date()),
        sa.Column("error", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("status in ('draft','open','paid')", name="ck_invoice_status"),
    )
    op.create_index("ix_invoice_ledger_status", "invoices", ["ledger_id", "status", "due_date"])

    # lines on the payable account that book or settle an invoice
    op.add_column("journal_lines", sa.Column("invoice_id", sa.Integer(),
                                             sa.ForeignKey("invoices.id", ondelete="SET NULL"), index=True))

    op.add_column("receipts", sa.Column("kind", sa.String(16), nullable=False, server_default="receipt"))
    op.create_check_constraint("ck_receipt_kind", "receipts", "kind in ('receipt','invoice','payment','other')")
    op.add_column("receipts", sa.Column("invoice_id", sa.Integer(),
                                        sa.ForeignKey("invoices.id", ondelete="SET NULL"), index=True))


def downgrade() -> None:
    op.drop_column("receipts", "invoice_id")
    op.drop_constraint("ck_receipt_kind", "receipts", type_="check")
    op.drop_column("receipts", "kind")
    op.drop_column("journal_lines", "invoice_id")
    op.drop_table("invoices")
    op.drop_column("transactions", "stats_hidden")
    op.execute("DELETE FROM transactions WHERE source = 'invoice'")
    op.drop_constraint("ck_tx_source", "transactions", type_="check")
    op.create_check_constraint("ck_tx_source", "transactions",
                               "source in ('bank','csv','manual','receipt','opening')")
    op.execute("UPDATE accounts SET kind = 'other' WHERE kind = 'payable'")
    op.drop_constraint("ck_account_kind", "accounts", type_="check")
    op.create_check_constraint(
        "ck_account_kind", "accounts",
        "kind in ('bank','credit_card','cash','receivable','equity','suspense','other')")
