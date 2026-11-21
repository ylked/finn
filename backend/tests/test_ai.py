from decimal import Decimal

from .test_accounting import camt_files


def test_ai_classification_and_review(client, accounts, monkeypatch):
    from app import worker as w
    from app.ai import classify
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ai_base_url", "http://fake")
    monkeypatch.setattr(s, "ai_text_model", "fake-model")
    seen = []

    def fake_chat(model, messages):
        seen.append(messages)
        txt = messages[-1]["content"]
        if "UBER" in txt:
            return {"account_code": "4120", "confidence": 0.93, "reason": "Livraison Uber Eats"}
        return {"account_code": "9999", "confidence": 0.5, "reason": "?"}  # invalid -> error

    monkeypatch.setattr(classify, "chat_json", fake_chat)
    bank = accounts["1020"]["id"]
    client.patch(f"/api/accounts/{bank}", json={"iban": "CH690027427423101840J"})
    client.post("/api/imports", files=camt_files())
    from app.db import SessionLocal
    from app.models import Transaction
    from sqlalchemy import select

    with SessionLocal() as db:
        ids = db.scalars(select(Transaction.id).where(Transaction.ai_status == "pending")).all()
    for i in ids:
        w._classify_one(i)
    assert "Chart of accounts" in seen[0][-1]["content"]

    auto = client.get("/api/transactions", params={"state": "auto"}).json()
    assert auto["total"] >= 1
    assert all("Uber" in t["description"] or "UBER" in t["description"] for t in auto["items"])
    t = auto["items"][0]
    assert t["ai_confidence"] == 0.93 and any(ln["account_code"] == "4120" for ln in t["lines"])
    # confirm in bulk
    r = client.post("/api/transactions/confirm", json=[x["id"] for x in auto["items"]]).json()
    assert r["confirmed"] == auto["total"]
    # failed ones stay unclassified and keep a reason
    left = client.get("/api/transactions", params={"state": "unclassified"}).json()["items"][0]
    assert "AI error" in (left["ai_reason"] or "")


def test_receipt_flow(client, accounts, monkeypatch):
    from app import worker as w
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ai_base_url", "http://fake")
    monkeypatch.setattr(s, "ai_vision_model", "fake-vision")
    monkeypatch.setattr(w, "extract", lambda db, r: {
        "merchant": "Migros Dorigny", "date": "2026-01-07", "total": "28.10", "currency": "CHF",
        "splits": [{"account_code": "4100", "amount": "20.10", "label": "food"},
                   {"account_code": "4030", "amount": "8.00", "label": "detergent"}]})
    jpg = b"\xff\xd8\xff\xe0" + b"0" * 100
    # receipt uploaded BEFORE the bank statement is imported
    r = client.post("/api/receipts", files={"files": ("ticket.jpg", jpg, "image/jpeg")}).json()[0]
    assert r["status"] == "pending"
    w._receipt_one(r["id"])
    assert client.get(f"/api/receipts/{r['id']}").json()["status"] == "unmatched"

    bank = accounts["1020"]["id"]
    client.patch(f"/api/accounts/{bank}", json={"iban": "CH690027427423101840J"})
    client.post("/api/imports", files=camt_files())
    rr = client.get(f"/api/receipts/{r['id']}").json()
    assert rr["status"] == "matched"
    tx = client.get(f"/api/transactions/{rr['transaction_id']}").json()
    assert tx["state"] == "auto" and len(tx["lines"]) == 3
    assert {ln["account_code"] for ln in tx["lines"]} >= {"4100", "4030"}
    assert sum(Decimal(ln["amount"]) for ln in tx["lines"]) == 0
