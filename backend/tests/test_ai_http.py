"""Exercise the real openai SDK against a local mock OpenAI-compatible server."""

import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .test_accounting import camt_files

calls: list[dict] = []
mode = {"reject_format": False}


def _reply(content: str):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}


mock = FastAPI()


@mock.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    calls.append(body)
    if mode["reject_format"] and "response_format" in body:
        return JSONResponse({"error": {"message": "response_format is not supported"}}, status_code=400)
    content = body["messages"][-1]["content"]
    if isinstance(content, list):  # vision
        return _reply('```json\n{"merchant":"Migros","date":"2026-01-08","total":13.70,"currency":"CHF",'
                      '"splits":[{"account_code":"4100","amount":13.70}]}\n```')
    return _reply(json.dumps({"account_code": "4120", "confidence": 0.8, "reason": "ok", "counterparty": "Uber Eats"}))


@pytest.fixture(scope="module")
def server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    srv.should_exit = True


@pytest.fixture()
def ai(server, monkeypatch):
    from app.ai import client as c
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ai_base_url", server)
    monkeypatch.setattr(s, "ai_api_key", "k")
    monkeypatch.setattr(s, "ai_text_model", "text-m")
    monkeypatch.setattr(s, "ai_vision_model", "vision-m")
    monkeypatch.setattr(c, "_client", None)
    calls.clear()
    yield
    monkeypatch.setattr(c, "_client", None)


@pytest.mark.parametrize("reject", [False, True])
def test_classify_over_http(client, accounts, ai, reject):
    from app import worker as w
    from app.db import SessionLocal
    from app.models import Transaction
    from sqlalchemy import select

    mode["reject_format"] = reject
    client.patch(f"/api/accounts/{accounts['1020']['id']}", json={"iban": "CH690027427423101840J"})
    client.post("/api/imports", files=camt_files())
    with SessionLocal() as db:
        tx_id = db.scalars(select(Transaction.id).where(Transaction.ai_status == "pending")).first()
    w._classify_one(tx_id)
    tx = client.get(f"/api/transactions/{tx_id}").json()
    assert tx["state"] == "auto", tx["ai_reason"]
    assert any(ln["account_code"] == "4120" for ln in tx["lines"])
    assert calls[0]["model"] == "text-m"
    assert ("response_format" in calls[-1]) is (not reject)
    assert "Dekhli Nima" in calls[0]["messages"][0]["content"]  # account owner passed to the model
    mode["reject_format"] = False


def test_receipt_vision_over_http(client, accounts, ai):
    from app import worker as w

    client.patch(f"/api/accounts/{accounts['1020']['id']}", json={"iban": "CH690027427423101840J"})
    client.post("/api/imports", files=camt_files())
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(buf, "PNG")
    r = client.post("/api/receipts", files={"files": ("r.png", buf.getvalue(), "image/png")}).json()[0]
    w._receipt_one(r["id"])
    rr = client.get(f"/api/receipts/{r['id']}").json()
    assert rr["status"] == "matched", rr
    img = calls[-1]["messages"][0]["content"][1]["image_url"]["url"]
    assert calls[-1]["model"] == "vision-m" and img.startswith("data:image/jpeg;base64,")
