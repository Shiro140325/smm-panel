import asyncio, subprocess, uuid, httpx
from playwright.async_api import async_playwright

WEB = "http://localhost:8000"
MOCK = "http://127.0.0.1:9001"
OUT = "/var/tmp/smmtest/shots"
results, console_errors = [], []

def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))

def psql(sql):
    return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "smm6", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()

async def sync():
    import sys; sys.path.insert(0, "/home/claude/smm-panel")
    from app.workers.sync import run_sync_once
    await run_sync_once()

async def main():
    import os; os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        page.on("console", lambda m: m.type == "error" and "fonts.g" not in m.text and console_errors.append(m.text))
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        # landing
        await page.goto(WEB + "/")
        await page.wait_for_selector("#price-body tr td >> text=TikTok Followers")
        check("landing price table loads", await page.locator("#price-body tr").count() == 2)
        await page.click("#price-tabs button:has-text('Facebook')")
        check("landing tab switch", "Facebook Post Likes" in await page.inner_text("#price-body"))
        check("landing hero card", await page.locator("#hero-card .svc-row").count() >= 2)
        await page.screenshot(path=f"{OUT}/1-landing.png", full_page=True)

        # register
        await page.goto(WEB + "/login/?mode=register")
        await page.fill("#email", "juan@example.com"); await page.fill("#password", "short")
        await page.click("#submit")
        check("short password rejected client-side", "8 characters" in await page.inner_text("#auth-error"))
        await page.fill("#password", "password123"); await page.click("#submit")
        await page.wait_for_url("**/dashboard/")
        await page.wait_for_selector("#order-form")
        check("register → dashboard", True)
        check("balance shows 0", "₱0.00" in await page.inner_text(".balance-card"))

        # low balance state
        await page.click("[data-svc]:has-text('TikTok Followers') >> nth=1")
        await page.fill("#link", "https://www.tiktok.com/@juan"); await page.fill("#qty", "1000"); await page.check("#ack")
        check("low balance message + disabled", "Not enough balance" in await page.inner_text("#charge-msg") and await page.is_disabled("#place"))

        # credit ₱500 directly
        uid = psql("select id from users where email='juan@example.com'")
        tid = str(uuid.uuid4())
        psql(f"insert into topups (id,user_id,amount_php,method,status,credited_at) values ('{tid}',{uid},500,'paymongo','credited',now())")
        psql(f"insert into ledger (user_id,delta,reason,ref) values ({uid},500,'topup','{tid}')")
        await page.reload(); await page.wait_for_selector("#order-form")
        check("balance ₱500 after top-up", "₱500.00" in await page.inner_text(".balance-card"))

        # place HQ order
        await page.click("[data-svc]:has-text('HQ')")
        await page.fill("#link", "https://www.tiktok.com/@juan"); await page.fill("#qty", "50")
        await page.check("#ack")
        check("below-min message", "Minimum is 100" in await page.inner_text("#charge-msg"))
        await page.fill("#qty", "1000")
        charge = await page.inner_text("#charge")
        check("charge shown matches price", charge == "₱113.59", charge)
        await page.screenshot(path=f"{OUT}/2-new-order.png", full_page=True)
        await page.click("#place")
        await page.wait_for_url("**#orders")
        await page.wait_for_selector("#orders-body tr >> text=TikTok Followers")
        check("order appears in Orders", await page.locator("#orders-body tr").count() == 1)
        check("balance debited", "₱386.41" in await page.inner_text(".balance-card"), await page.inner_text(".balance-card"))

        # custom comments
        await page.click(".side-nav a:has-text('New order')")
        await page.click("[data-platform=instagram]")
        check("comments textarea shown", await page.locator("#comments").count() == 1 and await page.locator("#qty").count() == 0)
        await page.fill("#link", "https://instagram.com/p/abc")
        await page.fill("#comments", "Ganda!\n\n Solid 🔥 \nSaan mabibili?")
        check("comment count", "3 comments" in await page.inner_text("#qty-count"), await page.inner_text("#qty-count"))
        await page.check("#ack")
        await page.click("#place"); await page.wait_for_url("**#orders")
        last = httpx.get(MOCK + "/_last_add").json()
        check("comments sent to provider", last.get("comments") == "Ganda!\nSolid 🔥\nSaan mabibili?" and last.get("quantity") == 3, last)

        # complete HQ order at provider, sync, refill
        po = psql("select provider_order_id from orders o join services s on s.id=o.service_id where s.tier='HQ' and s.platform='tiktok'")
        httpx.post(MOCK + "/_set_order", data={"oid": po, "status": "Completed", "remains": "0"})
        await sync()
        await page.click("#refresh")
        await page.wait_for_selector("[data-refill]")
        await page.screenshot(path=f"{OUT}/3-orders.png", full_page=True)
        await page.click("[data-refill]")
        await page.wait_for_selector("#orders-body >> text=Refill requested")
        check("refill requested via UI", True)
        await page.click("[data-filter=completed]")
        await page.wait_for_timeout(500)
        check("filter completed", await page.locator("#orders-body tr").count() == 1)

        # funds
        await page.click(".side-nav a:has-text('Add funds')")
        await page.wait_for_selector("#funds-form")
        await page.click("[data-preset='1000']")
        check("funds summary", "₱1,000.00" in await page.inner_text("#sum-pay"))
        check("no method picker; button says Pay ₱1,000.00", await page.locator("[data-method]").count() == 0 and "Pay ₱1,000.00" in await page.inner_text("#pay"), await page.inner_text("#pay"))
        await page.fill("#amount", "50")
        check("funds min validation", await page.is_disabled("#pay") and "Minimum" in await page.inner_text("#amt-hint"))
        await page.fill("#amount", "500")
        await page.click("#pay")
        await page.wait_for_selector("#toast.show.bad")
        check("pay without PayMongo key → error toast", "not configured" in await page.inner_text("#toast"), await page.inner_text("#toast"))
        await page.screenshot(path=f"{OUT}/4-funds.png", full_page=True)
        await page.goto(WEB + f"/dashboard/#funds?status=success&topup={tid}")
        await page.wait_for_selector("#funds-banner.alert-ok", timeout=10000)
        check("return-from-checkout banner", "₱500.00 added" in await page.inner_text("#funds-banner"))

        # mobile
        m = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                      storage_state=await ctx.storage_state())
        mp = await m.new_page()
        mp.on("pageerror", lambda e: console_errors.append(str(e)))
        await mp.goto(WEB + "/dashboard/#new"); await mp.wait_for_selector("#order-form")
        check("mobile tabbar visible", await mp.is_visible(".tabbar") and not await mp.is_visible(".sidebar"))
        sw = await mp.evaluate("document.documentElement.scrollWidth")
        check("mobile no horizontal scroll (new)", sw <= 390, sw)
        await mp.screenshot(path=f"{OUT}/5-mobile-new.png", full_page=True)
        await mp.click(".tabbar a:has-text('Orders')"); await mp.wait_for_selector("#orders-body tr >> text=Instagram")
        sw = await mp.evaluate("document.documentElement.scrollWidth")
        check("mobile no horizontal scroll (orders)", sw <= 390, sw)
        await mp.screenshot(path=f"{OUT}/6-mobile-orders.png", full_page=True)
        await mp.goto(WEB + "/"); await mp.wait_for_selector("#price-body tr td >> text=TikTok")
        sw = await mp.evaluate("document.documentElement.scrollWidth")
        check("mobile no horizontal scroll (landing)", sw <= 390, sw)
        await mp.screenshot(path=f"{OUT}/7-mobile-landing.png", full_page=True)

        # logout
        await page.goto(WEB + "/dashboard/#new"); await page.wait_for_selector("#order-form")
        await page.click(".side-user [data-logout]")
        await page.wait_for_url("**/login/")
        await page.goto(WEB + "/dashboard/"); await page.wait_for_url("**/login/")
        check("logout + guard", True)
        await browser.close()

    print("console errors:", console_errors or "none")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results)-len(failed)}/{len(results)} passed", "FAILED: " + str(failed) if failed else "")

asyncio.run(main())
