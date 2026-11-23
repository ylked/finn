import datetime as dt
import json
import re
from decimal import Decimal

from app.importers import camt053

from .test_accounting import DATA, camt_files


def _dec(r):
    return json.loads(r.text, parse_float=Decimal)


def _ref_payments():
    """Outgoing statement lines paid with a payment reference (QR / e-bill), from the sample statements."""
    out = []
    for p in sorted(DATA.glob("*.xml")):
        for st in camt053.parse(p.read_bytes()):
            for e in st.entries:
                m = re.search(r"Ref (\d{15,})", e.remittance_info or "")
                if e.amount < 0 and m:
                    out.append((e, m.group(1)))
    return out


def test_invoice_lifecycle_manual(client, accounts):
    a = {k: v["id"] for k, v in accounts.items()}
    assert accounts["2000"]["kind"] == "payable"

    # incomplete: draft, nothing booked
    inv = client.post("/api/invoices", json={"issuer": "Romande Energie", "due_date": "2026-03-31"}).json()
    assert inv["status"] == "draft" and inv["transaction_id"] is None

    # amount + category: booked as expense / payable on the invoice date
    inv = client.patch(f"/api/invoices/{inv['id']}", json={
        "amount": "120.50", "issue_date": "2026-03-01", "account_id": a["4010"], "number": "F-42"}).json()
    assert inv["status"] == "open" and Decimal(inv["remaining"]) == Decimal("120.50")
    booking = client.get(f"/api/transactions/{inv['transaction_id']}").json()
    assert booking["source"] == "invoice" and booking["date"] == "2026-03-01"
    assert booking["invoice"]["role"] == "booking"
    assert {ln["account_code"]: Decimal(ln["amount"]) for ln in booking["lines"]} == {
        "4010": Decimal("120.50"), "2000": Decimal("-120.50")}
    assert booking["merchant"]["name"] == "Romande Energie"
    s = client.get("/api/invoices/summary").json()
    assert s["open_count"] == 1

    # editing the amount re-books
    inv = client.patch(f"/api/invoices/{inv['id']}", json={"amount": "130"}).json()
    assert Decimal(inv["remaining"]) == 130

    # a payment entered by hand (unclassified) is proposed and linked
    pay = client.post("/api/transactions", json={"date": "2026-03-20", "description": "e-banking Romande Energie",
                                                 "lines": [{"account_id": a["1020"], "amount": "-130"},
                                                           {"account_id": a["1090"], "amount": "130"}]}).json()
    cands = client.get(f"/api/invoices/{inv['id']}/candidates").json()
    assert cands[0]["id"] == pay["id"]
    inv = client.post(f"/api/invoices/{inv['id']}/payments", json={"transaction_id": pay["id"]}).json()
    assert inv["status"] == "paid" and inv["paid_date"] == "2026-03-20" and Decimal(inv["remaining"]) == 0
    tx = client.get(f"/api/transactions/{pay['id']}").json()
    assert tx["invoice"]["role"] == "payment" and tx["state"] == "confirmed"
    assert {ln["account_code"] for ln in tx["lines"]} == {"1020", "2000"}

    # editing the payment (memo) keeps the link
    body = {"date": tx["date"], "description": "edited", "counterparty": None, "notes": None,
            "lines": [{"account_id": ln["account_id"], "amount": ln["amount"], "memo": "m"} for ln in tx["lines"]]}
    assert client.put(f"/api/transactions/{pay['id']}", json=body).status_code == 200
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"

    # unlink: open again, payment back to 'to classify'
    inv = client.delete(f"/api/invoices/{inv['id']}/payments/{pay['id']}").json()
    assert inv["status"] == "open"
    assert client.get(f"/api/transactions/{pay['id']}").json()["state"] == "unclassified"

    # mark as paid from the current account (two payments: partial then the rest)
    inv = client.post(f"/api/invoices/{inv['id']}/pay", json={"account_id": a["1030"], "date": "2026-03-25"}).json()
    assert inv["status"] == "paid" and len(inv["payments"]) == 1

    # deleting the booking entry turns the invoice back into a draft
    assert client.delete(f"/api/transactions/{inv['transaction_id']}").status_code == 200
    assert client.get("/api/invoices").json()[0]["status"] == "draft"

    # delete the invoice: its payment goes back to 'to classify'
    pay2 = inv["payments"][0]["transaction"]["id"]
    assert client.delete(f"/api/invoices/{inv['id']}").status_code == 200
    assert client.get(f"/api/transactions/{pay2}").json()["invoice"] is None
    assert client.get("/api/invoices").json() == []


