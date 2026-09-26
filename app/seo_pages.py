"""Server-rendered landing pages per platform + service ("/tiktok-followers/") for search.

Each page has its own title, copy and FAQ, and a price table built from the live catalog, so
what search engines index matches what customers can actually order.
"""
from app.site import SITE, e

# slug → page. Copy is specific per page; the price table and "from ₱X" come from the catalog.
PAGES: dict[str, dict] = {
    "tiktok-followers": dict(
        platform="tiktok", category="Followers", h1_short="TikTok followers",
        h1="Buy TikTok followers in the Philippines",
        intro="Grow your TikTok profile with followers from the Philippines and worldwide. Every service shows its quality, start time, drop risk and refill terms before you pay, so you know exactly what you're getting.",
        faqs=[
            ("Will buying TikTok followers get my account banned?", "Orders only need your public profile link, never your password. Followers arrive gradually on most services. Platforms can still remove engagement they flag, which is why we show each service's drop risk and refill period."),
            ("Do you have Filipino TikTok followers?", "Yes. Services tagged PH deliver from Philippine accounts. Pick them from the Philippines category on the order page."),
            ("How long do TikTok followers take?", "Each service lists its usual start time and speed. Most start within minutes to a few hours."),
        ]),
    "tiktok-likes": dict(
        platform="tiktok", category="Likes", h1_short="TikTok likes",
        h1="Buy TikTok likes in the Philippines",
        intro="Add likes to your TikTok videos from ₱ per thousand, paid by QR Ph. Choose by quality and drop risk; services with refill are topped up if likes drop during the refill period.",
        faqs=[
            ("Can I split likes across several videos?", "Each order is for one video link. Use Mass order to send the same service to many videos at once, one line per video."),
            ("Do TikTok likes drop?", "Some do. Every service shows its drop risk (Lowest, Moderate or Likely) and whether it has refill before you order."),
            ("Does my video need to be public?", "Yes. Keep the video public and don't change the link until the order completes."),
        ]),
    "tiktok-views": dict(
        platform="tiktok", category="Views", h1_short="TikTok views",
        h1="Buy TikTok views in the Philippines",
        intro="Cheap TikTok video views that start fast, for new posts and ads-ready content. Order thousands at a time and track delivery from your dashboard.",
        faqs=[
            ("How fast do TikTok views arrive?", "Views are the fastest service; most start within minutes. The exact start time and speed are shown on each service."),
            ("What's the minimum order?", "It depends on the service, and it's shown in the table below. Many view services start at a few hundred."),
            ("Can I order views for a live stream?", "Yes, live stream views are a separate category on the order page."),
        ]),
    "facebook-followers": dict(
        platform="facebook", category="Followers", h1_short="Facebook followers",
        h1="Buy Facebook page and profile followers in the Philippines",
        intro="Followers for Facebook pages and profiles, including Philippine-tagged services. Built for sellers, creators and small businesses who want their page to look established.",
        faqs=[
            ("Does this work for Facebook pages and personal profiles?", "Yes. Each service says whether it's for pages, profiles or both. Use the public link to the page or profile."),
            ("Do you have Filipino Facebook followers?", "Yes. PH-tagged services deliver from Philippine accounts; find them in the Philippines category."),
            ("Will my page lose followers?", "Some drop over time. Check the drop risk and refill period on each service; refill tops them back up within the period."),
        ]),
    "facebook-likes": dict(
        platform="facebook", category="Likes", h1_short="Facebook likes",
        h1="Buy Facebook likes in the Philippines",
        intro="Likes for Facebook posts, photos, videos and pages, paid in pesos. Compare quality and refill terms side by side, then order in under a minute.",
        faqs=[
            ("Post likes or page likes?", "Both are available. The service name says which one it is: use a post link for post likes and a page link for page likes."),
            ("Can I choose reactions like Love or Haha?", "Yes, reaction services are in the Reactions category on the order page."),
            ("How do I pay?", "Top up your wallet by QR Ph with GCash, Maya or your bank app, then order from your balance."),
        ]),
    "instagram-followers": dict(
        platform="instagram", category="Followers", h1_short="Instagram followers",
        h1="Buy Instagram followers in the Philippines",
        intro="Instagram followers with clear labels: quality tier, start time, drop risk and refill period on every service. Non-drop options are marked so you can pick the most stable ones.",
        faqs=[
            ("Is my Instagram account safe?", "We only need your public profile link, never your password. Keep the account public while the order runs."),
            ("What does non-drop mean?", "The supplier states these followers should not drop within a stated period. They're tagged Non-drop and show how many days are guaranteed."),
            ("Can I order for a private account?", "No. The account must be public until the order completes, or it may finish without full delivery."),
        ]),
    "instagram-likes": dict(
        platform="instagram", category="Likes", h1_short="Instagram likes",
        h1="Buy Instagram likes in the Philippines",
        intro="Instagram likes for posts and reels, starting within minutes on most services. Order one post at a time or many with Mass order.",
        faqs=[
            ("Do likes work on reels?", "Yes. Use the link to the reel or post."),
            ("Do Instagram likes drop?", "Some can. Each service shows its drop risk and whether refill is included."),
            ("What happens if an order is only partly delivered?", "The undelivered part is refunded to your wallet balance automatically."),
        ]),
    "youtube-subscribers": dict(
        platform="youtube", category="Subscribers", h1_short="YouTube subscribers",
        h1="Buy YouTube subscribers in the Philippines",
        intro="YouTube subscribers for new and growing channels, with the refill period and drop risk shown up front. Pay by QR Ph in pesos.",
        faqs=[
            ("Will subscribers count toward monetization?", "We can't promise that. YouTube decides what counts toward the Partner Program, and it may remove subscribers it flags."),
            ("How fast are YouTube subscribers delivered?", "Subscriber services are slower than views or likes. Each service shows its start time and daily speed."),
            ("What link do I use?", "Your channel link, for example youtube.com/@yourchannel."),
        ]),
    "youtube-views": dict(
        platform="youtube", category="Views", h1_short="YouTube views",
        h1="Buy YouTube views in the Philippines",
        intro="YouTube views for videos and shorts, with retention and source details in the service descriptions. Order from a few hundred to hundreds of thousands.",
        faqs=[
            ("Do you have views for YouTube Shorts?", "Yes. Service names say when they're for Shorts; use the Short's link."),
            ("Why do YouTube views take time to show?", "YouTube can freeze the public count while it checks new views. Delivery continues, and the count usually catches up."),
            ("Can views drop?", "YouTube may remove some views. Services with refill are topped up within their refill period."),
        ]),
    "telegram-members": dict(
        platform="telegram", category="Members", h1_short="Telegram members",
        h1="Buy Telegram members in the Philippines",
        intro="Members for public Telegram channels and groups, with non-drop and refill options clearly marked. Paid in pesos by QR Ph.",
        faqs=[
            ("Does my channel need to be public?", "Yes. Use the public t.me link. Private invite links usually can't be delivered."),
            ("Channel or group?", "Most services work for both; the service name and description say if one is required."),
            ("Can I also get post views?", "Yes, Telegram post views are in the Views category on the order page."),
        ]),
}

