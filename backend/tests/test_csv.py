from decimal import Decimal

from app.importers.csv_import import CsvMapping, parse

M = CsvMapping(date_column="Date", date_format="%d.%m.%Y", amount_mode="debit_credit",
               debit_column="Debit", credit_column="Credit", description_columns=["Text"])


def amounts(csv: str) -> list[Decimal]:
    entries, errors = parse(csv.encode(), M, "CHF")
    assert not errors
    return [e.amount for e in entries]


def test_refund_in_debit_column_is_money_in():
    csv = "Date;Text;Debit;Credit\n01.10.2026;Wash;2.45;\n02.10.2026;Dry;6.75;\n15.10.2026;Refund;-6.75;\n16.10.2026;Top-up;;20.00\n"
    assert amounts(csv) == [Decimal("-2.45"), Decimal("-6.75"), Decimal("6.75"), Decimal("20.00")]


def test_bank_writing_debits_as_negative_numbers():
    csv = "Date;Text;Debit;Credit\n01.10.2026;Shop;-12.00;\n02.10.2026;Shop;-3.50;\n03.10.2026;Salary;;100.00\n"
    assert amounts(csv) == [Decimal("-12.00"), Decimal("-3.50"), Decimal("100.00")]
