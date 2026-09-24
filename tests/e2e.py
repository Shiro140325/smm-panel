import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
API = "http://127.0.0.1:8000"
MOCK = "http://127.0.0.1:9001"
WH_SECRET = os.environ["PAYMONGO_WEBHOOK_SECRET"]
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def signed(body: dict, secret=WH_SECRET, live=True):
    raw = json.dumps(body).encode()
    t = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    hdr = f"t={t},te=,li={sig}" if live else f"t={t},te={sig},li="
    return raw, {"Paymongo-Signature": hdr, "Content-Type": "application/json"}


def paid_event(topup_id, amount_php):
    return {"data": {"id": "evt_1", "attributes": {"type": "checkout_session.payment.paid", "data": {
        "id": "cs_1", "attributes": {"reference_number": topup_id, "metadata": {"topup_id": topup_id},
                                     "payments": [{"attributes": {"amount": amount_php * 100, "source": {"type": "gcash"}}}]}}}}}


async def sql(q, params=None):
    from app.db import transaction
    async with transaction() as db:
        if q.strip().lower().startswith("select") or "returning" in q.lower():
            return await db.fetch_all(q, params)
        await db.execute(q, params)


async def main():
    from app.workers.sync import run_sync_once

    # --- setup provider + catalog + services
    await sql("insert into providers (name, api_url, api_key_env, currency) values ('mock', :u, 'MOCK_KEY', 'USD')",
              {"u": f"{MOCK}/api/v2"})
    await run_sync_once()
    ps = await sql("select provider_service_id, rate, refill from provider_services order by 1")
    check("catalog synced", len(ps) == 3, ps)
    await sql("""insert into services (provider_id, provider_service_id, platform, name, tier, refill_days, markup_pct)
                 values (1, 1, 'tiktok', 'TikTok Followers', 'Basic', 0, 80),
                        (1, 2, 'tiktok', 'TikTok Followers', 'HQ', 30, 60),
                        (1, 3, 'instagram', 'Instagram Custom Comments', 'Basic', 0, 60)""")

    c = httpx.AsyncClient(base_url=API)
    r = await c.get("/services")
    svcs = r.json()
    # 0.50 USD * 58 * 1.8 = 52.20 ; 1.20 * 58 * 1.6 = 111.36
    by_svc = {s["id"]: s for s in svcs}
    check("service pricing", [by_svc[1]["price_per_1k_php"], by_svc[2]["price_per_1k_php"]] == [52.2, 111.36], svcs)
    check("custom_comments flag", by_svc[3]["custom_comments"] is True and by_svc[1]["custom_comments"] is False, svcs)

    # --- auth
    r = await c.post("/auth/register", json={"email": "Juan@Example.com", "password": "password123"})
    check("register", r.status_code == 200, r.text)
    r = await c.post("/auth/register", json={"email": "juan@example.com", "password": "password123"})
    check("duplicate email 409", r.status_code == 409, r.text)
    c2 = httpx.AsyncClient(base_url=API)
    r = await c2.post("/auth/login", json={"email": "juan@example.com", "password": "wrongpass1"})
    check("bad login 401", r.status_code == 401)
    r = await c2.get("/auth/me")
    check("me without cookie 401", r.status_code == 401)
    r = await c.get("/auth/me")
    me = r.json()
    check("me balance 0", r.status_code == 200 and me["balance_php"] == 0, r.text)

    # --- order with no balance
    r = await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@a", "quantity": 1000})
    check("order without balance 402", r.status_code == 402, r.text)

    # --- top-up via webhook
    tid = str(uuid.uuid4())
    await sql("insert into topups (id, user_id, amount_php, method) values (CAST(:id AS uuid), :u, 500, 'paymongo')",
              {"id": tid, "u": me["id"]})
    raw, hdr = signed(paid_event(tid, 500), secret="wrong")
    r = await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    check("webhook bad signature 401", r.status_code == 401)
    raw, hdr = signed(paid_event(tid, 400))
    r = await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    bal = (await c.get("/auth/me")).json()["balance_php"]
    check("amount mismatch not credited", r.status_code == 200 and bal == 0, bal)
    raw, hdr = signed(paid_event(tid, 500), live=False)
    r = await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    r2 = await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    bal = (await c.get("/auth/me")).json()["balance_php"]
    check("topup credited once (test-mode sig, retried)", r.status_code == 200 and r2.status_code == 200 and bal == 500, bal)
    m = await sql("select method, status from topups where id = CAST(:id AS uuid)", {"id": tid})
    check("topup records actual method (gcash)", m[0]["method"] == "gcash" and m[0]["status"] == "credited", m)
    raw, hdr = signed({"data": {"attributes": {"type": "checkout_session.payment.paid", "data": {"id": "x", "attributes": {
        "reference_number": "not-a-uuid", "payments": [{"attributes": {"amount": 100}}]}}}}})
    r = await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    check("garbage topup id → 200, ignored", r.status_code == 200, r.text)

    # --- orders
    r = await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@a", "quantity": 50})
    check("below min 400", r.status_code == 400, r.text)
    r = await c.post("/orders", json={"service_id": 1, "link": "tiktok.com/@a", "quantity": 1000})
    check("bad link 422", r.status_code == 422, r.text)
    r = await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@reject", "quantity": 1000})
    bal = (await c.get("/auth/me")).json()["balance_php"]
    check("provider reject → 400 + full refund", r.status_code == 400 and bal == 500, f"{r.text} bal={bal}")

    r = await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@a", "quantity": 2000})
    o_basic = r.json()
    check("basic order placed, charge 104.40", r.status_code == 200 and o_basic["charge_php"] == 104.4, r.text)
    r = await c.post("/orders", json={"service_id": 2, "link": "https://tiktok.com/@a", "quantity": 1000})
    o_hq = r.json()
    check("HQ order placed, charge 111.36", r.status_code == 200 and o_hq["charge_php"] == 111.36, r.text)
    bal = (await c.get("/auth/me")).json()["balance_php"]
    check("balance after orders 284.24", bal == 284.24, bal)

    r = await c.post("/orders", json={"service_id": 2, "link": "https://tiktok.com/@a", "quantity": 3000})
    check("overspend blocked 402", r.status_code == 402, r.text)

    # --- sync: basic partial (500 of 2000 remain), HQ completed
    pos = await sql("select id, provider_order_id from orders where status = 'pending' order by id")
    pid = {o["id"]: str(o["provider_order_id"]) for o in pos}
    m = httpx.AsyncClient(base_url=MOCK)
    await m.post("/_set_order", data={"oid": pid[o_basic["id"]], "status": "Partial", "remains": "500"})
    await m.post("/_set_order", data={"oid": pid[o_hq["id"]], "status": "Completed", "remains": "0"})
    await run_sync_once()
    await run_sync_once()  # idempotent
    bal = (await c.get("/auth/me")).json()["balance_php"]
    # refund 104.40 * 500/2000 = 26.10 → 310.34
    check("partial refunded once (26.10)", bal == 310.34, bal)

    orders = (await c.get("/orders")).json()
    by_id = {o["id"]: o for o in orders}
    check("list: statuses", by_id[o_basic["id"]]["status"] == "partial" and by_id[o_hq["id"]]["status"] == "completed",
          orders)
    check("list: refill_state", by_id[o_basic["id"]]["refill_state"] == "none"
          and by_id[o_hq["id"]]["refill_state"] == "available", orders)
    check("list: refunded_php", float(by_id[o_basic["id"]]["refunded_php"]) == 26.1, by_id[o_basic["id"]])
    r = await c.get("/orders", params={"status": "partial"})
    check("filter by status", [o["id"] for o in r.json()] == [o_basic["id"]], r.text)
    r = await c.get("/orders", params={"q": f"#{o_hq['id']}"})
    check("search by #id", [o["id"] for o in r.json()] == [o_hq["id"]], r.text)

    # --- refills
    r = await c.post(f"/orders/{o_basic['id']}/refill")
    check("refill on no-refill service 400", r.status_code == 400, r.text)
    r = await c.post(f"/orders/{o_hq['id']}/refill")
    check("refill requested", r.status_code == 200, r.text)
    rid = str(r.json().get("refill_id"))
    r = await c.post(f"/orders/{o_hq['id']}/refill")
    check("second refill while pending 409", r.status_code == 409, r.text)
    st = (await c.get("/orders")).json()
    check("refill_state requested", {o["id"]: o for o in st}[o_hq["id"]]["refill_state"] == "requested", st)
    await m.post("/_set_refill", data={"rid": rid, "status": "Completed"})
    await run_sync_once()
    rows = await sql("select status, resolved_at from provider_refills")
    check("refill completed via sync", rows[0]["status"] == "completed" and rows[0]["resolved_at"], rows)
    st = (await c.get("/orders")).json()
    check("refill available again after completion", {o["id"]: o for o in st}[o_hq["id"]]["refill_state"] == "available", st)

    # --- custom comments
    r = await c.post("/orders", json={"service_id": 3, "link": "https://instagram.com/p/x", "quantity": 1})
    check("custom comments without comments 400", r.status_code == 400, r.text)
    bal_before = (await c.get("/auth/me")).json()["balance_php"]
    r = await c.post("/orders", json={"service_id": 3, "link": "https://instagram.com/p/x", "quantity": 999,
                                      "comments": "Nice post!\n\n  Galing  \nSolid 🔥\n"})
    j = r.json()
    # 1.00 USD * 58 * 1.6 = 92.80/1K → 3 comments = 0.2784 → 0.28
    check("custom comments: quantity = lines, charge 0.28", r.status_code == 200 and j["quantity"] == 3 and j["charge_php"] == 0.28, r.text)
    last = (await (httpx.AsyncClient(base_url=MOCK)).get("/_last_add")).json()
    check("custom comments sent to provider", last.get("comments") == "Nice post!\nGaling\nSolid 🔥" and last.get("quantity") == 3, last)
    bal_after = (await c.get("/auth/me")).json()["balance_php"]
    check("custom comments debited", round(bal_before - bal_after, 2) == 0.28, (bal_before, bal_after))

    # --- PayMongo checkout + fallback crediting (webhook missed)
    m2 = httpx.AsyncClient(base_url=MOCK)
    bal0 = (await c.get("/auth/me")).json()["balance_php"]
    r = await c.post("/topups", json={"amount_php": 300})
    j = r.json()
    check("create checkout", r.status_code == 200 and j["checkout_url"].startswith("https://checkout.example/"), r.text)
    sent = (await m2.get("/_pm_last_create")).json()["data"]["attributes"]
    check("checkout payload: ₱300, gcash+paymaya, return URL", sent["line_items"][0]["amount"] == 30000
          and sent["payment_method_types"] == ["gcash", "paymaya"] and "/dashboard/#funds?status=success" in sent["success_url"], sent)
    r = await c.post(f"/topups/{j['topup_id']}/check")
    check("check before paying → pending", r.json().get("status") == "pending", r.text)
    sid = (await sql("select checkout_id from topups where id = CAST(:id AS uuid)", {"id": j["topup_id"]}))[0]["checkout_id"]
    await m2.post("/_pm_pay", data={"sid": sid, "amount_php": 300, "source": "paymaya"})
    r = await c.post(f"/topups/{j['topup_id']}/check")
    r2 = await c.post(f"/topups/{j['topup_id']}/check")
    bal1 = (await c.get("/auth/me")).json()["balance_php"]
    check("check after paying → credited once", r.json().get("status") == "credited" and r2.json().get("status") == "credited"
          and round(bal1 - bal0, 2) == 300, (r.text, bal0, bal1))
    row = await sql("select method from topups where id = CAST(:id AS uuid)", {"id": j["topup_id"]})
    check("method recorded from checkout (paymaya)", row[0]["method"] == "paymaya", row)
    # background reconcile for a second top-up, then a late webhook must not double-credit
    r = await c.post("/topups", json={"amount_php": 150}); t2 = r.json()["topup_id"]
    sid2 = (await sql("select checkout_id from topups where id = CAST(:id AS uuid)", {"id": t2}))[0]["checkout_id"]
    await m2.post("/_pm_pay", data={"sid": sid2, "amount_php": 150})
    await run_sync_once()
    raw, hdr = signed(paid_event(t2, 150))
    await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    bal2 = (await c.get("/auth/me")).json()["balance_php"]
    check("sync reconcile credits; late webhook doesn't double", round(bal2 - bal1, 2) == 150, (bal1, bal2))
    r = await c2.post(f"/topups/{j['topup_id']}/check")
    check("other user can't check my top-up", r.status_code in (401, 404), r.text)
    await m2.aclose()

    # --- live status: GET /orders pulls fresh provider status (no background sync)
    r = await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@live", "quantity": 100})
    live_id = r.json()["id"]
    po = (await sql("select provider_order_id from orders where id = :i", {"i": live_id}))[0]["provider_order_id"]
    m3 = httpx.AsyncClient(base_url=MOCK)
    await m3.post("/_set_order", data={"oid": str(po), "status": "In progress", "remains": "40"})
    await asyncio.sleep(10.5)   # past the per-user throttle
    st = {o["id"]: o for o in (await c.get("/orders")).json()}[live_id]
    check("orders list is live (in_progress, remains 40)", st["status"] == "in_progress" and st["remains"] == 40, st)
    await m3.aclose()

    # --- mass order
    async def n_orders():
        return (await sql("select count(*) n from orders where user_id = :u", {"u": me["id"]}))[0]["n"]
    before = await n_orders()
    r = await c.post("/orders/mass", json={"orders": "1|https://tiktok.com/@a|1000\n2|tiktok.com/@b|500\n99|https://x.com/c|100"})
    check("mass: invalid lines → 400 listing lines, nothing placed",
          r.status_code == 400 and "Line 2" in r.text and "Line 3" in r.text and await n_orders() == before, r.text)
    r = await c.post("/orders/mass", json={"orders": "3|https://instagram.com/p/x|5"})
    check("mass: custom comments rejected", r.status_code == 400 and "Custom comments" in r.text, r.text)
    r = await c.post("/orders/mass", json={"orders": "2|https://tiktok.com/@a|20000\n2|https://tiktok.com/@b|20000"})
    check("mass: total over balance → 402, nothing placed", r.status_code == 402 and await n_orders() == before, r.text)
    bal_a = (await c.get("/auth/me")).json()["balance_php"]
    text = "1|https://tiktok.com/@m1|100\n\n  #1 | https://tiktok.com/@m2 | 1,000 \n1|https://tiktok.com/@reject|200"
    r = await c.post("/orders/mass", json={"orders": text})
    j = r.json()
    res = {x["line"]: x for x in j.get("results", [])}
    bal_b = (await c.get("/auth/me")).json()["balance_php"]
    # 1: 100 x 52.20/1K = 5.22 ; 3: 1000 → 52.20 ; 4: rejected by provider → refunded
    check("mass: 2 placed, provider rejection fails only its line",
          r.status_code == 200 and j["placed"] == 2 and j["failed"] == 1 and res[1]["ok"] and res[3]["ok"] and not res[4]["ok"], j)
    check("mass: charged only placed lines (57.42)", j.get("charged_php") == 57.42 and round(bal_a - bal_b, 2) == 57.42, (j.get("charged_php"), bal_a, bal_b))

    # --- other user can't touch my order
    await c2.post("/auth/register", json={"email": "other@example.com", "password": "password123"})
    r = await c2.post(f"/orders/{o_hq['id']}/refill")
    check("other user's order 404", r.status_code == 404, r.text)

    # --- ledger integrity
    led = await sql("select reason, sum(delta) s, count(*) n from ledger where user_id = :u group by reason order by reason",
                    {"u": me["id"]})
    print(led)
    await c.aclose(); await c2.aclose(); await m.aclose()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


asyncio.run(main())