SHARED_FAQ = [
    ("How do I pay?", "Top up your wallet by QR Ph on PayMongo's secure checkout with GCash, Maya or your bank app. Orders are then paid from your balance."),
    ("What if my order doesn't fully deliver?", "If an order ends up partial or canceled, the undelivered part is refunded to your wallet balance automatically."),
]

TABLE_ROWS = 12
HIGHLIGHT_ROWS = 5


def _cap(s: str) -> str:
    return s[0].upper() + s[1:]


def title(p: dict, low: float | None) -> str:
    what = " ".join(_cap(w) for w in p["h1_short"].split())
    return f"Buy {what} Philippines{f' from ₱{low:,.2f}/1K' if low else ''} | SMM Shiro"


def description(p: dict, low: float | None) -> str:
    price = f"from ₱{low:,.2f} per 1,000" if low else "in pesos"
    return (f"{_cap(p['h1_short'])} {price}. Quality, start time, drop risk and refill terms "
            f"shown before you pay. Pay by QR Ph with GCash, Maya or bank apps.")


def _refill(days) -> str:
    if not days:
        return "No refill"
    return "Lifetime refill" if days >= 365 else f"{days}-day refill"


def _table(rows: list[dict]) -> str:
    body = "".join(f"""<tr>
      <td class="mono">{r['id']}</td>
      <td><div class="t">{e(r['name'])}</div><div class="s">{e(_refill(r['refill_days']))} · {e(r['start_time'] or '')}</div></td>
      <td>{_badge(r['tier'])}</td>
      <td class="num">{r['min']:,}–{r['max']:,}</td>
      <td class="num"><strong>₱{r['price_per_1k_php']:,.2f}</strong></td>
    </tr>""" for r in rows)
    return f"""<div class="card table-card"><div class="table-scroll"><table class="table seo-table">
    <thead><tr><th>ID</th><th>Service</th><th>Quality</th><th class="num">Min–max</th><th class="num">Per 1,000</th></tr></thead>
    <tbody>{body}</tbody></table></div></div>"""


