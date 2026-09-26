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
    auto = await sql("select provider_service_id, platform, category, tier, refill_days, markup_pct, active "
                     "from services where auto order by provider_service_id")
    check("full catalog auto-imported (platform, category, tier, refill)",
          [(a["platform"], a["category"], a["tier"], a["refill_days"]) for a in auto]
          == [("tiktok", "Followers", "Basic", 0), ("tiktok", "Followers", "HQ", 30), ("instagram", "Comments", "Basic", 0)]
          and all(a["markup_pct"] is None and a["active"] for a in auto), auto)
    r = httpx.get(f"{API}/services")
    # tiered markup: $0.50 → 60% (not under $0.50), $1.20 → 60%, $1.00 → 60%
    check("auto services priced with tiered markup", sorted(s["price_per_1k_php"] for s in r.json()) == [46.4, 92.8, 111.36], r.text)
    check("featured list excludes auto rows", httpx.get(f"{API}/services?featured=1").json() == [])
    # the rest of this test uses hand-picked services with ids 1..3
    await sql("delete from services where auto")
    await sql("alter sequence services_id_seq restart with 1")
    await sql("""insert into services (provider_id, provider_service_id, platform, name, tier, refill_days, markup_pct)
                 values (1, 1, 'tiktok', 'TikTok Followers', 'Basic', 0, 80),
                        (1, 2, 'tiktok', 'TikTok Followers', 'HQ', 30, 60),
                        (1, 3, 'instagram', 'Instagram Custom Comments', 'Basic', 0, 60)""")
    from app.catalog import import_catalog
    res = await import_catalog(1)
    check("import skips services that have a hand-picked row", res["imported"] == 0 and res["added"] == 0, res)
    cats = await sql("select id, category from services order by id")
    check("hand-picked rows get a category", [c["category"] for c in cats] == ["Followers", "Followers", "Comments"], cats)

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
    check("no welcome credit unless WELCOME_CREDIT_PHP is set", r.json().get("welcome_php") == float(os.environ.get("WELCOME_CREDIT_PHP", "0")), r.text)
    r = await c.post("/auth/register", json={"email": "juan@example.com", "password": "password123"})
    check("duplicate email 409", r.status_code == 409, r.text)
    c2 = httpx.AsyncClient(base_url=API)
    r = await c2.post("/auth/login", json={"email": "juan@example.com", "password": "wrongpass1"})
    check("bad login 401", r.status_code == 401)
    r = await c2.get("/auth/me")
    check("me without cookie 401", r.status_code == 401)

    # --- login and sign-up rate limits (client IP comes from Cloudflare's header)
    ip = lambda a: {"cf-connecting-ip": a}
    for _ in range(3):   # + the bad login above = 4 misses
        await c2.post("/auth/login", json={"email": "juan@example.com", "password": "wrongpass1"}, headers=ip("10.0.0.1"))
    r = await c2.post("/auth/login", json={"email": "juan@example.com", "password": "password123"}, headers=ip("10.0.0.2"))
    check("login: right password still works after 4 misses (and clears them)", r.status_code == 200, r.text)
    for _ in range(5):
        await c2.post("/auth/login", json={"email": "juan@example.com", "password": "wrongpass1"}, headers=ip("10.0.0.1"))
    r = await c2.post("/auth/login", json={"email": "juan@example.com", "password": "password123"}, headers=ip("10.0.0.3"))
    check("login: account locked after 5 wrong passwords, from any IP", r.status_code == 429 and "15 minutes" in r.text, r.text)
    for i in range(20):
        await c2.post("/auth/login", json={"email": f"nobody{i}@example.com", "password": "wrongpass1"}, headers=ip("10.0.0.4"))
    r = await c2.post("/auth/login", json={"email": "other-ip@example.com", "password": "password123"}, headers=ip("10.0.0.4"))
    check("login: IP blocked after 20 wrong passwords", r.status_code == 429, r.text)
    c2.cookies.clear()
    n = int(os.environ.get("SIGNUPS_PER_IP_HOUR", "5"))
    codes = [(await c2.post("/auth/register", json={"email": f"su{i}@example.com", "password": "password123"},
                            headers=ip("10.0.0.5"))).status_code for i in range(n + 1)]
    check("sign-up: limited per IP per hour", codes[:n] == [200] * n and codes[n] == 429, codes)
    c2.cookies.clear()
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
    ids = [o["id"] for o in (await c.get("/orders", params={"status": "refilling"})).json()]
    check("Refilling tab shows the order while its refill runs", ids == [o_hq["id"]], ids)
    rid = str((await sql("select provider_refill_id from provider_refills where id = :i",
                         {"i": r.json().get("refill_id")}))[0]["provider_refill_id"])
    r = await c.post(f"/orders/{o_hq['id']}/refill")
    check("second refill while pending 409", r.status_code == 409, r.text)
    st = (await c.get("/orders")).json()
    check("refill_state requested", {o["id"]: o for o in st}[o_hq["id"]]["refill_state"] == "requested", st)
    await m.post("/_set_refill", data={"rid": rid, "status": "Completed"})
    await run_sync_once()
    rows = await sql("select status, resolved_at from provider_refills")
    check("refill completed via sync", rows[0]["status"] == "completed" and rows[0]["resolved_at"], rows)
    ids = [o["id"] for o in (await c.get("/orders", params={"status": "refilling"})).json()]
    check("…and drops off the Refilling tab once done", ids == [], ids)
    refunded = (await c.get("/orders", params={"status": "refunded"})).json()
    expect = {r["ref"]: float(r["delta"]) for r in await sql("select ref, delta from ledger where user_id = :u and reason = 'refund'", {"u": me["id"]})}
    check("Refunded tab: exactly the refunded orders, with amounts", {str(o["id"]): float(o["refunded_php"]) for o in refunded} == expect
          and all(float(o["refunded_php"]) > 0 for o in refunded), (refunded, expect))
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
    check("checkout payload: ₱300, QR Ph, return URL", sent["line_items"][0]["amount"] == 30000
          and sent["payment_method_types"] == ["qrph"] and "/dashboard/#funds?status=success" in sent["success_url"], sent)
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

    # --- cancel / auto-expire unpaid top-ups
    async def new_topup(amount):
        tid = (await c.post("/topups", json={"amount_php": amount})).json()["topup_id"]
        sid = (await sql("select checkout_id from topups where id = CAST(:id AS uuid)", {"id": tid}))[0]["checkout_id"]
        return tid, sid
    status_of = lambda tid: sql("select status from topups where id = CAST(:id AS uuid)", {"id": tid})
    t3, s3 = await new_topup(200)
    r = await c2.post(f"/topups/{t3}/cancel")
    check("other user can't cancel my top-up", r.status_code in (401, 404), r.text)
    r = await c.post(f"/topups/{t3}/cancel")
    sess = (await m2.get(f"/v1/checkout_sessions/{s3}")).json()["data"]["attributes"]
    check("cancel closes the top-up and expires the checkout", r.json().get("status") == "canceled"
          and (await status_of(t3))[0]["status"] == "canceled" and sess.get("status") == "expired", (r.text, sess))
    bal_a = (await c.get("/auth/me")).json()["balance_php"]
    await m2.post("/_pm_pay", data={"sid": s3, "amount_php": 200})   # money arrives anyway (e.g. mid-cancel)
    await run_sync_once()
    bal_b = (await c.get("/auth/me")).json()["balance_php"]
    check("payment after cancel is still credited", round(bal_b - bal_a, 2) == 200 and (await status_of(t3))[0]["status"] == "credited", (bal_a, bal_b))

    t4, s4 = await new_topup(120)
    await m2.post("/_pm_pay", data={"sid": s4, "amount_php": 120})   # paid, webhook not in yet
    r = await c.post(f"/topups/{t4}/cancel")
    bal_c = (await c.get("/auth/me")).json()["balance_php"]
    check("cancelling a paid top-up credits it instead", r.json().get("status") == "credited" and round(bal_c - bal_b, 2) == 120, (r.text, bal_b, bal_c))

    t5, _ = await new_topup(130)
    await sql("update topups set created_at = now() - interval '11 minutes' where id = CAST(:id AS uuid)", {"id": t5})
    t6, _ = await new_topup(140)
    lst = {t["id"]: t for t in (await c.get("/topups")).json()}
    check("unpaid after 10 minutes → expired; newer ones stay open", lst[t5]["status"] == "expired"
          and lst[t6]["status"] == "pending" and lst[t6]["expires_at"], (lst[t5], lst[t6]))
    await sql("update topups set created_at = now() - interval '11 minutes' where id = CAST(:id AS uuid)", {"id": t6})
    await run_sync_once()
    check("background sync expires stale top-ups too", (await status_of(t6))[0]["status"] == "expired")
    r = await c.post(f"/topups/{t5}/cancel")
    check("cancel on a closed top-up is a no-op", r.json().get("status") == "expired", r.text)
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

    # --- cancel a running order
    oc = (await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@cancelme", "quantity": 1000})).json()
    lst = {o["id"]: o for o in (await c.get("/orders")).json()}
    check("running order on a cancellable service offers cancel", lst[oc["id"]]["can_cancel"] and not lst[oc["id"]]["cancel_requested"], lst[oc["id"]])
    r = await c2.post(f"/orders/{oc['id']}/cancel")
    check("other user can't cancel my order", r.status_code == 404, r.text)
    bal_c0 = (await c.get("/auth/me")).json()["balance_php"]
    r = await c.post(f"/orders/{oc['id']}/cancel")
    lst = {o["id"]: o for o in (await c.get("/orders")).json()}
    check("cancel sent to provider, shown as requested, no refund yet", r.status_code == 200
          and lst[oc["id"]]["cancel_requested"] and not lst[oc["id"]]["can_cancel"]
          and (await c.get("/auth/me")).json()["balance_php"] == bal_c0, (r.text, lst[oc["id"]]))
    r = await c.post(f"/orders/{oc['id']}/cancel")
    check("second cancel 409", r.status_code == 409, r.text)
    pid_c = str((await sql("select provider_order_id from orders where id = :i", {"i": oc["id"]}))[0]["provider_order_id"])
    await m.post("/_set_order", data={"oid": pid_c, "status": "Canceled", "remains": "1000"})
    await run_sync_once()
    bal_c1 = (await c.get("/auth/me")).json()["balance_php"]
    lst = {o["id"]: o for o in (await c.get("/orders")).json()}
    check("provider cancels → full refund once, no more cancel button", round(bal_c1 - bal_c0, 2) == oc["charge_php"]
          and lst[oc["id"]]["status"] == "canceled" and not lst[oc["id"]]["can_cancel"] and not lst[oc["id"]]["cancel_requested"],
          (bal_c0, bal_c1, lst[oc["id"]]))
    oc2 = (await c.post("/orders", json={"service_id": 3, "link": "https://instagram.com/p/y", "quantity": 1, "comments": "Nice"})).json()
    lst = {o["id"]: o for o in (await c.get("/orders")).json()}
    r = await c.post(f"/orders/{oc2['id']}/cancel")
    check("service without cancel: no button, 400", not lst[oc2["id"]]["can_cancel"] and r.status_code == 400, r.text)
    r = await c.post(f"/orders/{o_hq['id']}/cancel")
    check("completed order can't be canceled", r.status_code == 400, r.text)

    # --- control panel (/admin)
    ca = httpx.AsyncClient(base_url=API)
    r = await ca.get("/admin/api/overview")
    check("admin: no session → 401", r.status_code == 401, r.text)
    r = await c.get("/admin/api/overview")
    check("admin: a customer's login doesn't open it", r.status_code == 401, r.text)
    r = await ca.post("/admin/api/login", json={"password": "wrong"})
    check("admin: wrong password 401", r.status_code == 401, r.text)
    r = await ca.post("/admin/api/login", json={"password": os.environ["ADMIN_PASS"]})
    check("admin: right password → session cookie", r.status_code == 200 and "admin_session" in ca.cookies, r.text)
    ov = (await ca.get("/admin/api/overview")).json()
    check("admin: overview has money, sales and SMMGen balance", ov.get("customers", 0) >= 2 and "all" in ov.get("sales", {})
          and ov["providers"] and ov["providers"][0].get("balance") == 100.0, ov)
    bal_led = float((await sql("select coalesce(sum(delta), 0) s from ledger"))[0]["s"])
    check("admin: customer balances = ledger total", round(ov["balances_php"], 2) == round(bal_led, 2), (ov["balances_php"], bal_led))

    orr = (await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@review", "quantity": 100})).json()
    await sql("update orders set status = 'needs_review' where id = :i", {"i": orr["id"]})
    rows = (await ca.get("/admin/api/orders", params={"status": "needs_review"})).json()
    ar = (await ca.get("/admin/api/orders", params={"status": "refunded"})).json()
    check("admin: Refunded filter across customers", ar and all(float(o["refunded_php"]) > 0 for o in ar), ar[:2])
    check("admin: orders filter finds the one under review", [o["id"] for o in rows] == [orr["id"]] and rows[0]["email"] == "juan@example.com", rows)
    b0 = (await c.get("/auth/me")).json()["balance_php"]
    r = await ca.post(f"/admin/api/orders/{orr['id']}/refund")
    b1 = (await c.get("/auth/me")).json()["balance_php"]
    check("admin: refund an order under review", r.status_code == 200 and round(b1 - b0, 2) == orr["charge_php"], (r.text, b0, b1))
    r = await ca.post(f"/admin/api/orders/{orr['id']}/refund")
    check("admin: can't refund it twice", r.status_code == 400, r.text)

    users = (await ca.get("/admin/api/users", params={"q": "juan@"})).json()
    uid = users[0]["id"]
    check("admin: customer search", len(users) == 1 and round(users[0]["balance_php"], 2) == round(b1, 2), users)
    r = await ca.post(f"/admin/api/users/{uid}/adjust", json={"amount_php": 50, "note": "goodwill credit"})
    b2 = (await c.get("/auth/me")).json()["balance_php"]
    led = await sql("select reason, ref from ledger where user_id = :u order by id desc limit 1", {"u": uid})
    check("admin: add balance, recorded with the note", r.status_code == 200 and round(b2 - b1, 2) == 50
          and led[0]["reason"] == "adjustment" and led[0]["ref"] == "goodwill credit", (r.text, b1, b2, led))
    r = await ca.post(f"/admin/api/users/{uid}/adjust", json={"amount_php": -(b2 + 1), "note": "too much"})
    check("admin: can't take a balance below zero", r.status_code == 400, r.text)
    tps = (await ca.get("/admin/api/topups", params={"status": "credited"})).json()
    check("admin: top-ups list", tps and all(t["status"] == "credited" for t in tps), tps[:2])

    r = await ca.post("/admin/api/services/1/hidden", json={"hidden": True})
    ids = [x["id"] for x in (await c.get("/services")).json()]
    r2 = await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@hidden", "quantity": 100})
    hid = (await ca.get("/admin/api/services", params={"hidden": "true"})).json()
    check("admin: hidden service leaves the site and can't be ordered", r.status_code == 200 and 1 not in ids
          and r2.status_code in (400, 404) and [x["id"] for x in hid] == [1], (ids, r2.text, hid))
    await run_sync_once()   # catalog import must not bring it back
    check("admin: stays hidden after a catalog sync", 1 not in [x["id"] for x in (await c.get("/services")).json()])
    await ca.post("/admin/api/services/1/hidden", json={"hidden": False})
    check("admin: showing it again", 1 in [x["id"] for x in (await c.get("/services")).json()])

    await ca.post("/admin/api/logout")
    r = await ca.get("/admin/api/overview")
    check("admin: logout ends the session", r.status_code == 401, r.text)
    for _ in range(5):
        await ca.post("/admin/api/login", json={"password": "nope"})
    r = await ca.post("/admin/api/login", json={"password": os.environ["ADMIN_PASS"]})
    check("admin: locked for 15 min after 5 wrong passwords", r.status_code == 429, r.text)
    await ca.aclose()

    # --- recently completed (all customers, anonymous)
    cx = httpx.AsyncClient(base_url=API)
    await cx.post("/auth/register", json={"email": "viewer@example.com", "password": "password123"})
    feed = (await cx.get("/orders/recently-completed")).json()
    check("recently completed: other customers' orders, no links or owners",
          any(f["quantity"] == o_hq["quantity"] for f in feed) and all(set(f) == {"platform", "category", "service_name", "tier", "quantity", "completed_at", "took_seconds"} for f in feed), feed[:2])
    check("recently completed: login required", (await httpx.AsyncClient(base_url=API).get("/orders/recently-completed")).status_code == 401)
    await cx.aclose()

    # --- service timing: average over exactly the last 15 completed orders, plus the latest one
    await sql("""insert into orders (user_id, service_id, provider_id, link, quantity, price_php, status, created_at, completed_at)
                 select :u, 3, 1, 'https://t/' || g, 10, 1, 'completed',
                        now() - make_interval(mins => g * 10) - make_interval(secs => g * 60), now() - make_interval(mins => g * 10)
                   from generate_series(1, 16) g""", {"u": me["id"]})   # order g took g minutes; g = 16 is the oldest
    timing = (await c.get("/services/timing")).json()
    t3 = timing.get("3", {})
    check("timing: average of the newest 15 only (1..15 min → 8 min), last = newest (1 min)",
          t3.get("n") == 15 and t3.get("avg_seconds") == 480 and t3.get("last_seconds") == 60, t3)
    t_hq = timing.get("2", {})   # the HQ order (service 2) completed earlier in this test
    check("timing: under 15 orders, no average yet but a last completion", t_hq.get("n", 0) >= 1
          and t_hq.get("avg_seconds") is None and t_hq.get("last_seconds") is not None, (timing, o_hq))

    # --- referral program
    aff = (await c.get("/account/affiliate")).json()
    check("affiliate: code and link", aff["code"] and aff["link"].endswith("/?ref=" + aff["code"]) and aff["pct"] == 5, aff)
    check("affiliate: same code next time", (await c.get("/account/affiliate")).json()["code"] == aff["code"])
    c3 = httpx.AsyncClient(base_url=API)
    r = await c3.post("/auth/register", json={"email": "friend@example.com", "password": "password123", "ref": aff["code"].upper()})
    fid = r.json()["id"]
    c4 = httpx.AsyncClient(base_url=API)
    await c4.post("/auth/register", json={"email": "stranger@example.com", "password": "password123", "ref": "nosuchcode"})
    refs = {x["email"]: x["referred_by"] for x in await sql("select email, referred_by from users where email in ('friend@example.com', 'stranger@example.com')")}
    check("referral: signup link records the referrer; unknown code ignored",
          refs == {"friend@example.com": me["id"], "stranger@example.com": None}, refs)
    bal0 = (await c.get("/auth/me")).json()["balance_php"]
    ftid = str(uuid.uuid4())
    await sql("insert into topups (id, user_id, amount_php, method) values (CAST(:id AS uuid), :u, 1000, 'paymongo')", {"id": ftid, "u": fid})
    raw, hdr = signed(paid_event(ftid, 1000))
    await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    await c.post("/webhooks/paymongo", content=raw, headers=hdr)   # replayed webhook
    bal1 = (await c.get("/auth/me")).json()["balance_php"]
    check("referral: 5% of the friend's top-up credited once", round(bal1 - bal0, 2) == 50, (bal0, bal1))
    check("referral: friend gets their full top-up", (await c3.get("/auth/me")).json()["balance_php"] == 1000)
    aff = (await c.get("/account/affiliate")).json()
    check("affiliate: stats", aff["referred"] == 1 and aff["paying"] == 1 and aff["earned_php"] == 50
          and aff["recent"][0]["email"] == "f***@example.com" and float(aff["recent"][0]["topup_php"]) == 1000, aff)
    await c4.aclose()

    # --- reseller API
    v2 = lambda **kw: c3.post("/api/v2", data=kw)
    check("api: no key yet", (await c3.get("/account/api-key")).json()["has_key"] is False)
    key = (await c3.post("/account/api-key")).json()["key"]
    check("api: key shown once, only its hash stored", len(key) == 32 and (await c3.get("/account/api-key")).json()["has_key"]
          and key not in str(await sql("select api_key_hash from users where id = :u", {"u": fid})))
    r = await v2(key="wrong", action="balance")
    check("api: wrong key", r.json() == {"error": "Invalid API key"}, r.text)
    r = await v2(key=key, action="balance")
    check("api: balance in PHP", r.json() == {"balance": "1000.00", "currency": "PHP"}, r.text)
    svcs = (await v2(key=key, action="services")).json()
    s1 = next(x for x in svcs if x["service"] == 1)
    check("api: services list", {"service", "name", "type", "category", "rate", "min", "max", "refill", "cancel"} <= set(s1)
          and s1["rate"] == "52.20" and s1["category"].startswith("TikTok"), s1)
    r = await v2(key=key, action="add", service=1, link="https://www.tiktok.com/@api", quantity=1000)
    oid = r.json().get("order")
    check("api: add order", isinstance(oid, int), r.text)
    check("api: charged like the site", (await v2(key=key, action="balance")).json()["balance"] == "947.80")
    r = await v2(key=key, action="add", service=1, link="tiktok.com/@api", quantity=1000)
    check("api: bad link", "error" in r.json(), r.text)
    r = await v2(key=key, action="add", service=1, link="https://www.tiktok.com/@api", quantity=100000000)
    check("api: quantity out of range", "Quantity must be between" in r.json().get("error", ""), r.text)
    r = await v2(key=key, action="status", order=oid)
    check("api: status", r.json() == {"charge": "52.20", "start_count": "0", "status": "Pending", "remains": "0", "currency": "PHP"}, r.text)
    r = await v2(key=key, action="status", orders=f"{oid},999999")
    check("api: multi status", r.json()[str(oid)]["status"] == "Pending" and r.json()["999999"] == {"error": "Incorrect order ID"}, r.text)
    r = await v2(key=key, action="status", order=o_hq["id"])   # juan's order
    check("api: can't see other customers' orders", r.json() == {"error": "Incorrect order ID"}, r.text)
    r = await v2(key=key, action="refill", order=oid)
    check("api: refill refused with the site's reason", r.json() == {"error": "This service has no refill"}, r.text)
    r = await v2(key=key, action="cancel", orders=str(oid))
    check("api: cancel answers per order", isinstance(r.json(), list) and r.json()[0]["order"] == oid, r.text)
    r = await v2(key=key, action="refill_status", refill=424242)
    check("api: refill_status unknown", r.json() == {"error": "Refill not found"}, r.text)
    r = await c3.post("/api/v2", json={"key": key, "action": "balance"})
    check("api: JSON body works too", r.json().get("currency") == "PHP", r.text)
    r = await v2(key=key, action="nope")
    check("api: unknown action", r.json() == {"error": "Incorrect action"}, r.text)
    await c3.post("/account/api-key")   # regenerate: the old key stops working
    check("api: regenerating revokes the old key", (await v2(key=key, action="balance")).json() == {"error": "Invalid API key"})
    await c3.aclose()

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