def test_invoice_payment_recognised(client, accounts):
    pays = _ref_payments()
    assert len(pays) >= 2
    (e1, ref1), (e2, ref2) = pays[0], pays[-1]
    a = {k: v["id"] for k, v in accounts.items()}
    issue = lambda e: str(e.booking_date - dt.timedelta(days=20))  # noqa: E731
    # invoice recorded before the statement is imported: the payment is linked on import (QR reference)
    spaced = " ".join(ref1[i:i + 5] for i in range(0, len(ref1), 5))
    inv1 = client.post("/api/invoices", json={"issuer": "Somebody", "amount": str(-e1.amount), "reference": spaced,
                                              "issue_date": issue(e1), "account_id": a["4300"]}).json()
    client.post("/api/imports", files=camt_files(), data={"iban_map": '{"CH690027427423101840J": "new"}'})
    inv1 = client.get(f"/api/invoices/{inv1['id']}").json()
    assert inv1["status"] == "paid", inv1
    p = inv1["payments"][0]["transaction"]
    assert p["state"] == "confirmed" and p["reconciled"]

    # invoice recorded after the import: the already imported payment is found
    inv2 = client.post("/api/invoices", json={"issuer": "Other", "amount": str(-e2.amount), "reference": ref2,
                                              "issue_date": issue(e2), "account_id": a["4300"]}).json()
    if e2.remittance_info != e1.remittance_info:
        assert inv2["status"] == "paid", inv2