def _badge(tier: str) -> str:
    t = tier or ""
    low = t.lower()   # same rule as tierBadge() in common.js
    cls = "badge-nd" if "non-drop" in low else "badge-ph" if "ph" in low else "badge-hq" if "hq" in low else "badge-basic"
    return f'<span class="badge {cls}">{e(t)}</span>'


def render(slug: str, rows: list[dict]) -> tuple[str, list, float | None]:
    """rows: this platform's orderable services (catalog rows). Returns (body html, json-ld, lowest price)."""
    p = PAGES[slug]
    mine = sorted((r for r in rows if r["category"] == p["category"]), key=lambda r: (r["price_per_1k_php"], r["id"]))
    low = mine[0]["price_per_1k_php"] if mine else None
    cheapest = mine[:TABLE_ROWS]
    ph = [r for r in mine if "ph" in (r["tier"] or "").lower()][:HIGHLIGHT_ROWS]
    nd = [r for r in mine if r["non_drop"]][:HIGHLIGHT_ROWS]
    intro = p["intro"].replace("from ₱ per thousand", f"from ₱{low:,.2f} per 1,000" if low else "in pesos")
    faqs = p["faqs"] + SHARED_FAQ

    sections = [f"""<h2>Cheapest {e(p['h1_short'])}</h2>
      <p class="muted-p">Live prices per 1,000, updated from our catalog. {len(mine):,} {e(p['h1_short'])} services available.</p>
      {_table(cheapest) if cheapest else '<p>No services available right now. Check back soon.</p>'}"""]
    if ph:
        sections.append(f"""<h2>Philippine {e(p['h1_short'])}</h2>
      <p class="muted-p">Services tagged PH deliver from Philippine accounts.</p>{_table(ph)}""")
    if nd:
        sections.append(f"""<h2>Non-drop {e(p['h1_short'])}</h2>
      <p class="muted-p">The supplier states these should not drop within the guaranteed period.</p>{_table(nd)}""")

    others = "".join(f'<a class="pill" href="/{s}/">{e(q["h1_short"])}</a>' for s, q in PAGES.items() if s != slug)
    faq_html = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in faqs)
    body = f"""<section class="hero seo-hero">
  <div class="wrap">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> / <span>{e(_cap(p['h1_short']))}</span></nav>
      <h1>{e(p['h1'])}</h1>
      <p class="lead">{e(intro)}</p>
      <div class="hero-cta">
        <a class="btn btn-primary btn-lg" href="/login/?mode=register">Create free account</a>
        <a class="btn btn-ghost btn-lg" href="#prices">See prices{f' from ₱{low:,.2f}' if low else ''}</a>
      </div>
      <div class="pay-row"><span>Pay by QR Ph:</span><span class="pay-chip">GCash</span><span class="pay-chip">Maya</span><span class="pay-chip">Bank apps</span></div>
    </div>
  </div>
</section>
<section class="block" id="prices" style="padding-top:0">
  <div class="wrap seo-sections">
    {''.join(f'<div>{s}</div>' for s in sections)}
  </div>
</section>
<section class="block" style="padding-top:0">
  <div class="wrap">
    <div class="section-head"><span class="kicker">How it works</span><h2>Order in three steps</h2></div>
    <div class="grid-3">
      <div class="card step"><span class="n">01</span><h3>Top up by QR Ph</h3><p>Create a free account and add balance from ₱100 with GCash, Maya or your bank app.</p></div>
      <div class="card step"><span class="n">02</span><h3>Pick a service</h3><p>Compare quality, start time, drop risk and refill terms, then paste your public link.</p></div>
      <div class="card step"><span class="n">03</span><h3>Track delivery</h3><p>Watch progress on your Orders page. Undelivered parts are refunded to your balance automatically.</p></div>
    </div>
  </div>
</section>
<section class="block faq" style="padding-top:0">
  <div class="wrap">
    <div class="section-head"><span class="kicker">FAQ</span><h2>{e(_cap(p['h1_short']))}: questions, answered</h2></div>
    {faq_html}
  </div>
</section>
<section class="block" style="padding-top:0">
  <div class="wrap">
    <div class="section-head"><span class="kicker">More services</span><h2>Other popular services</h2></div>
    <div class="price-tabs">{others}</div>
  </div>
</section>"""
    jsonld = [
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": _cap(p["h1_short"]), "item": f"{SITE}/{slug}/"},
        ]},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faqs
        ]},
    ]
    return body, jsonld, low
