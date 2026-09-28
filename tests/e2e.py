import asyncio
import hashlib
import hmac
import json
import os
import re
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


async def verify_all():
    """Accounts in the tests below are used right after sign-up: mark them verified (the flow itself is tested later)."""
    await sql("update users set email_verified_at = now() where email_verified_at is null")


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
    # peso markup brackets: costs ₱29, ₱58 and ₱69.60 per 1K
    check("auto services priced with tiered markup", sorted(s["price_per_1k_php"] for s in r.json()) == [37.98, 72.26, 85.95], r.text)
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
    check("no free credit at sign-up (free trial order instead)", "welcome_php" not in r.json() and "trial" in r.json(), r.text)
    check("verify: sign-up says a code is needed", r.json()["verify_required"] is True and r.json()["email_verified"] is False, r.text)
    r = await c.get("/auth/me")
    check("verify: unverified account can still read /auth/me", r.status_code == 200 and r.json()["email_verified"] is False, r.text)
    locked = [(await c.get(u)).status_code for u in ("/orders", "/topups", "/account/affiliate")] + [
        (await c.post("/orders", json={"service_id": 1, "link": "https://x.com/a", "quantity": 100})).status_code]
    check("verify: everything else is locked until verified", locked == [403, 403, 403, 403], locked)
    await verify_all()
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
    await verify_all()
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

    # --- customer tiers: Member → Pro (₱10k) → Elite (₱25k), website spending after refunds, kept forever
    ct = httpx.AsyncClient(base_url=API)
    tu = (await ct.post("/auth/register", json={"email": "tier@example.com", "password": "password123"})).json()["id"]
    await verify_all()
    await sql("insert into ledger (user_id, delta, reason, ref) values (:u, 50000, 'adjustment', 'test')", {"u": tu})
    t = (await ct.get("/auth/me")).json()["tier"]
    check("tier: new account is Member, card not seen yet", t["name"] == "member" and t["seen"] is None
          and t["discount_pct"] == 0 and t["next"] == {"name": "pro", "at_php": 10000}, t)
    await sql("""insert into orders (user_id, service_id, provider_id, link, quantity, price_php, status, source)
                 values (:u, 2, 1, 'https://t/a', 1000, 9950, 'completed', 'web'),
                        (:u, 2, 1, 'https://t/api', 1000, 90000, 'completed', 'api')""", {"u": tu})
    t = (await ct.get("/auth/me")).json()["tier"]
    check("tier: API orders don't count toward the tier", t["name"] == "member" and t["spent_php"] == 9950, t)
    r = await ct.post("/orders", json={"service_id": 2, "link": "https://tiktok.com/@t", "quantity": 1000})
    check("tier: Member pays full price", r.json().get("charge_php") == 111.36, r.text)
    t = (await ct.get("/auth/me")).json()["tier"]
    check("tier: reaching ₱10,000 makes Pro (3% off, 6% referral)", t["name"] == "pro" and t["discount_pct"] == 3
          and t["referral_pct"] == 6 and t["next"]["name"] == "elite", t)
    r = await ct.post("/orders", json={"service_id": 2, "link": "https://tiktok.com/@t", "quantity": 1000})
    check("tier: Pro website order charged 3% less (₱111.36 → ₱108.02)", r.json().get("charge_php") == 108.02, r.text)
    r = await ct.post("/orders/mass", json={"orders": "2|https://tiktok.com/@m1|1000\n2|https://tiktok.com/@m2|1000"})
    check("tier: mass orders get the discount too", r.json().get("charged_php") == 216.04, r.text)
    key = (await ct.post("/account/api-key")).json()["key"]
    r = await ct.post("/api/v2", data={"key": key, "action": "add", "service": 2, "link": "https://tiktok.com/@api", "quantity": 1000})
    api_price = await sql("select price_php, source from orders where id = :i", {"i": r.json().get("order")})
    check("tier: reseller API orders get no discount", float(api_price[0]["price_php"]) == 111.36 and api_price[0]["source"] == "api", api_price)
    check("tier: card for the new tier not seen yet", (await ct.get("/auth/me")).json()["tier"]["seen"] is None)
    r = await ct.post("/account/tier-seen")
    check("tier: closing the card is remembered", r.json() == {"seen": "pro"} and (await ct.get("/auth/me")).json()["tier"]["seen"] == "pro", r.text)
    await sql("insert into ledger (user_id, delta, reason, ref) select :u, price_php, 'refund', id::text from orders where user_id = :u and price_php = 9950", {"u": tu})
    t = (await ct.get("/auth/me")).json()["tier"]
    check("tier: kept forever even if refunds bring spending back under", t["name"] == "pro" and t["spent_php"] < 10000, t)
    users = (await ca.get("/admin/api/users", params={"q": "tier@example.com"})).json()
    check("tier: shown in the control panel", users and users[0]["tier"] == "pro", users)
    await sql("""insert into orders (user_id, service_id, provider_id, link, quantity, price_php, status, source)
                 values (:u, 2, 1, 'https://t/b', 1000, 30000, 'completed', 'web')""", {"u": tu})
    t = (await ct.get("/auth/me")).json()["tier"]
    check("tier: ₱25,000 makes Elite (5% off, 7% referral, +2% top-up bonus)", t["name"] == "elite" and t["discount_pct"] == 5
          and t["referral_pct"] == 7 and t["topup_bonus_pct"] == 2 and t["next"] is None and t["seen"] == "pro", t)
    bal0 = (await ct.get("/auth/me")).json()["balance_php"]
    for amount in (1000, 999):
        tid = str(uuid.uuid4())
        await sql("insert into topups (id, user_id, amount_php, method) values (CAST(:id AS uuid), :u, :a, 'paymongo')", {"id": tid, "u": tu, "a": amount})
        raw, hdr = signed(paid_event(tid, amount))
        await c.post("/webhooks/paymongo", content=raw, headers=hdr)
        await c.post("/webhooks/paymongo", content=raw, headers=hdr)   # replay
    bal1 = (await ct.get("/auth/me")).json()["balance_php"]
    check("tier: Elite +2% bonus on a ₱1,000 top-up only, once", round(bal1 - bal0, 2) == 1000 + 20 + 999, (bal0, bal1))
    code = (await ct.get("/account/affiliate")).json()
    check("tier: affiliate page shows the tier's rate", code["pct"] == 7, code)
    cf = httpx.AsyncClient(base_url=API)
    fid2 = (await cf.post("/auth/register", json={"email": "friend2@example.com", "password": "password123", "ref": code["code"]})).json()["id"]
    await verify_all()
    tid = str(uuid.uuid4())
    await sql("insert into topups (id, user_id, amount_php, method) values (CAST(:id AS uuid), :u, 500, 'paymongo')", {"id": tid, "u": fid2})
    raw, hdr = signed(paid_event(tid, 500))
    await c.post("/webhooks/paymongo", content=raw, headers=hdr)
    com = await sql("select delta from ledger where reason = 'referral' and ref = :r", {"r": tid})
    check("tier: an Elite referrer earns 7%", com and float(com[0]["delta"]) == 35, com)
    await ct.aclose(); await cf.aclose()

    # --- announcement bar
    check("announcement: none by default", (await c.get("/announcement")).json()["text"] == "")
    r = await ca.put("/admin/api/announcement", json={"text": "  New:  ₱15 free credit\n on sign-up!  "})
    check("announcement: saved as one tidy line", r.json()["text"] == "New: ₱15 free credit on sign-up!" and r.json()["max_chars"] == 140, r.text)
    check("announcement: everyone sees it right away", (await c.get("/announcement")).json()["text"] == "New: ₱15 free credit on sign-up!")
    r = await ca.put("/admin/api/announcement", json={"text": "x" * 141})
    check("announcement: over 140 characters refused", r.status_code == 422, r.status_code)
    r = await c.put("/admin/api/announcement", json={"text": "hacked"})
    check("announcement: customers can't change it", r.status_code == 401 and (await c.get("/announcement")).json()["text"].startswith("New:"), r.status_code)
    await ca.put("/admin/api/announcement", json={"text": ""})
    check("announcement: empty removes the bar", (await c.get("/announcement")).json()["text"] == "")
    r = await httpx.AsyncClient(base_url=API).get("/announcement")
    check("announcement: only for logged-in customers", r.status_code == 401, r.status_code)

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
    await verify_all()
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

    # --- fixed peso price per service (overrides the markup while it stays above cost)
    base = next(x for x in (await c.get("/services")).json() if x["id"] == 1)["price_per_1k_php"]
    await sql("update services set price_php = 40 where id = 1")   # cost: $0.50 × 58 = ₱29
    s1 = next(x for x in (await c.get("/services")).json() if x["id"] == 1)
    check("fixed price shown instead of the markup price", s1["price_per_1k_php"] == 40.0, s1)
    r = await c.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@fixed", "quantity": 1000})
    check("fixed price charged", r.status_code == 200 and r.json()["charge_php"] == 40.0, r.text)
    await sql("update services set price_php = 30 where id = 1")   # under cost + 5%
    s1 = next(x for x in (await c.get("/services")).json() if x["id"] == 1)
    check("fixed price below cost is ignored (markup price instead)", s1["price_per_1k_php"] == base != 30, (s1, base))
    await sql("update services set price_php = null where id = 1")

    # --- referral program
    aff = (await c.get("/account/affiliate")).json()
    check("affiliate: code and link", aff["code"] and aff["link"].endswith("/?ref=" + aff["code"]) and aff["pct"] == 5, aff)
    check("affiliate: same code next time", (await c.get("/account/affiliate")).json()["code"] == aff["code"])
    c3 = httpx.AsyncClient(base_url=API)
    r = await c3.post("/auth/register", json={"email": "friend@example.com", "password": "password123", "ref": aff["code"].upper()})
    fid = r.json()["id"]
    c4 = httpx.AsyncClient(base_url=API)
    await c4.post("/auth/register", json={"email": "stranger@example.com", "password": "password123", "ref": "nosuchcode"})
    await verify_all()
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

    # --- free trial order: up to 100 on service 237, once per account and per link, no balance needed
    await sql("insert into provider_services (provider_id, provider_service_id, name, category, type, rate, min_qty, max_qty) "
              "values (1, 9001, 'Facebook Post Reaction', 'Facebook Reactions', 'Default', 0.001, 10, 100000)")
    tsid = (await sql("insert into services (id, provider_id, provider_service_id, platform, category, name, tier) "
                      "values (237, 1, 9001, 'facebook', 'Reactions', 'Facebook Post Reaction', 'Basic') returning id"))[0]["id"]
    def dev_client(dev, ip):
        return httpx.AsyncClient(base_url=API, headers={"X-Device": dev * 32, "cf-connecting-ip": ip})
    async def last_code(email):
        mails = (await httpx.AsyncClient(base_url=MOCK).get("/_emails", params={"to": email})).json()
        return re.search(r"\b(\d{6})\b", mails[-1]["text"]).group(1) if mails else None
    async def verify(cl, email):
        await cl.post("/auth/verify/send")
        return await cl.post("/auth/verify", json={"code": await last_code(email)})
    cn = dev_client("a", "10.9.0.1")
    r = await cn.post("/auth/register", json={"email": "trial@example.com", "password": "password123"})
    check("trial: locked until the email is verified", r.json()["trial"] == {"service_id": tsid, "quantity": 100, "available": False, "needs_verify": True}
          and r.json()["verify_required"] and not r.json()["email_verified"], r.text)
    r = await cn.post("/orders", json={"service_id": tsid, "link": "https://tiktok.com/@t/video/1", "quantity": 100})
    check("trial: can't order at all before verifying", r.status_code == 403 and "Verify your email" in r.text, r.text)
    # --- email verification
    r = await cn.post("/auth/verify", json={"code": "123456"})
    check("verify: asks for a code first", r.status_code == 400 and "Ask for a code" in r.text, r.text)
    r = await cn.post("/auth/verify/send")
    mail = (await httpx.AsyncClient(base_url=MOCK).get("/_emails", params={"to": "trial@example.com"})).json()
    check("verify: code emailed from noreply@smmshiro.com", r.status_code == 200 and len(mail) == 1
          and mail[0]["from"] == "SMM Shiro <noreply@smmshiro.com>" and re.search(r"\b\d{6}\b", mail[0]["subject"]), (r.text, mail))
    r = await cn.post("/auth/verify/send")
    check("verify: one code per minute", r.status_code == 429 and "Wait" in r.text, r.text)
    code = await last_code("trial@example.com")
    wrong = f"{(int(code) + 1) % 1000000:06d}"
    r = await cn.post("/auth/verify", json={"code": wrong})
    check("verify: wrong code counted", r.status_code == 400 and "4 tries left" in r.text, r.text)
    r = await cn.post("/auth/verify", json={"code": wrong})
    check("verify: wrong tries persist", r.status_code == 400 and "3 tries left" in r.text, r.text)
    await sql("update email_codes set expires_at = now() - interval '1 minute' where user_id = (select id from users where email = 'trial@example.com')")
    r = await cn.post("/auth/verify", json={"code": code})
    check("verify: expired code refused", r.status_code == 400 and "expired" in r.text, r.text)
    await sql("update email_codes set expires_at = now() + interval '10 minutes' where user_id = (select id from users where email = 'trial@example.com')")
    r = await cn.post("/auth/verify", json={"code": f"{code[:3]} {code[3:]}"})
    check("verify: right code (spaces ignored) unlocks the trial", r.status_code == 200 and r.json()["email_verified"]
          and r.json()["trial"]["available"] is True, r.text)
    check("verify: code deleted and account marked", (await sql("select count(*) n from email_codes"))[0]["n"] == 0
          and (await cn.get("/auth/me")).json()["email_verified"] is True)
    r = await cn.post("/auth/verify/send")
    check("verify: no new code once verified", r.status_code == 409, r.text)
    cb = dev_client("x", "10.9.0.9")
    await cb.post("/auth/register", json={"email": "bounce@example.com", "password": "password123"})
    r = await cb.post("/auth/verify/send")
    check("verify: sending failure is reported, not hidden", r.status_code == 502, r.text)
    check("verify: a failed send leaves no code behind", (await sql(
        "select count(*) n from email_codes where user_id = (select id from users where email = 'bounce@example.com')"))[0]["n"] == 0)
    await cb.aclose()
    cd = dev_client("y", "10.9.0.8")
    await cd.post("/auth/register", json={"email": "dead@example.com", "password": "password123"})
    await cd.post("/auth/verify/send")
    await sql("update email_codes set attempts = 5 where user_id = (select id from users where email = 'dead@example.com')")
    r = await cd.post("/auth/verify", json={"code": await last_code("dead@example.com")})
    check("verify: after 5 wrong tries even the right code is refused", r.status_code == 429, r.text)
    await cd.aclose()
    ce = dev_client("z", "10.9.0.7")
    await ce.post("/auth/register", json={"email": "typo@exmaple.com", "password": "password123"})
    await ce.post("/auth/verify/send")
    r = await ce.post("/auth/verify/email", json={"email": "trial@example.com"})
    check("verify: can't switch to an email that has an account", r.status_code == 409, r.text)
    r = await ce.post("/auth/verify/email", json={"email": "Typo@Example.com"})
    check("verify: mistyped email can be fixed, code goes to the new address", r.status_code == 200 and r.json()["email"] == "typo@example.com"
          and await last_code("typo@example.com") and (await ce.get("/auth/me")).json()["email"] == "typo@example.com", r.text)
    r = await ce.post("/auth/verify", json={"code": await last_code("typo@example.com")})
    check("verify: the new address's code works", r.status_code == 200, r.text)
    r = await ce.post("/auth/verify/email", json={"email": "other2@example.com"})
    check("verify: email can't be changed once verified", r.status_code == 409, r.text)
    r = await ce.post("/auth/login", json={"email": "typo@example.com", "password": "password123"})
    check("verify: login says whether the account is verified", r.json()["email_verified"] is True, r.text)
    # --- account settings: password change (current password + emailed code), other devices logged out
    other = dev_client("z", "10.9.0.7")
    await other.post("/auth/login", json={"email": "typo@example.com", "password": "password123"})
    await asyncio.sleep(1.1)   # sessions carry whole-second issue times
    r = await ce.post("/account/password/code", json={"current_password": "wrongpass1"})
    check("settings: password code needs the current password", r.status_code == 400 and "current password" in r.text, r.text)
    r = await ce.post("/account/password/code", json={"current_password": "password123"})
    check("settings: password code emailed", r.status_code == 200 and r.json()["sent_to"].endswith("@example.com")
          and "password" in (await httpx.AsyncClient(base_url=MOCK).get("/_emails", params={"to": "typo@example.com"})).json()[-1]["subject"], r.text)
    pcode = await last_code("typo@example.com")
    r = await ce.post("/account/password", json={"current_password": "password123", "new_password": "newpass456", "code": f"{(int(pcode) + 1) % 1000000:06d}"})
    check("settings: wrong password code refused and counted", r.status_code == 400 and "4 tries left" in r.text, r.text)
    r = await ce.post("/account/password", json={"current_password": "password123", "new_password": "newpass456", "code": pcode})
    check("settings: password changed with the code", r.status_code == 200, r.text)
    check("settings: this device stays logged in", (await ce.get("/account/settings")).status_code == 200)
    r = await other.get("/auth/me")
    check("settings: other devices are logged out", r.status_code == 401 and "password was changed" in r.text, r.text)
    await other.aclose()
    r = await httpx.AsyncClient(base_url=API).post("/auth/login", json={"email": "typo@example.com", "password": "newpass456"},
                                                   headers={"cf-connecting-ip": "10.9.0.77"})
    check("settings: new password works", r.status_code == 200, r.text)
    r = await ce.post("/account/password", json={"current_password": "newpass456", "new_password": "another789", "code": pcode})
    check("settings: a password code works once", r.status_code == 400, r.text)
    # --- email change: codes to the old and the new address, both required
    r = await ce.post("/account/email/start", json={"new_email": "trial@example.com"})
    check("settings: can't take another account's email", r.status_code == 409, r.text)
    r = await ce.post("/account/email/start", json={"new_email": "Moved@Example.com"})
    check("settings: email change sends two codes", r.status_code == 200 and r.json()["new_to"] == "moved@example.com", r.text)
    old_c, new_c = await last_code("typo@example.com"), await last_code("moved@example.com")
    r = await ce.post("/account/email", json={"old_code": new_c, "new_code": old_c} if old_c != new_c else {"old_code": "000000", "new_code": new_c})
    check("settings: both codes must match their address", r.status_code == 400 and "current email" in r.text, r.text)
    check("settings: nothing changed yet", (await ce.get("/auth/me")).json()["email"] == "typo@example.com")
    r = await ce.post("/account/email", json={"old_code": old_c, "new_code": new_c})
    me_e = (await ce.get("/auth/me")).json()
    check("settings: email changed and verified", r.status_code == 200 and me_e["email"] == "moved@example.com" and me_e["email_verified"], (r.text, me_e))
    r = await httpx.AsyncClient(base_url=API).post("/auth/login", json={"email": "typo@example.com", "password": "newpass456"},
                                                   headers={"cf-connecting-ip": "10.9.0.78"})
    check("settings: the old email no longer logs in", r.status_code == 401, r.text)
    await ce.aclose()
    # --- the trial itself
    r = await cn.post("/orders", json={"service_id": tsid, "link": "https://tiktok.com/@t/video/1", "quantity": 101})
    check("trial: more than 100 isn't free", r.status_code == 402, r.text)
    r = await cn.post("/orders", json={"service_id": 1, "link": "https://tiktok.com/@t/video/1", "quantity": 100})
    check("trial: other services aren't free", r.status_code == 402, r.text)
    r = await cn.post("/orders", json={"service_id": tsid, "link": "https://tiktok.com/@t/video/1", "quantity": 100})
    check("trial: 100 free with no balance", r.status_code == 200 and r.json()["charge_php"] == 0 and r.json()["free_trial"], r.text)
    me_t = (await cn.get("/auth/me")).json()
    check("trial: used up, balance untouched, not counted toward tiers", me_t["trial"]["available"] is False
          and me_t["balance_php"] == 0 and me_t["tier"]["spent_php"] == 0, me_t)
    r = await cn.post("/orders", json={"service_id": tsid, "link": "https://tiktok.com/@t/video/2", "quantity": 100})
    check("trial: only once per account", r.status_code == 402, r.text)
    cn2 = dev_client("b", "10.9.0.2")
    await cn2.post("/auth/register", json={"email": "trial2@example.com", "password": "password123"})
    await verify(cn2, "trial2@example.com")
    r = await cn2.post("/orders", json={"service_id": tsid, "link": "https://TIKTOK.com/@t/video/1", "quantity": 100})
    check("trial: only once per link, even from another account", r.status_code == 400 and "already had a free trial" in r.text, r.text)
    await sql("update users set trial_used_at = now() where email = 'trial2@example.com'")
    check("trial: accounts from before the trial don't get it", (await cn2.get("/auth/me")).json()["trial"]["available"] is False)
    cn3 = dev_client("a", "10.9.0.3")   # same device, new account and network
    r = await cn3.post("/auth/register", json={"email": "trial3@example.com", "password": "password123"})
    check("trial: one per device, even on a new account", r.json()["trial"]["available"] is False and not r.json()["trial"]["needs_verify"], r.text)
    await verify(cn3, "trial3@example.com")
    r = await cn3.post("/orders", json={"service_id": tsid, "link": "https://tiktok.com/@t/video/3", "quantity": 100})
    check("trial: same device can't order it free, even verified", r.status_code == 402, r.text)
    cn4 = dev_client("c", "10.9.0.1")   # new device, same network within 30 days
    r = await cn4.post("/auth/register", json={"email": "trial4@example.com", "password": "password123"})
    check("trial: one per network for 30 days", r.json()["trial"]["available"] is False and not r.json()["trial"]["needs_verify"], r.text)
    cn5 = dev_client("d", "10.9.0.5")
    r = await cn5.post("/auth/register", json={"email": "trial5@example.com", "password": "password123"})
    check("trial: a new device on a new network can get it", r.json()["trial"]["needs_verify"] is True, r.text)
    await verify(cn5, "trial5@example.com")
    r = await cn5.post("/orders", json={"service_id": tsid, "link": "https://tiktok.com/@t/video/5", "quantity": 50})
    check("trial: less than 100 is free too; device and network recorded", r.status_code == 200 and (await sql(
        "select trial_device, trial_ip from users where email = 'trial5@example.com'"))[0] == {"trial_device": "d" * 32, "trial_ip": "10.9.0.5"}, r.text)
    await sql("update users set trial_used_at = now() - interval '31 days' where email = 'trial@example.com'")
    r = await dev_client("e", "10.9.0.1").post("/auth/register", json={"email": "trial6@example.com", "password": "password123"})
    check("trial: network block expires after 30 days", r.json()["trial"]["needs_verify"] is True, r.text)
    for x in (cn, cn2, cn3, cn4, cn5): await x.aclose()

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
