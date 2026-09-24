"""Auto-import of the provider's full catalog into `services`.

Hand-curated rows (services.auto = false) are never touched. Everything else in
provider_services is classified here: deceptive or off-topic services are
skipped, the rest get a cleaned-up name, platform, category, tier and refill
window, and are upserted as auto rows. Auto rows whose provider service
disappeared (or is now excluded) are deactivated, never deleted, so old orders
keep their service.
"""
import html
import json
import logging
import re
import unicodedata

from app.db import transaction

log = logging.getLogger("catalog")

IMPORT_TYPES = {"default", "custom comments"}

# Things we won't resell: fake reviews/ratings/votes, ad-revenue/monetisation
# gaming, bought website traffic, account/subscription reselling, junk buckets.
EXCLUDE = re.compile(
    r"review|rating|\brate us\b|\bvotes?\b|\bvoting\b|\bpoll\b|traffic|monetiz|monetis"
    r"|ads? click|\bclicks?\b|app install|premium apps|subscriptions?\b.*\bapps?\b|not for you"
    r"|\breports?\b|\bnetflix\b|\baccounts? for sale\b|down ?votes?",
    re.I,
)
# watch-time packages sold for monetisation thresholds (plain views that mention retention are fine)
WATCH_TIME = re.compile(r"watch ?(time|hours?)", re.I)
# an instruction to the buyer, not a review service
FLAG_FOR_REVIEW = re.compile(r"disable the flag for review|flag review disable", re.I)
FLAG_NOTE = "Turn off \"Flag for review\" in Instagram settings before ordering"

# (code, label, pattern) — earliest match in the text wins.
PLATFORMS = [
    ("tiktok", "TikTok", r"tik ?tok"),
    ("instagram", "Instagram", r"instagram|\binsta\b|\big\b"),
    ("facebook", "Facebook", r"facebook|\bfb\b"),
    ("youtube", "YouTube", r"youtube|\byt\b"),
    ("x", "X", r"twitter|\bx\s*\(|^x\b|\bx -|\btweets?\b"),
    ("telegram", "Telegram", r"telegram"),
    ("whatsapp", "WhatsApp", r"whats ?app"),
    ("spotify", "Spotify", r"spotify"),
    ("threads", "Threads", r"\bthreads\b"),
    ("shopee", "Shopee", r"shopee"),
    ("linkedin", "LinkedIn", r"linked ?in"),
    ("kick", "Kick", r"\bkick\b"),
    ("twitch", "Twitch", r"twitch"),
    ("snapchat", "Snapchat", r"snap ?chat"),
    ("soundcloud", "SoundCloud", r"sound ?cloud"),
    ("discord", "Discord", r"discord"),
    ("reddit", "Reddit", r"reddit"),
    ("pinterest", "Pinterest", r"pinterest"),
]
# smaller sites share one "other" platform; their category is the site name
OTHER_SITES = [
    ("Kwai", r"\bkwai\b"), ("Likee", r"likee"), ("VK", r"vkontakte|\bvk\b"), ("OK.ru", r"ok\.ru|odnoklassniki"),
    ("Quora", r"quora"), ("Tumblr", r"tumblr"), ("Deezer", r"deezer"), ("Audiomack", r"audiomack"),
    ("Tidal", r"\btidal\b"), ("Roblox", r"roblox"), ("Lazada", r"lazada"), ("Vimeo", r"vimeo"),
    ("Coub", r"\bcoub\b"), ("ReverbNation", r"reverbnation"), ("Yandex Zen", r"yandex"),
    ("Apple Music", r"apple music|itunes"), ("SoundClick", r"soundclick"), ("Mixcloud", r"mixcloud"),
    ("Dailymotion", r"dailymotion"), ("Rumble", r"rumble"), ("Trovo", r"trovo"), ("Bigo", r"\bbigo\b"),
    ("Clubhouse", r"clubhouse"), ("Medium", r"\bmedium\.com\b"), ("Behance", r"behance"),
    ("Dribbble", r"dribbble"), ("Kakao", r"kakao"), ("Line", r"\bline\b"), ("Weibo", r"weibo"),
]

