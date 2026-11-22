"""MCP endpoint: token auth, tool listing, read and write tools over streamable HTTP (JSON responses)."""

import json
from decimal import Decimal

H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, token, method, params=None, id_=1):
    headers = {**H, "Authorization": f"Bearer {token}"} if token else H
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, headers=headers)
    return r


def init(client, token):
    r = rpc(client, token, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(client, token, name, args=None):
    r = rpc(client, token, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def text_of(res):
    return res["content"][0]["text"]


def data(res):
    """Tool result payload: structured content when the tool declares a schema, else the JSON text."""
    assert not res.get("isError"), res
    sc = res.get("structuredContent")
    if sc is not None:
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(text_of(res))


def make_token(client, write):
    r = client.post("/api/tokens", json={"name": "claude", "can_write": write})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_mcp_requires_token(client):
    r = rpc(client, None, "tools/list")
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")
    assert rpc(client, "finn_wrong", "tools/list").status_code == 401
    meta = client.get("/.well-known/oauth-protected-resource/mcp")
    assert meta.status_code == 200 and meta.json()["resource"].endswith("/mcp")


def test_mcp_read_and_write(client, accounts):
    ro = make_token(client, write=False)
    rw = make_token(client, write=True)
    toks = client.get("/api/tokens").json()
    assert len(toks) == 2 and all("token" not in t for t in toks)

    info = init(client, ro)
    assert info["serverInfo"]["name"] == "finn"
    tools = {t["name"] for t in rpc(client, ro, "tools/list").json()["result"]["tools"]}
    assert {"ledger_overview", "search_transactions", "create_transaction", "classify_transaction"} <= tools

    accs = data(call(client, ro, "list_accounts"))
    assert any(a["code"] == "1020" and a["kind"] == "bank" for a in accs)

    # read-only token cannot write
    res = call(client, ro, "create_transaction", {
        "date": "2026-01-05", "description": "x",
        "lines": [{"account_code": "4000", "amount": 10}, {"account_code": "1020", "amount": -10}]})
    assert res["isError"] and "read-only" in text_of(res)

    # write token: expense with a share paid for a person
    person = data(call(client, rw, "create_person", {"name": "Alice Martin"}))
    res = call(client, rw, "create_transaction", {
        "date": "2026-01-05", "description": "Restaurant",
        "lines": [{"account_code": "1020", "amount": -80},
                  {"account_code": "4000", "amount": 40},
                  {"account_code": person["code"], "amount": 40, "memo": "Alice"}]})
    tx = data(res)
    assert tx["state"] == "confirmed" and len(tx["lines"]) == 3

    # unbalanced entries are refused with a readable error
    res = call(client, rw, "create_transaction", {
        "date": "2026-01-05", "description": "bad",
        "lines": [{"account_code": "4000", "amount": 10}, {"account_code": "1020", "amount": -9}]})
    assert res["isError"]
    res = call(client, rw, "create_transaction", {
        "date": "2026-01-05", "description": "bad",
        "lines": [{"account_code": "9999", "amount": 10}, {"account_code": "1020", "amount": -10}]})
    assert res["isError"] and "Unknown account" in text_of(res)

    found = data(call(client, ro, "search_transactions", {"text": "restaurant"}))
    assert found["total"] == 1
    rec = data(call(client, ro, "receivables"))
    assert any(Decimal(r["balance"]) == 40 for r in rec), rec
    bs = data(call(client, ro, "balance_sheet", {"at": "2026-12-31"}))
    assert bs["balanced"] is True

    # revoked token stops working
    tid = client.get("/api/tokens").json()[0]["id"]
    client.delete(f"/api/tokens/{tid}")
    assert rpc(client, ro, "tools/list").status_code == 401


def test_mcp_classify_imported_entry(client, accounts):
    from .test_accounting import camt_files

    client.post("/api/imports", files=camt_files(), data={"iban_map": '{"CH690027427423101840J": "new"}'})
    rw = make_token(client, write=True)
    items = data(call(client, rw, "search_transactions", {"state": ["unclassified"], "text": "migros"}))["items"]
    assert items
    tx = items[0]
    bank_amount = next(Decimal(ln["amount"]) for ln in tx["lines"] if ln.get("bank_entry_id"))
    # split the purchase between groceries and household
    a = abs(bank_amount)
    half = (a / 2).quantize(Decimal("0.01"))
    res = call(client, rw, "classify_transaction", {
        "transaction_id": tx["id"],
        "splits": [{"account_code": "4000", "amount": str(half)}, {"account_code": "4100", "amount": str(a - half)}],
        "confirm": True})
    out = data(res)
    assert out["state"] == "confirmed"
    assert sorted(ln["account_code"] for ln in out["lines"]) == ["1020", "4000", "4100"]
    assert out["bank_entries"]

    res = call(client, rw, "classify_transaction", {"transaction_id": tx["id"], "account_code": "4000"})
    assert [ln["account_code"] for ln in data(res)["lines"]].count("4000") == 1
    res = call(client, rw, "classify_transaction", {
        "transaction_id": tx["id"], "splits": [{"account_code": "4000", "amount": "1.00"}]})
    assert res["isError"] and "add up" in text_of(res)


def test_mcp_oauth_jwt(client, monkeypatch):
    """Access tokens issued by the OIDC provider for this app are accepted when MCP_OAUTH is on."""
    import time

    import anyio
    from joserfc import jwt
    from joserfc.jwk import KeySet, RSAKey

    from app.config import get_settings
    from app.mcp_server import FinnTokenVerifier

    client.get("/api/me")  # creates the local user (oidc_sub "local")
    s = get_settings()
    for k, v in {"mcp_oauth": True, "auth_mode": "oidc", "oidc_issuer": "https://auth.test/application/o/finn/",
                 "oidc_client_id": "finn-client", "oidc_client_secret": "a-long-client-secret-for-tests-0123456789"}.items():
        monkeypatch.setattr(s, k, v)
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    v = FinnTokenVerifier()
    v._jwks = (time.monotonic(), KeySet([key]))

    def tok(**over):
        claims = {"iss": s.oidc_issuer, "aud": "finn-client", "sub": "local", "exp": int(time.time()) + 300, **over}
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key)

    ok = anyio.run(v.verify_token, tok())
    assert ok is not None and ok.claims["write"] is True
    assert anyio.run(v.verify_token, tok(aud="other-app")) is None
    assert anyio.run(v.verify_token, tok(iss="https://evil.test/")) is None
    assert anyio.run(v.verify_token, tok(exp=int(time.time()) - 3600)) is None
    assert anyio.run(v.verify_token, tok(sub="stranger")) is None
    # HS256 signed with the client secret (provider without signing key)
    from joserfc.jwk import OctKey
    hs = jwt.encode({"alg": "HS256"}, {"iss": s.oidc_issuer, "aud": "finn-client", "sub": "local",
                                      "exp": int(time.time()) + 300}, OctKey.import_key("a-long-client-secret-for-tests-0123456789"))
    assert anyio.run(v.verify_token, hs) is not None
    forged = jwt.encode({"alg": "HS256"}, {"iss": s.oidc_issuer, "aud": "finn-client", "sub": "local",
                                          "exp": int(time.time()) + 300}, OctKey.import_key("a-different-guessed-secret-0123456789"))
    assert anyio.run(v.verify_token, forged) is None
