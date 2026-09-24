import asyncio
from playwright.async_api import async_playwright
W = "http://localhost:8000"; OUT = "/var/tmp/smmtest/shots"
res = []
def check(n, c, i=""):
    res.append((n, bool(c))); print(("PASS " if c else "FAIL ") + n + (f"  [{i}]" if i and not c else ""))
async def bg(pg): return await pg.evaluate("getComputedStyle(document.body).backgroundColor")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        # system = dark
        ctx = await b.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
        pg = await ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(W + "/"); await pg.wait_for_selector("#price-body tr td >> text=TikTok")
        check("system dark → dark bg", await bg(pg) == "rgb(15, 17, 21)", await bg(pg))
        check("logo inverted in dark", "invert" in await pg.evaluate("getComputedStyle(document.querySelector('.logo-ink')).filter"))
        check("toggle label says System (dark)", "System (dark)" in (await pg.get_attribute("[data-theme-toggle]", "aria-label")))
        await pg.screenshot(path=f"{OUT}/dark-landing.png", full_page=True)
        # cycle: system → light → dark → system
        await pg.click("[data-theme-toggle]")
        check("toggle → Light overrides system", await bg(pg) == "rgb(246, 245, 241)" and await pg.evaluate("document.documentElement.dataset.theme") == "light", await bg(pg))
        await pg.click("[data-theme-toggle]")
        check("toggle → Dark", await pg.evaluate("document.documentElement.dataset.theme") == "dark" and await bg(pg) == "rgb(15, 17, 21)")
        # persists across pages, applied before scripts run (inline head script)
        await pg.goto(W + "/login/")
        check("dark persists to login (set by head script)", await pg.evaluate("document.documentElement.dataset.theme") == "dark")
        await pg.screenshot(path=f"{OUT}/dark-login.png")
        await pg.click("[data-theme-toggle]")
        check("toggle → System again", await pg.evaluate("document.documentElement.dataset.theme") is None and await pg.evaluate("localStorage.getItem('theme')") == "system")
        # live follow of device while on System
        await pg.emulate_media(color_scheme="light"); await pg.wait_for_timeout(200)
        check("System follows device change live", await bg(pg) == "rgb(246, 245, 241)", await bg(pg))
        await pg.emulate_media(color_scheme="dark"); await pg.wait_for_timeout(200)
        # dashboard in dark
        await pg.fill("#email", "dark@example.com"); await pg.fill("#password", "password123")
        await pg.click("#tab-register"); await pg.click("#submit")
        await pg.wait_for_url("**/dashboard/"); await pg.wait_for_selector("#order-form")
        check("dashboard dark", await bg(pg) == "rgb(15, 17, 21)")
        await pg.fill("#link", "https://tiktok.com/@x"); await pg.fill("#qty", "100")
        await pg.screenshot(path=f"{OUT}/dark-dashboard.png", full_page=True)
        await pg.click(".side-nav a:has-text('Add funds')"); await pg.wait_for_selector("#funds-form")
        await pg.screenshot(path=f"{OUT}/dark-funds.png")
        m = await b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, color_scheme="dark", storage_state=await ctx.storage_state())
        mp = await m.new_page(); await mp.goto(W + "/dashboard/#new"); await mp.wait_for_selector("#order-form")
        check("mobile topbar toggle visible", await mp.is_visible(".topbar [data-theme-toggle]"))
        sw = await mp.evaluate("document.documentElement.scrollWidth"); check("mobile no h-scroll", sw <= 390, sw)
        await mp.screenshot(path=f"{OUT}/dark-mobile.png")
        # light system, no preference → light
        l = await b.new_context(color_scheme="light"); lp = await l.new_page(); await lp.goto(W + "/")
        check("system light → light", await bg(lp) == "rgb(246, 245, 241)")
        check("no page errors", not errs, errs)
        await b.close()
    f = [n for n, ok in res if not ok]; print(f"{len(res)-len(f)}/{len(res)} passed", f or "")
asyncio.run(main())