# (category, pattern) — first match wins, tested against the cleaned name then the provider category
KINDS = [
    ("Live stream", r"\blive\b|livestream|live ?stream"),
    ("Stories", r"\bstor(y|ies)\b"),
    ("Comment likes", r"comments? (likes?|reactions?|upvotes?)"),
    ("Comments", r"\bcomments?\b|\breplies\b|\breply\b"),
    ("Reactions", r"reaction|emoji|\blove\b|\bhaha\b|\bwow\b|\bsad\b|\bangry\b|\bcare\b"),
    ("Followers", r"follow"),
    ("Subscribers", r"subscri"),
    ("Members", r"\bmembers?\b|\bjoin"),
    ("Likes", r"\blikes?\b|\bhearts?\b|\bfavou?rites?\b|\bupvotes?\b"),
    ("Shares", r"\bshares?\b|repost|retweet|\bquotes?\b"),
    ("Saves", r"\bsaves?\b|bookmark"),
    ("Plays", r"\bplays?\b|\bstreams?\b|\blisten"),
    ("Views", r"\bviews?\b|\bviewers?\b|impression|\breach\b|\bvisits?\b"),
]

# the provider's own "non drop" / "no drop" claim (test on NFKC text: names use fancy fonts)
NON_DROP = re.compile(r"\b(non|no)[ -]?drop", re.I)

PH = re.compile(r"phil+ip+in|filipin|pinoy|\bph\b|\U0001F1F5\U0001F1ED", re.I)

# flags and decorative symbols; other emoji are kept because they tell reaction services apart
_DECOR = re.compile(
    "[\U0001F1E6-\U0001F1FF❌✔✖⚡\U0001F525⭐\U0001F31F\U0001F680✅✨"
    "\U0001F51D\U0001F48E\U0001F195\U0001F50E\U0001F50D\U0001F4F1\U0001F310™®★☆"
    "•─-╿■-◿←-⇿\U0001F449\U0001F448\U0001F447\U0001F446]+"
)
_ORPHAN_VS = re.compile(r"(?<![^\s])[️‍]+")
_SUPERSCRIPT_NEW = re.compile("ᴺᴱᵂ")


def clean(text: str) -> str:
    t = html.unescape(text or "")
    t = _SUPERSCRIPT_NEW.sub(" ", t)
    t = _DECOR.sub(" ", t)                            # before NFKC, or ™ becomes "TM"
    t = unicodedata.normalize("NFKC", t)              # 𝗥𝗘𝗙𝗜𝗟𝗟 → REFILL, fancy fonts → plain
    t = _DECOR.sub(" ", t)
    t = _ORPHAN_VS.sub(" ", t)
    t = re.sub(r"\[\s*\]|\(\s*\)", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -|~·,")


def _smart_case(s: str) -> str:
    """ALL CAPS words (BRAZIL, INSTANT) → Title case, but keep short acronyms (HQ, PH, USA)."""
    return re.sub(r"\b[A-Z]{4,}\b", lambda m: m.group(0).capitalize(), s)


def _find_platform(text: str):
    best = None
    for code, label, pat in PLATFORMS:
        m = re.search(pat, text, re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), code, label)
    if best:
        return best[1], best[2]
    for label, pat in OTHER_SITES:
        if re.search(pat, text, re.I):
            return "other", label
    return None


