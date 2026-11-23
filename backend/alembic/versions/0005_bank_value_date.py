"""entries imported from statements are dated with the value date (new default of the bank_date setting)

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

REDATE = """
UPDATE transactions t SET date = sub.d, updated_at = now()
FROM (
    SELECT jl.transaction_id, min({expr}) AS d
    FROM journal_lines jl JOIN bank_entries be ON be.id = jl.bank_entry_id
    GROUP BY jl.transaction_id
) sub, ledgers l
WHERE t.id = sub.transaction_id AND l.id = t.ledger_id AND t.source IN ('bank', 'csv') AND t.date <> sub.d
  AND coalesce(l.settings::jsonb ->> 'bank_date', 'value') = '{mode}'
"""


def upgrade() -> None:
    op.execute(REDATE.format(expr="coalesce(be.value_date, be.booking_date)", mode="value"))


def downgrade() -> None:
    op.execute(REDATE.format(expr="be.booking_date", mode="value"))