def test_receipt_kinds_counts_and_hidden_stats(client, accounts):
    a = {k: v["id"] for k, v in accounts.items()}
    tx = client.post("/api/transactions", json={"date": "2026-02-01", "description": "Loyer", "lines": [
        {"account_id": a["1020"], "amount": "-1500"}, {"account_id": a["4000"], "amount": "1500"}]}).json()
    small = client.post("/api/transactions", json={"date": "2026-02-02", "description": "Coop", "lines": [
        {"account_id": a["1020"], "amount": "-20"}, {"account_id": a["4100"], "amount": "20"}]}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    # several documents on one entry: the invoice and the proof of payment
    client.post("/api/receipts", files={"files": ("inv.png", png, "image/png")},
                data={"transaction_id": str(tx["id"]), "kind": "invoice"})
    client.post("/api/receipts", files={"files": ("paid.png", png, "image/png")},
                data={"transaction_id": str(tx["id"]), "kind": "payment"})
    docs = client.get(f"/api/transactions/{tx['id']}").json()["receipts"]
    assert sorted(d["kind"] for d in docs) == ["invoice", "payment"]
    assert client.delete(f"/api/receipts/{docs[0]['id']}").status_code == 200
    assert len(client.get(f"/api/transactions/{tx['id']}").json()["receipts"]) == 1
    client.post("/api/receipts", files={"files": ("x.png", png, "image/png")})
    assert client.get("/api/receipts/counts").json() == {"matched": 1, "manual": 1}
    assert len(client.get("/api/receipts", params={"status": "manual"}).json()) == 1

    span = {"start": "2026-02-01", "end": "2026-02-28"}
    ins = _dec(client.get("/api/insights", params=span))
    assert ins["largest"][0]["id"] == tx["id"]
    # hide one entry: gone from the lists, still in the totals
    client.post("/api/transactions/stats-hidden", json={"ids": [tx["id"]], "hidden": True})
    ins2 = _dec(client.get("/api/insights", params=span))
    assert [x["id"] for x in ins2["largest"]] == [small["id"]]
    assert ins2["totals"]["expenses"] == ins["totals"]["expenses"] == Decimal("1520")
    assert ins2["hidden"]["count"] == 1 and ins2["hidden"]["amount"] == 1500
    # or a whole category
    client.post("/api/transactions/stats-hidden", json={"ids": [tx["id"]], "hidden": False})
    client.post("/api/insights/hidden-accounts", json={"account_id": a["4000"], "hidden": True})
    ins3 = _dec(client.get("/api/insights", params=span))
    assert [x["id"] for x in ins3["largest"]] == [small["id"]]
    assert [x["code"] for x in ins3["hidden"]["accounts"]] == ["4000"]


def test_balance_history_and_forecast(client, accounts):
    a = {k: v["id"] for k, v in accounts.items()}
    today = dt.date.today()
    for i in range(1, 8):  # salary in, rent out, every month
        d = (today.replace(day=1) - dt.timedelta(days=30 * i)).replace(day=5)
        client.post("/api/transactions", json={"date": str(d), "description": "Salaire", "lines": [
            {"account_id": a["1020"], "amount": "5000"}, {"account_id": a["3000"], "amount": "-5000"}]})
        client.post("/api/transactions", json={"date": str(d), "description": "Loyer", "lines": [
            {"account_id": a["1020"], "amount": "-1500"}, {"account_id": a["4000"], "amount": "1500"}]})
    client.post("/api/invoices", json={"issuer": "Dentist", "amount": "400", "issue_date": str(today),
                                       "due_date": str(today + dt.timedelta(days=20)), "account_id": a["4310"]})
    b = _dec(client.get("/api/insights/balance"))
    assert b["closing"] == 7 * 3500 and b["points"][-1]["balance"] == b["closing"]
    f = b["forecast"]
    assert f["monthly_trend"] == 3500 and f["open_invoices"] == 400
    assert f["points"][0]["balance"] == 7 * 3500
    assert f["points"][1]["balance"] < f["points"][-1]["balance"]
    nw = _dec(client.get("/api/insights/balance", params={"scope": "net_worth"}))
    assert nw["closing"] == 7 * 3500 - 400  # the invoice is a debt already
    assert client.get("/api/insights/balance", params={"scope": "nope"}).status_code == 422
    home = _dec(client.get("/api/reports/dashboard"))
    assert home["invoices"]["open_count"] == 1 and home["liquid"] == 7 * 3500


def test_invoice_upload_read_by_ai(client, accounts, monkeypatch):
    from app import worker as w
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ai_base_url", "http://fake")
    monkeypatch.setattr(s, "ai_vision_model", "fake-vision")
    monkeypatch.setattr(w, "extract_invoice", lambda db, r: {
        "issuer": "Swisscom", "number": "123", "issue_date": "2026-04-01", "due_date": "2026-04-30",
        "total": "79.90", "currency": "CHF", "iban": "CH4431999123000889012", "reference": "210000000003139471430009017",
        "website": "swisscom.ch", "account_code": "4020", "description": "Mobile", "confidence": 0.9})
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    inv = client.post("/api/invoices/upload", files={"files": ("bill.png", png, "image/png")}).json()[0]
    assert inv["status"] == "draft" and inv["reading"]
    # not in the receipts list: it lives with its invoice
    assert client.get("/api/receipts").json() == []
    w._receipt_one(inv["documents"][0]["id"])
    inv = client.get(f"/api/invoices/{inv['id']}").json()
    assert inv["status"] == "open" and inv["due_date"] == "2026-04-30" and inv["account"]["code"] == "4020"
    assert inv["merchant"]["name"] == "Swisscom" and not inv["reading"]
    tx = client.get(f"/api/transactions/{inv['transaction_id']}").json()
    assert tx["state"] == "auto"  # booked by the AI: to review
    # a proof of payment can be added
    client.post(f"/api/invoices/{inv['id']}/documents", files={"files": ("p.png", png, "image/png")},
                data={"kind": "payment"})
    assert [d["kind"] for d in client.get(f"/api/invoices/{inv['id']}").json()["documents"]] == ["invoice", "payment"]
    # the user corrects it: confirmed
    client.patch(f"/api/invoices/{inv['id']}", json={"amount": "80.00"})
    assert client.get(f"/api/transactions/{inv['transaction_id']}").json()["state"] == "confirmed"