_SEG_SPLIT = re.compile(r"\s*(?:~|\||»|\[|\]|\(|\))\s*")
_REFILL_SEG = re.compile(r"refill|non ?-?drop|no ?drop|lifetime|guarantee|drop ?rate|\bdrop\b|^r\d+$", re.I)
_SPEED_SEG = re.compile(r"/\s*(day|days|d|hour|hr|h)\b|per ?day|\bdaily\b|\bspeed\b|^day\s*\d", re.I)
_START_SEG = re.compile(r"\binstant\b|\bstart\b|^\d+\s*-\s*\d+\s*(h|hr|hrs|hours?|min|mins|minutes?)\b", re.I)
_MAXMIN_SEG = re.compile(r"^(max|min|maximum|minimum)\b|^[\d.,]+\s*[km]?$|^&$|^\+$", re.I)


def parse_name(raw_name: str):
    """→ (title, description, start_time, speed)"""
    name = clean(raw_name)
    segs = [s.strip(" -:,") for s in _SEG_SPLIT.split(name) if s and s.strip(" -:,")]
    title = segs[0] if segs else name
    extra, start, speed = [], None, None
    for s in segs[1:]:
        if _MAXMIN_SEG.search(s) or _REFILL_SEG.search(s):
            continue
        if _START_SEG.search(s) and not start:
            start = "Instant" if re.fullmatch(r"(start\s*)?instant", s, re.I) else s
            continue
        if _SPEED_SEG.search(s) and not speed:
            speed = s
            continue
        extra.append(s)
    # a bare "Facebook -" style title isn't a name: pull the next part in
    if extra and len(title) < 30 and not any(re.search(p, title, re.I) for _, p in KINDS):
        title = f"{title} {extra.pop(0)}".replace(" - ", " ").strip()
    title = re.sub(r"^([A-Za-z]+) - ", r"\1 ", title)               # "TikTok - Likes" → "TikTok Likes"
    title = _smart_case(re.sub(r"\s+-\s*$", "", title))
    desc = " · ".join(_smart_case(e) for e in extra)[:300] or None
    return title[:120], desc, start, speed


def refill_days(raw_name: str, api_refill: bool) -> int:
    if not api_refill:
        return 0      # no refill button upstream → we don't promise one, whatever the name says
    n = clean(raw_name)
    m = re.search(r"refill\s*(\d+)\s*d|(\d+)\s*d(?:ays?)?\s*refill|refill\s*(\d+)\s*days?", n, re.I)
    if m:
        return min(int(next(g for g in m.groups() if g)), 365)
    if re.search(r"lifetime|life ?time", n, re.I):
        return 365
    return 30


def classify(svc: dict):
    """One provider_services row → dict for `services`, or None to skip."""
    typ = str(svc.get("type") or "").strip().lower()
    if typ not in IMPORT_TYPES:
        return None
    raw_name = str(svc.get("name") or "")
    raw_cat = str(svc.get("category") or "")
    name, cat = clean(raw_name), clean(raw_cat)
    if not name or re.fullmatch(r"[-=_*~.\s]*", name):
        return None
    flag_note = bool(FLAG_FOR_REVIEW.search(f"{name} {cat}"))
    name, cat = FLAG_FOR_REVIEW.sub(" ", name), FLAG_FOR_REVIEW.sub(" ", cat)
    both = f"{name} {cat}"
    if EXCLUDE.search(both):
        return None
    if WATCH_TIME.search(cat) or WATCH_TIME.match(re.sub(r"^\W*(youtube|twitter|x)\W*", "", name, flags=re.I)):
        return None
    try:
        rate = float(svc.get("rate") or 0)
    except (TypeError, ValueError):
        return None
    if rate <= 0 or int(svc.get("max_qty") or svc.get("max") or 0) <= 0:
        return None

    plat = _find_platform(cat) or _find_platform(name)
    if not plat:
        return None
    platform, site = plat

    is_comments = typ == "custom comments"
    kind = "Comments" if is_comments else None
    if not kind:
        for text in (parse_name(raw_name)[0], name, cat):
            kind = next((label for label, pat in KINDS if re.search(pat, text, re.I)), None)
            if kind:
                break
    category = site if platform == "other" else (kind or "Other")

    title, desc, start, speed = parse_name(FLAG_FOR_REVIEW.sub(" ", clean(raw_name)))
    if flag_note:
        desc = f"{FLAG_NOTE} · {desc}" if desc else FLAG_NOTE
    if is_comments and "custom" not in title.lower():
        title = f"{title} (custom)"
    api_refill = bool(svc.get("refill"))
    days = refill_days(raw_name, api_refill)
    non_drop = bool(NON_DROP.search(name))
    if PH.search(both) and not re.search(r"pakistan|\bpk\b", both, re.I):
        tier = "PH"
    elif non_drop:
        tier = "Non-drop"
    elif api_refill:
        tier = "HQ"
    else:
        tier = "Basic"
    return {
        "sid": int(svc.get("provider_service_id") or svc.get("service")),
        "platform": platform,
        "category": category,
        "name": title,
        "tier": tier,
        "description": desc,
        "start_time": start,
        "speed": speed,
        "drop_risk": "Lowest" if non_drop else "Moderate" if api_refill else "High",
        "refill_days": days,
    }


