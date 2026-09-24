"""UI checks for the full catalog: category filter, search, capped lists, featured landing table.

Run after tests/e2e.py plus a catalog load (see tests/README.md); expects the app on :8000.
"""
import asyncio
import os
import subprocess
import uuid

from playwright.async_api import async_playwright

W = "http://localhost:8000"
OUT = os.environ.get("SHOTS", "/tmp")
res = []


def check(n, c, i=""):
    res.append((n, bool(c)))
    print(("PASS " if c else "FAIL ") + n + (f"  [{i}]" if i and not c else ""))


def psql(q):
    return subprocess.run(["psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "smm_test", "-tAc", q],
                          capture_output=True, text=True, env={**os.environ, "PGPASSWORD": "x"}).stdout.strip()


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1280, "height": 1000})
        pg = await ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # landing: hand-picked rows only
        await pg.goto(W + "/")
        await pg.wait_for_selector("#price-body tr td.mono")
        n_rows = await pg.locator("#price-body tr").count()
        check("landing shows only featured services", 0 < n_rows < 10, n_rows)

        email = f"cat{uuid.uuid4().hex[:6]}@example.com"
        await pg.goto(W + "/login/?mode=register")
        await pg.fill("#email", email)
        await pg.fill("#password", "password123")
        await pg.click("#submit")
        await pg.wait_for_url("**/dashboard/")
        await pg.wait_for_selector("#order-form")
        uid = psql(f"select id from users where email='{email}'")
        tid = str(uuid.uuid4())
        psql(f"insert into topups (id,user_id,amount_php,method,status,credited_at) values ('{tid}',{uid},1000,'gcash','credited',now())")
        psql(f"insert into ledger (user_id,delta,reason,ref) values ({uid},1000,'topup','{tid}')")
        await pg.reload()
        await pg.wait_for_selector("#order-form")

        check("category select present", await pg.locator("#svc-cat").count() == 1)
        first = await pg.eval_on_selector("#svc-cat", "e => e.options[e.selectedIndex].text")
        check("TikTok opens on Recommended", first.startswith("Recommended"), first)
        check("many platforms offered", await pg.locator("[data-platform]").count() >= 15)
        await pg.screenshot(path=f"{OUT}/cat-new.png", full_page=True)

        # the site's own dropdown, not the browser's
        await pg.click("#svc-cat-btn")
        check("custom dropdown opens its own list", await pg.is_visible("#svc-cat-list")
              and await pg.eval_on_selector("#svc-cat", "e => getComputedStyle(e).opacity") == "0")
        await pg.click('#svc-cat-list [role="option"]:has(.dd-text:text-is("Followers"))')
        check("picking an option sets the category", await pg.eval_on_selector("#svc-cat", "e => e.value") == "Followers"
              and not await pg.is_visible("#svc-cat-list") and "Followers" in await pg.inner_text("#svc-cat-btn"))
        await pg.focus("#svc-cat-btn")
        await pg.keyboard.press("ArrowDown")
        await pg.keyboard.press("Home")
        await pg.keyboard.press("Enter")
        check("dropdown works from the keyboard", await pg.eval_on_selector("#svc-cat", "e => e.selectedIndex") == 0
              and await pg.evaluate("document.activeElement.id") == "svc-cat-btn")
        await pg.select_option("#svc-cat", "Followers")
        cnt = await pg.locator("#svc-list .svc-option").count()
        hint = await pg.inner_text("#order-form .field:nth-of-type(3) .hint")
        check("list capped at 80 with a hint", cnt == 80 and "Showing 80 of" in hint, (cnt, hint))
        prices = await pg.eval_on_selector_all(
            "#svc-list .svc-option .p", "els => els.map(e => parseFloat(e.firstChild.textContent.replace(/[₱,]/g, '')))")
        featured_n = int(psql("select count(*) from services where not auto and active and platform='tiktok' and category='Followers'"))
        rest = prices[featured_n:]
        check("non-featured sorted cheapest first", rest == sorted(rest), rest[:10])

        await pg.evaluate("window.__search = document.getElementById('svc-search')")
        await pg.type("#svc-search", "brazil", delay=20)
        await pg.wait_for_timeout(200)
        check("typing never rebuilds the search box", await pg.evaluate("document.getElementById('svc-search') === window.__search"))
        names = await pg.eval_on_selector_all("#svc-list .svc-option", "els => els.map(e => e.textContent.toLowerCase())")
        check("search looks across categories", len(names) > 0 and all("brazil" in t for t in names), names[:5])
        check("category select disabled while searching", await pg.is_disabled("#svc-cat"))
        check("search keeps focus", await pg.evaluate("document.activeElement.id") == "svc-search")

        sid = psql("select id from services where auto and active and platform='tiktok' and category='Followers' order by id limit 1")
        await pg.fill("#svc-search", sid)
        await pg.wait_for_timeout(150)
        check("search by ID", await pg.locator(f'#svc-list [data-svc="{sid}"]').count() == 1)
        await pg.click(f'#svc-list [data-svc="{sid}"]')
        mn = int(psql(f"select ps.min_qty from services s join provider_services ps using (provider_id, provider_service_id) where s.id={sid}"))
        mx = int(psql(f"select ps.max_qty from services s join provider_services ps using (provider_id, provider_service_id) where s.id={sid}"))
        qty = min(max(mn, 1000), mx)
        await pg.fill("#link", "https://www.tiktok.com/@shiro/video/1")
        await pg.fill("#qty", str(qty))
        await pg.check("#ack")
        ui_charge = await pg.inner_text("#charge")
        await pg.click("#place")
        await pg.wait_for_url("**/dashboard/#orders")
        db_charge = psql(f"select price_php from orders where user_id={uid} order by id desc limit 1")
        check("auto service order charged what the form showed",
              float(ui_charge.replace("₱", "").replace(",", "")) == float(db_charge), (ui_charge, db_charge))

        # "Other" platform groups smaller sites by site name
        await pg.goto(W + "/dashboard/#new")
        await pg.wait_for_selector("#order-form")
        await pg.click('[data-platform="other"]')
        opts = await pg.eval_on_selector_all("#svc-cat option", "els => els.map(e => e.textContent)")
        check("Other platform lists sites as categories", any(o.startswith("VK") for o in opts), opts)

        # Philippines: a cross-category view of PH-tier services, only where there are any
        await pg.click('[data-platform="facebook"]')
        ph_opt = await pg.eval_on_selector_all("#svc-cat option", "els => els.map(e => [e.value, e.textContent])")
        check("Philippines category on Facebook", any(v == "__ph" and t.startswith("Philippines") for v, t in ph_opt), ph_opt)
        await pg.select_option("#svc-cat", "__ph")
        tiers = await pg.eval_on_selector_all("#svc-list .svc-option .badge", "els => els.map(e => e.textContent)")
        n_ph = int(psql("select count(*) from services where active and platform='facebook' and tier ~ '\\mPH\\M'"))
        check("Philippines lists every PH service and nothing else", len(tiers) == n_ph and all("PH" in t for t in tiers), (n_ph, tiers))
        await pg.click('[data-platform="telegram"]')
        opts = await pg.eval_on_selector_all("#svc-cat option", "els => els.map(e => e.value)")
        check("no Philippines category where there are no PH services", "__ph" not in opts, opts)

        # Non-drop: the provider's own non-drop claim, as a cross-category view
        await pg.click('[data-platform="instagram"]')
        await pg.select_option("#svc-cat", "__nondrop")
        tiers = await pg.eval_on_selector_all("#svc-list .svc-option .badge", "els => els.map(e => e.textContent)")
        n_nd = int(psql("select count(*) from services s join provider_services ps using (provider_id, provider_service_id) "
                        "where s.active and s.platform='instagram' and normalize(ps.name, NFKC) ~* '(non|no)[ -]?drop'"))
        check("Non-drop lists every non-drop service, tiered Non-drop (or PH)", n_nd > 0 and len(tiers) == min(n_nd, 80)
              and all(x in ("Non-drop", "PH", "Real · PH") for x in tiers) and "Non-drop" in tiers, (n_nd, tiers[:3]))
        await pg.click("#svc-list .svc-option")
        kv = await pg.inner_text(".details .kv")
        check("service info has a Non-drop line", "Non-drop" in kv and ("guaranteed" in kv or "No time limit stated" in kv), kv)
        await pg.click('[data-platform="discord"]')
        opts = await pg.eval_on_selector_all("#svc-cat option", "els => els.map(e => e.value)")
        check("no Non-drop category where there are none", "__nondrop" not in opts, opts)

        # mass order ID list
        await pg.goto(W + "/dashboard/#mass")
        await pg.wait_for_selector("#id-list .id-row")
        check("ID list capped", await pg.locator("#id-list .id-row").count() == 80
              and "Showing 80 of" in await pg.inner_text("#id-list"))
        await pg.fill("#id-search", "indonesia likes")
        await pg.wait_for_timeout(250)
        rows = await pg.eval_on_selector_all("#id-list .id-row", "els => els.map(e => e.textContent.toLowerCase())")
        check("ID list multi-word search", len(rows) > 0 and all("indonesia" in r and "like" in r for r in rows), rows[:5])
        await pg.screenshot(path=f"{OUT}/cat-mass.png", full_page=True)

        # phone width + dark theme
        m = await b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark",
                                storage_state=await ctx.storage_state())
        mp = await m.new_page()
        mp.on("pageerror", lambda e: errs.append(str(e)))
        await mp.goto(W + "/dashboard/#new")
        await mp.wait_for_selector("#order-form")
        await mp.select_option("#svc-cat", "Followers")
        overflow = await mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("no horizontal scroll on phone", overflow <= 0, overflow)
        await mp.screenshot(path=f"{OUT}/cat-phone-dark.png")
        check("no JS errors", not errs, errs)
        await b.close()

    print(f"\n{sum(ok for _, ok in res)}/{len(res)} passed")


asyncio.run(main())
