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


def test_transfer_between_two_imported_accounts_is_merged(client, accounts):
    """Card charge classified as a top-up of a prepaid card that has its own statement: one transfer."""
    import json

    card = accounts["2051"]["id"]
    pre = client.post("/api/accounts", json={"code": "1215", "name": "Laundry card", "type": "asset"}).json()["id"]
    card_map = {"delimiter": ";", "date_column": "Date", "date_format": "%d.%m.%Y",
                "description_columns": ["Text"], "amount_column": "Amount", "invert_sign": True}
    pre_map = {"delimiter": ";", "date_column": "Date", "date_format": "%d.%m.%Y", "description_columns": ["Text"],
               "amount_mode": "debit_credit", "debit_column": "Debit", "credit_column": "Credit"}
    pre_csv = b"Date;Text;Debit;Credit\n02.03.2026;Recharge;;20.00\n06.03.2026;Recharge;;20.00\n04.03.2026;Wash;2.50;\n"
    card_csv = b"Date;Text;Amount\n01.03.2026;LAUNDRY TOPUP;20.00\n06.03.2026;LAUNDRY TOPUP;20.00\n"
    # the prepaid statement first: its top-ups are new entries waiting for a category
    r = client.post("/api/imports", files={"files": ("pre.csv", pre_csv)},
                    data={"account_id": pre, "mapping": json.dumps(pre_map)}).json()["results"][0]
    assert r["new"] == 3
    r = client.post("/api/imports", files={"files": ("card.csv", card_csv)},
                    data={"account_id": card, "mapping": json.dumps(card_map)}).json()["results"][0]
    assert r["new"] == 2

    # classifying the card charges as top-ups: the editor merges the 1st, the bulk move the 2nd
    topups = sorted(client.get("/api/transactions", params={"q": "TOPUP"}).json()["items"], key=lambda t: t["date"])
    t = client.get(f"/api/transactions/{topups[0]['id']}").json()
    body = {"date": t["date"], "description": t["description"], "counterparty": None, "notes": None, "confirm": True,
            "lines": [{"account_id": pre if ln["account_kind"] == "suspense" else ln["account_id"],
                       "amount": ln["amount"], "bank_entry_id": ln["bank_entry_id"]} for ln in t["lines"]]}
    t = client.put(f"/api/transactions/{t['id']}", json=body).json()
    assert all(ln["bank_entry_id"] for ln in t["lines"])  # both statement lines on one entry
    r = client.post("/api/transactions/bulk-account", json={"ids": [topups[1]["id"]], "account_id": pre}).json()
    assert r["merged"] == 1

    accs = {a["code"]: a for a in client.get("/api/accounts").json()}
    assert Decimal(accs["1215"]["balance"]) == Decimal("37.50")  # 2 x 20 in, 2.50 spent
    assert Decimal(accs["2051"]["balance"]) == Decimal("40.00")  # owed on the card
    assert len(client.get("/api/transactions", params={"account_id": pre}).json()["items"]) == 3
