from decimal import Decimal

import pytest
from sqlalchemy import text

from .conftest import DATA


def D(x):
    return Decimal(str(x))


def camt_files():
    return [("files", (p.name, p.read_bytes(), "application/xml")) for p in sorted(DATA.glob("*.xml"))]


def test_camt_import_unknown_iban_then_new_account(client, accounts):
    r = client.post("/api/imports", files=camt_files()).json()
    assert r["unknown_ibans"][0]["iban"] == "CH690027427423101840J"
    assert not [x for x in r["results"] if x["status"] == "ok"]

    r = client.post("/api/imports", files=camt_files(), data={"iban_map": '{"CH690027427423101840J": "new"}'}).json()
    ok = [x for x in r["results"] if x["status"] == "ok"]
    assert len(ok) == 8
    assert sum(x["new"] for x in ok) == 24
    assert ok[0]["opening_set"] is True

    accs = {a["code"]: a for a in client.get("/api/accounts").json()}
    bank = accs["1020"]  # the seeded, unused current account receives the IBAN
    assert bank["iban"] == "CH690027427423101840J"
    assert D(bank["balance"]) == D("4158.76")  # last CLBD
    assert D(bank["opening_balance"]) == D("4972.70")
    # counterpart of the net outflow sits on suspense until classified
    assert D(accs["1090"]["balance"]) == D("4972.70") - D("4158.76")

    batches = client.get("/api/imports").json()
    assert all(b["balance_ok"] for b in batches)

    # re-import: whole-file duplicates
    r = client.post("/api/imports", files=camt_files()).json()
    assert all(x["status"] == "duplicate" for x in r["results"])

    bs = client.get("/api/reports/balance-sheet", params={"at": "2026-01-31"}).json()
    assert bs["balanced"]
    assert client.get("/api/transactions/stats").json()["by_state"]["unclassified"] == 24


def test_manual_entry_is_matched_by_import(client, accounts):
    bank = accounts["1020"]["id"]
    client.patch(f"/api/accounts/{bank}", json={"iban": "CH69 0027 4274 2310 1840 J"})
    # entered by hand before the statement arrives: Infomaniak e-bill, split hosting + domain
    tx = client.post("/api/transactions", json={
        "date": "2026-01-10", "description": "Infomaniak hébergement", "lines": [
            {"account_id": bank, "amount": "-18.85"},
            {"account_id": accounts["4020"]["id"], "amount": "12.00", "memo": "hosting"},
            {"account_id": accounts["4630"]["id"], "amount": "6.85", "memo": "domain"},
        ]}).json()
    assert tx["reconciled"] is False
    r = client.post("/api/imports", files=camt_files()).json()
    assert sum(x.get("matched", 0) for x in r["results"]) == 1
    tx = client.get(f"/api/transactions/{tx['id']}").json()
    assert tx["reconciled"] is True and tx["state"] == "confirmed"
    assert tx["bank_entries"][0]["counterparty"] == "INFOMANIAK NETWORK SA"
    assert client.get("/api/transactions/stats").json()["by_state"]["unclassified"] == 23


def test_person_receivable_and_reimbursement(client, accounts):
    bank = accounts["1020"]["id"]
    client.patch(f"/api/accounts/{bank}", json={"iban": "CH690027427423101840J"})
    p = client.post("/api/accounts/persons", json={"name": "Alexandre Maquet"}).json()
    assert p["code"] == "1100.01" and p["type"] == "asset"
    # I paid a dinner of 100 for both: 50 mine, 50 advanced to Alexandre
    client.post("/api/transactions", json={"date": "2026-01-01", "description": "Dîner", "lines": [
        {"account_id": accounts["1000"]["id"], "amount": "-100"},
        {"account_id": accounts["4110"]["id"], "amount": "50"},
        {"account_id": p["id"], "amount": "50"}]})
    client.post("/api/imports", files=camt_files())
    twint = client.get("/api/transactions", params={"q": "MAQUET"}).json()["items"][0]
    assert twint["state"] == "auto"
    assert any(ln["account_id"] == p["id"] and D(ln["amount"]) == -50 for ln in twint["lines"])
    rec = {x["name"]: D(x["balance"]) for x in client.get("/api/reports/receivables").json()}
    assert rec["Alexandre Maquet"] == 0  # fully reimbursed


