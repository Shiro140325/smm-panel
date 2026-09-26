"""Public server-rendered pages: legal pages, platform price pages (/tiktok-followers/) and the sitemap."""
import asyncio
import time

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app import legal, seo_pages
from app.db import DB, get_db
from app.routers.services import _rows
from app.site import SITE, page

router = APIRouter(tags=["pages"], include_in_schema=False)

LEGAL = {
    "terms": ("Terms of service | SMM Shiro", "The terms for using SMM Shiro: accounts, wallet, orders, reseller API and referral program.", legal.terms),
    "refund-policy": ("Refund and refill policy | SMM Shiro", "When SMM Shiro refunds orders to your wallet, how refills work, and what happens to your balance.", legal.refund_policy),
    "privacy": ("Privacy policy | SMM Shiro", "What personal data SMM Shiro collects, why, who processes it, and your rights under the Data Privacy Act.", legal.privacy),
}
PAGE_TTL = 600   # platform pages: prices move slowly; rebuild at most every 10 minutes
_html: dict[str, tuple[float, str]] = {}
_lock = asyncio.Lock()


def _send(request: Request, body: str) -> Response:
    headers = {"Cache-Control": "public, max-age=300"}
    if request.method == "HEAD":
        return Response(headers=headers, media_type="text/html")
    return HTMLResponse(body, headers=headers)


async def _redirect(request: Request):
    """"/terms" → "/terms/" (the static mount would 404 these)."""
    return RedirectResponse(request.url.path + "/", status_code=301)


async def _public_page(request: Request, db: DB = Depends(get_db)):
    slug = request.url.path.strip("/")
    if slug in LEGAL:
        title, desc, body = LEGAL[slug]
        return _send(request, page(path=f"/{slug}/", title=title, description=desc, body=body()))
    hit = _html.get(slug)
    if hit is None or time.monotonic() - hit[0] >= PAGE_TTL:
        async with _lock:
            hit = _html.get(slug)
            if hit is None or time.monotonic() - hit[0] >= PAGE_TTL:
                p = seo_pages.PAGES[slug]
                body, jsonld, low = seo_pages.render(slug, await _rows(db, p["platform"], False))
                html = page(path=f"/{slug}/", title=seo_pages.title(p, low), description=seo_pages.description(p, low),
                            body=body, jsonld=jsonld)
                hit = _html[slug] = (time.monotonic(), html)
    return _send(request, hit[1])


# explicit paths only, so nothing else ("/login", "/health") is shadowed
for _slug in [*LEGAL, *seo_pages.PAGES]:
    router.add_api_route(f"/{_slug}/", _public_page, methods=["GET", "HEAD"])
    router.add_api_route(f"/{_slug}", _redirect, methods=["GET", "HEAD"])


@router.get("/sitemap.xml")
async def sitemap():
    day = time.strftime("%Y-%m-%d")
    urls = [("/", "1.0", "weekly")] + [(f"/{s}/", "0.8", "daily") for s in seo_pages.PAGES] \
        + [(f"/{s}/", "0.3", "yearly") for s in LEGAL]
    items = "".join(f"<url><loc>{SITE}{u}</loc><lastmod>{day}</lastmod><changefreq>{f}</changefreq>"
                    f"<priority>{p}</priority></url>\n" for u, p, f in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}</urlset>\n'
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})
