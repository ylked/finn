import json
from decimal import Decimal

from app.merchants import clean_name, fold

from .test_accounting import camt_files


def test_text_helpers():
    assert fold("Coop-2204 Laus.C. au C.") == "coop 2204 laus c au c"
    assert fold("Électronique Müller") == "electronique muller"
    assert clean_name("SUMUP  *Heisse maronen") == "Heisse maronen"
    assert clean_name("Ochsner Sport / 968") == "Ochsner Sport"
    assert clean_name("Denner Discount 1725 Cha") == "Denner Discount"
    assert clean_name("HOLY COW GOURMET BURGER") == "Holy Cow Gourmet Burger"
    assert clean_name("FNAC (Suisse) SA") == "FNAC (Suisse)"
    assert clean_name("1234") is None


def _manual(client, a, desc, cp, amount, account="4100", date="2026-02-03"):
    return client.post("/api/transactions", json={"date": date, "description": desc, "counterparty": cp, "lines": [
        {"account_id": a["1020"], "amount": f"-{amount}"}, {"account_id": a[account], "amount": amount}]}).json()


def test_detection_correction_and_insights(client, accounts):
    a = {k: v["id"] for k, v in accounts.items()}
    client.post("/api/imports", files=camt_files(), data={"iban_map": '{"CH690027427423101840J": "new"}'})
    a["1020"] = next(x["id"] for x in client.get("/api/accounts").json() if x["iban"])

    # imported card payments at Migros branches are all "Migros" (catalog), with a logo URL
    migros = client.get("/api/transactions", params={"q": "migros"}).json()["items"]
    assert migros and all(t["merchant"]["name"] == "Migros" for t in migros)
    assert migros[0]["merchant"]["logo_url"].startswith(f"/api/merchants/{migros[0]['merchant']['id']}/logo")
    mid = migros[0]["merchant"]["id"]

    # people (TWINT "LAST, FIRST") and own-account transfers get no merchant
    t = client.post("/api/transactions", json={"date": "2026-02-01", "description": "Payment UBS TWINT", "counterparty":
                    "DESMEULES, PATRICIA", "lines": [{"account_id": a["1020"], "amount": "-7"},
                                                    {"account_id": a["4110"], "amount": "7"}]}).json()
    assert t["merchant"] is None

    # unknown shop: guessed from the counterparty
    kp = _manual(client, a, "Debit card payment // Kanpai T. Lo", "Kanpai T. Lo", "14.40", "4110")
    assert kp["merchant"]["name"] == "Kanpai T. Lo"
    kp2 = _manual(client, a, "Debit card payment // Kanpai T. Lo", "Kanpai T. Lo", "20.00", "4110", "2026-02-10")
    assert kp2["merchant"]["id"] == kp["merchant"]["id"]

    # the user renames it into an existing merchant + learn: similar entries follow
    r = client.put(f"/api/transactions/{kp['id']}/merchant", json={"name": "Kanpai", "learn": True}).json()
    assert r["merchant"]["name"] == "Kanpai" and r["applied"] == 1
    assert client.get(f"/api/transactions/{kp2['id']}").json()["merchant"]["name"] == "Kanpai"
    later = _manual(client, a, "Debit card payment // Kanpai T. Lo", "Kanpai T. Lo", "9.00", "4110", "2026-02-12")
    assert later["merchant"]["name"] == "Kanpai"
    got = client.get(f"/api/transactions/{kp['id']}").json()
    assert got["merchant_locked"] is True

    # merge the old guessed merchant away; delete leaves transactions without merchant and locked
    old = next(m for m in client.get("/api/merchants").json() if m["name"] == "Kanpai T. Lo")
    kanpai = r["merchant"]["id"]
    assert client.post(f"/api/merchants/{old['id']}/merge", json={"into_id": kanpai}).json()["tx_count"] == 3
    assert not [m for m in client.get("/api/merchants").json() if m["name"] == "Kanpai T. Lo"]

    # bulk assignment + domain edit resets the logo
    ids = [x["id"] for x in migros[:2]]
    assert client.post("/api/transactions/bulk-merchant", json={"ids": ids, "merchant_id": kanpai}).json()["updated"] == 2
    m = client.patch(f"/api/merchants/{kanpai}", json={"domain": "https://www.kanpai.ch/menu"}).json()
    assert m["domain"] == "kanpai.ch" and m["logo_url"]
    assert client.patch(f"/api/merchants/{kanpai}", json={"domain": "not a domain"}).status_code == 422

    # custom logo upload (served without ledger header, cached)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    m = client.put(f"/api/merchants/{kanpai}/logo", files={"file": ("l.png", png, "image/png")}).json()
    assert m["logo_status"] == "custom"
    img = client.get(m["logo_url"])
    assert img.status_code == 200 and img.content == png and "immutable" in img.headers["cache-control"]

    # insights: categories, merchants, monthly totals add up
    ins = json.loads(client.get("/api/insights", params={"start": "2026-01-01", "end": "2026-02-28"}).text,
                     parse_float=Decimal)
    tot = ins["totals"]
    assert Decimal(tot["expenses"]) == sum(Decimal(c["amount"]) for c in ins["categories"])
    assert Decimal(tot["expenses"]) == sum(Decimal(g["amount"]) for g in ins["groups"])
    assert Decimal(tot["expenses"]) == sum(Decimal(m["expenses"]) for m in ins["monthly"])
    assert [m["month"] for m in ins["monthly"]] == ["2026-01", "2026-02"]
    rest = next(c for c in ins["categories"] if c["code"] == "4110")
    assert Decimal(rest["amount"]) >= Decimal("50.40")
    food = next(g for g in ins["groups"] if g["code"] == "41")
    assert Decimal(food["amount"]) >= Decimal(rest["amount"])
    k = next(x for x in ins["merchants"] if x["id"] == kanpai)
    assert k["count"] == 5  # 3 restaurant visits + 2 re-assigned Migros entries (still unclassified)
    assert Decimal(tot["unclassified"]) > 0

    s = json.loads(client.get(f"/api/insights/merchants/{mid}").text, parse_float=Decimal)
    assert s["merchant"]["name"] == "Migros"
    assert s["totals"]["count"] == len(migros) - 2
    assert Decimal(s["totals"]["amount"]) == sum(Decimal(m["amount"]) for m in s["monthly"])
    assert client.get("/api/transactions", params={"merchant_id": mid}).json()["total"] == len(migros) - 2

    # re-detection keeps user choices
    assert client.post("/api/merchants/detect", json={"redo": True}).status_code == 200
    assert client.get(f"/api/transactions/{ids[0]}").json()["merchant"]["id"] == kanpai
    # back to automatic
    assert client.post(f"/api/transactions/{ids[0]}/merchant/reset").json()["merchant"]["name"] == "Migros"

    # deleting a catalog merchant: it is not re-created by detection
    assert client.delete(f"/api/merchants/{mid}").status_code == 200
    client.post("/api/merchants/detect", json={"redo": True})
    assert not [m for m in client.get("/api/merchants").json() if m["name"] == "Migros"]