def test_review_edit_split_and_confirm(client, accounts):
    bank = accounts["1020"]["id"]
    client.patch(f"/api/accounts/{bank}", json={"iban": "CH690027427423101840J"})
    client.post("/api/imports", files=camt_files())
    tx = client.get("/api/transactions", params={"q": "Uber", "state": "unclassified"}).json()["items"][0]
    src = next(ln for ln in tx["lines"] if ln["bank_entry_id"])
    amt = -D(src["amount"])
    lines = [
        {"account_id": src["account_id"], "amount": src["amount"], "bank_entry_id": src["bank_entry_id"]},
        {"account_id": accounts["4120"]["id"], "amount": str(amt - D("10"))},
        {"account_id": accounts["4730"]["id"], "amount": "10"},
    ]
    body = {"date": tx["date"], "description": tx["description"], "lines": lines}
    r = client.put(f"/api/transactions/{tx['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "confirmed" and len(r.json()["lines"]) == 3
    # statement line amount is immutable
    bad = dict(body, lines=[dict(lines[0], amount="-1"), {"account_id": accounts["4120"]["id"], "amount": "1"}])
    assert client.put(f"/api/transactions/{tx['id']}", json=bad).status_code == 422
    # unbalanced
    bad = dict(body, lines=[lines[0], dict(lines[1], amount="1")])
    assert client.put(f"/api/transactions/{tx['id']}", json=bad).status_code == 422
    # cannot delete a statement-backed transaction
    assert client.delete(f"/api/transactions/{tx['id']}").status_code == 409


def test_rule_classifies_on_import(client, accounts):
    bank = accounts["1020"]["id"]
    client.patch(f"/api/accounts/{bank}", json={"iban": "CH690027427423101840J"})
    client.post("/api/rules", json={"pattern": "migros", "target_account_id": accounts["4100"]["id"], "direction": "out"})
    r = client.post("/api/imports", files=camt_files()).json()
    from app.importers import camt053
    n_migros = sum(1 for p in DATA.glob("*.xml") for st in camt053.parse(p.read_bytes())
                   for e in st.entries if "migros" in e.description.lower())
    assert sum(x.get("classified_by_rule", 0) for x in r["results"]) == n_migros
    inc = client.get("/api/reports/income-statement", params={"start": "2026-01-01", "end": "2026-01-31"}).json()
    groceries = next(x for x in inc["expenses"] if x["code"] == "4100")
    from app.importers import camt053
    expected = -sum(e.amount for p in DATA.glob("*.xml") for st in camt053.parse(p.read_bytes())
                    for e in st.entries if "migros" in e.description.lower())
    assert D(groceries["amount"]) == expected > 0


def test_db_trigger_rejects_unbalanced(client, accounts):
    from app.db import engine

    tx = client.post("/api/transactions", json={"date": "2026-01-01", "description": "x", "lines": [
        {"account_id": accounts["1000"]["id"], "amount": "-5"},
        {"account_id": accounts["4100"]["id"], "amount": "5"}]}).json()
    with pytest.raises(Exception, match="not balanced"):
        with engine.begin() as c:
            c.execute(text("UPDATE journal_lines SET amount = 6 WHERE transaction_id = :t AND amount > 0"), {"t": tx["id"]})


def test_credit_card_csv_and_settlement(client, accounts):
    card = accounts["2051"]["id"]
    bank = accounts["1020"]["id"]
    csv_data = (
        "Date;Description;Amount;Currency;Original amount;Original currency\n"
        "03.01.2026;COOP PRONTO LAUSANNE;45.20;CHF;;\n"
        "05.01.2026;AMAZON EU;30.00;CHF;31.50;EUR\n"
        "28.01.2026;PAYMENT THANK YOU;-75.20;CHF;;\n"
    ).encode()
    mapping = {"delimiter": ";", "date_column": "Date", "date_format": "%d.%m.%Y",
               "description_columns": ["Description"], "amount_column": "Amount", "invert_sign": True,
               "currency_column": "Currency", "orig_amount_column": "Original amount",
               "orig_currency_column": "Original currency"}
    import json
    prev = client.post("/api/imports/csv/preview", files={"file": ("card.csv", csv_data)},
                       data={"mapping": json.dumps(mapping)}).json()
    assert len(prev["parsed"]) == 3 and D(prev["parsed"][0]["amount"]) == D("-45.20")

    # bank payment to the card already booked (e.g. from camt) as a transfer bank -> card
    client.post("/api/transactions", json={"date": "2026-01-27", "description": "Paiement carte", "lines": [
        {"account_id": bank, "amount": "-75.20"}, {"account_id": card, "amount": "75.20"}]})
    r = client.post("/api/imports", files={"files": ("card.csv", csv_data)},
                    data={"account_id": card, "mapping": json.dumps(mapping)}).json()
    res = r["results"][0]
    assert res["status"] == "ok" and res["new"] == 3 and res["matched"] == 1
    accs = {a["code"]: a for a in client.get("/api/accounts").json()}
    assert D(accs["2051"]["balance"]) == 0  # 75.20 spent, 75.20 paid
    amazon = client.get("/api/transactions", params={"q": "AMAZON"}).json()["items"][0]
    line = next(ln for ln in amazon["lines"] if ln["bank_entry_id"])
    assert line["orig_currency"] == "EUR" and D(line["orig_amount"]) == D("-31.50")


def test_opening_balance_manual_account(client, accounts):
    pre = accounts["1200"]["id"]
    r = client.post(f"/api/accounts/{pre}/opening", json={"amount": "42.50", "date": "2025-12-31"})
    assert D(r.json()["balance"]) == D("42.50")
    r = client.post(f"/api/accounts/{pre}/opening", json={"amount": "40", "date": "2025-12-31"})
    assert D(r.json()["balance"]) == D("40")
    bs = client.get("/api/reports/balance-sheet").json()
    assert bs["balanced"]


def test_delete_import_batch(client, accounts):
    bank = accounts["1020"]["id"]
    client.patch(f"/api/accounts/{bank}", json={"iban": "CH690027427423101840J"})
    client.post("/api/imports", files=camt_files())
    b = client.get("/api/imports").json()[0]
    r = client.delete(f"/api/imports/{b['id']}").json()
    assert r["deleted_transactions"] == b["entries_new"]
    # re-import of that file works again
    r = client.post("/api/imports", files=camt_files()).json()
    assert len([x for x in r["results"] if x["status"] == "ok"]) == 1
