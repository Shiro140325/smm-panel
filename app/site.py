"""Shared shell (head, header, footer) for server-rendered public pages: legal pages and platform price pages."""
import html
import json
import os
import time

# Every deploy gets a new asset version, stamped onto script/stylesheet URLs
# ("/assets/app.css" → "/assets/app.css?v=<commit>"), so no browser or CDN cache can serve an old file.
ASSET_VERSION = (os.environ.get("RENDER_GIT_COMMIT") or str(int(time.time())))[:10]
SITE = "https://smmshiro.com"
FONTS = ("https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700"
         "&family=Figtree:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap")

e = html.escape

FOOTER = """<footer class="site-footer">
  <div class="wrap">
    <div class="footer-col" style="flex:1;min-width:240px">
      <span class="brand"><img src="/assets/img/logo-white.png" alt="SMM Shiro" width="132" height="44"></span>
      <!--email_off--><a href="mailto:support@smmshiro.com">support@smmshiro.com</a><!--/email_off-->
      <span style="font-size:13px;color:var(--dark-muted)">© 2026 SMM Shiro. Not affiliated with TikTok, Meta, Google or X.</span>
    </div>
    <nav class="footer-col" aria-label="Footer">
      <a href="/#pricing">Services</a><a href="/#faq">FAQ</a><a href="/login/">Log in</a>
    </nav>
    <nav class="footer-col" aria-label="Popular services">
      {popular}
    </nav>
    <nav class="footer-col" aria-label="Legal">
      <a href="/terms/">Terms of service</a><a href="/refund-policy/">Refund and refill policy</a><a href="/privacy/">Privacy policy</a>
    </nav>
  </div>
</footer>"""


def footer_popular() -> str:
    from app.seo_pages import PAGES   # lazy: seo_pages imports this module
    return "".join(f'<a href="/{slug}/">{e(p["h1_short"])}</a>' for slug, p in list(PAGES.items())[:6])


def page(*, path: str, title: str, description: str, body: str, jsonld: list | None = None,
         og_type: str = "website", noindex: bool = False) -> str:
    url = SITE + path
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>\n' if jsonld else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
{'<meta name="robots" content="noindex">' if noindex else f'<link rel="canonical" href="{url}">'}
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="SMM Shiro">
<meta property="og:locale" content="en_PH">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:image" content="{SITE}/assets/img/og-image.png">
<meta name="twitter:card" content="summary_large_image">
{ld}<meta name="theme-color" content="#F4F4F4">
<script>/* apply saved theme before first paint */(function(){{try{{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}}catch(e){{}}}})();</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="icon" href="/favicon.ico?v=3" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/img/favicon-32.png?v=3">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png?v=3">
<link rel="manifest" href="/site.webmanifest">
<link rel="stylesheet" href="/assets/app.css?v={ASSET_VERSION}">
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="/" aria-label="SMM Shiro home"><img class="logo-ink" src="/assets/img/logo.png" alt="SMM Shiro" width="132" height="44"></a>
    <nav class="site-nav" aria-label="Main">
      <a href="/#services">How it works</a>
      <a href="/#pricing">Pricing</a>
      <a href="/#faq">FAQ</a>
    </nav>
    <div class="header-actions">
      <button class="theme-btn" type="button" data-theme-toggle aria-label="Theme"></button>
      <a class="btn btn-ghost" href="/login/">Log in</a>
      <a class="btn btn-primary" href="/login/?mode=register">Create account</a>
    </div>
  </div>
</header>
<main>
{body}
</main>
{FOOTER.replace("{popular}", footer_popular())}
<script type="module" src="/assets/page.js?v={ASSET_VERSION}"></script>
</body>
</html>
"""


def not_found_page() -> str:
    from app.seo_pages import PAGES
    links = "".join(f'<a class="pill" href="/{slug}/">{e(p["h1_short"])}</a>' for slug, p in PAGES.items())
    body = f"""<section class="block nf">
  <div class="wrap nf-wrap">
    <span class="nf-code" aria-hidden="true">404</span>
    <h1>This page doesn't exist.</h1>
    <p class="lead">The link may be old or mistyped. Everything you need is still a tap away.</p>
    <div class="hero-cta nf-cta">
      <a class="btn btn-primary btn-lg" href="/">Go to home page</a>
      <a class="btn btn-secondary btn-lg" href="/dashboard/">Open dashboard</a>
    </div>
    <div class="nf-more">
      <span class="kicker">Popular services</span>
      <div class="price-tabs">{links}</div>
    </div>
  </div>
</section>"""
    return page(path="/404", title="Page not found | SMM Shiro",
                description="This page doesn't exist. Go to the SMM Shiro home page or your dashboard.",
                body=body, noindex=True)