async def import_catalog(provider_id: int) -> dict:
    """Classify every provider service and upsert the auto rows. Returns counts."""
    async with transaction() as db:
        rows = await db.fetch_all("""
            select ps.provider_service_id, ps.name, ps.category, ps.type, ps.rate,
                   ps.max_qty, ps.refill
              from provider_services ps
             where ps.provider_id = :p
               and not exists (select 1 from services s
                                where s.provider_id = ps.provider_id
                                  and s.provider_service_id = ps.provider_service_id
                                  and not s.auto)
        """, {"p": provider_id})
    items = [c for c in (classify(dict(r)) for r in rows) if c]
    async with transaction() as db:
        res = await db.fetch_one("""
            with x as (
                select * from jsonb_to_recordset(CAST(:rows AS jsonb)) as x(
                    sid bigint, platform text, category text, name text, tier text, description text,
                    start_time text, speed text, drop_risk text, refill_days integer)
            ), up as (
                insert into services (provider_id, provider_service_id, platform, category, name, tier,
                                      description, start_time, speed, drop_risk, refill_days,
                                      markup_pct, auto, active, sort)
                select :p, sid, platform, category, name, tier, description, start_time, speed,
                       drop_risk, refill_days, null, true, true, 1000
                  from x
                on conflict (provider_id, provider_service_id) where auto do update set
                  platform = excluded.platform, category = excluded.category, name = excluded.name,
                  tier = excluded.tier, description = excluded.description,
                  start_time = excluded.start_time, speed = excluded.speed,
                  drop_risk = excluded.drop_risk, refill_days = excluded.refill_days, active = true
                where services.auto and (
                  services.platform, services.category, services.name, services.tier, services.description,
                  services.start_time, services.speed, services.drop_risk, services.refill_days, services.active
                ) is distinct from (
                  excluded.platform, excluded.category, excluded.name, excluded.tier, excluded.description,
                  excluded.start_time, excluded.speed, excluded.drop_risk, excluded.refill_days, true
                )
                returning (xmax = 0) as inserted
            ), off as (
                update services s set active = false
                 where s.provider_id = :p and s.auto and s.active
                   and s.provider_service_id not in (select sid from x)
                returning 1
            )
            select (select count(*) from up where inserted) as added,
                   (select count(*) from up where not inserted) as updated,
                   (select count(*) from off) as deactivated
        """, {"p": provider_id, "rows": json.dumps(items)})
    # give hand-picked rows a category too, so they sit in the same filters
    async with transaction() as db:
        for r in await db.fetch_all(
                "select id, name from services where provider_id = :p and not auto and category is null",
                {"p": provider_id}):
            kind = next((label for label, pat in KINDS if re.search(pat, r["name"], re.I)), "Other")
            await db.execute("update services set category = :c where id = :id", {"c": kind, "id": r["id"]})
    out = {"considered": len(rows), "imported": len(items), **dict(res)}
    log.info("catalog import (provider %s): %s", provider_id, out)
    return out
