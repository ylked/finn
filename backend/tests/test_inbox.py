PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
NO_CSRF = {"X-Finn": "0"}  # Shortcuts sends no X-Finn header; a bearer token stands in for it


def _token(client, write=True):
    return client.post("/api/tokens", json={"name": "Shortcuts", "can_write": write}).json()["token"]


def test_inbox_receipt_raw_body_and_form(client, accounts):
    tok = _token(client)
    h = {**NO_CSRF, "Authorization": f"Bearer {tok}"}
    # raw body (Shortcuts "Request Body: File")
    r = client.post("/api/inbox?filename=ticket.pdf", content=PDF, headers={**h, "Content-Type": "application/pdf"})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1 and r.json()["kind"] == "receipt"
    # form with two files under any field name
    r = client.post("/api/inbox", files=[("file", ("a.pdf", PDF, "application/pdf")), ("x", ("b.pdf", PDF, "application/pdf"))],
                    headers=h)
    assert r.json()["count"] == 2
    names = {x["filename"] for x in client.get("/api/receipts").json()}
    assert {"ticket.pdf", "a.pdf", "b.pdf"} <= names


def test_inbox_invoice(client, accounts):
    h = {**NO_CSRF, "Authorization": f"Bearer {_token(client)}"}
    r = client.post("/api/inbox?kind=invoice", files={"file": ("facture.pdf", PDF, "application/pdf")}, headers=h).json()
    inv = client.get(f"/api/invoices/{r['ids'][0]}").json()
    assert inv["documents"][0]["filename"] == "facture.pdf" and inv["documents"][0]["kind"] == "invoice"
    assert r["url"].endswith(f"/invoices?open={inv['id']}")


def test_inbox_rejects(client, accounts):
    files = {"file": ("a.pdf", PDF, "application/pdf")}
    assert client.post("/api/inbox", files=files, headers=NO_CSRF).status_code == 403  # no token: CSRF check applies
    assert client.post("/api/inbox", files=files, headers={**NO_CSRF, "Authorization": "Bearer finn_nope"}).status_code == 401
    ro = _token(client, write=False)
    assert client.post("/api/inbox", files=files, headers={**NO_CSRF, "Authorization": f"Bearer {ro}"}).status_code == 403
    h = {**NO_CSRF, "Authorization": f"Bearer {_token(client)}"}
    assert client.post("/api/inbox", content=b"hello", headers={**h, "Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/inbox?kind=x", files=files, headers=h).status_code == 422
