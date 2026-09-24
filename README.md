# SMM Panel API

FastAPI backend for an SMM reseller panel: wallet top-ups via PayMongo (GCash/Maya), orders routed to upstream SMM providers (standard API v2), background status/refill/catalog sync, and a double-entry-style ledger.

## Stack

- FastAPI + SQLAlchemy Core (async) + asyncpg
- Postgres (Neon)
- PayMongo Checkout Sessions + webhook
- Upstream providers: any standard SMM API v2 panel (SMMGen, SMMFollowom, …)

## Layout

```
app/
  main.py               FastAPI app + in-process sync loop (lifespan)
  config.py             env settings
  db.py                 engine, transaction helpers (handles Neon sslmode/pooler)
  security.py           argon2 passwords, JWT session cookie, balance
  catalog.py            full provider catalog → services (cleans names, skips reviews/votes/traffic)
  pricing.py            provider rate → PHP price with markup (tiered when markup_pct is null)
  providers/smm_client.py   SMM API v2 client (status/refill in batches of 100)
  routers/
    auth.py             /auth/register, /auth/login, /auth/logout, /auth/me
    services.py         /services (public catalog with PHP prices)
    orders.py           /orders (create, list/filter/search), /orders/{id}/refill
    topups.py           /topups (create PayMongo checkout, list)
    webhooks.py         /webhooks/paymongo (signature-verified, idempotent credit)
  workers/sync.py       order statuses + partial/cancel refunds, refill statuses, catalog, stuck orders
db/
  schema.sql            tables + indexes
  seed_example.sql      provider row, publishing services, refill scorecard query
scripts/
  ph_services.py        list PH-targeted services across providers → CSV
  refill_test.py        manual refill test against a provider
```

## Catalog

- Every 6 hours the sync pulls the provider's service list, then `app/catalog.py` imports it
  as `auto` rows: cleaned name, platform, category (Followers, Likes, Views…), tier, refill.
- Skipped on purpose: reviews/ratings, poll votes, website traffic, monetisation/watch-time,
  app subscriptions, separator and "not for you" rows, and non-standard order types (packages etc.).
- Tier: `HQ` only when the provider actually offers refills for it, `PH` for Philippine-targeted, else `Basic`.
  A refill promised in the name but not offered by the provider API counts as no refill.
- Hand-picked rows (`auto = false`) are never overwritten; they show first as "Recommended".
- Price: `markup_pct` null → 300% under $0.05/1K, 150% under $0.50/1K, 60% above. Set a number to fix it.

## Money rules

- Balance = `sum(ledger.delta)`. Nothing updates a balance in place.
- Order placement locks the user row, checks balance, debits, then calls the provider **outside** the transaction.
  - Provider rejects → order `failed` + full refund.
  - Network error/timeout → order `needs_review` (never auto-refunded: it may have been placed).
- Partial/canceled orders refund the unfilled share once (unique index on `ledger(reason, ref)`).
- Top-ups are credited only by the PayMongo webhook, only if the paid amount matches, only once.

## Render setup

**Web Service** (free works if kept awake with an uptime pinger on `/health`)

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |

**Environment variables** — see `.env.example`:

| Var | Notes |
|---|---|
| `DATABASE_URL` | Neon connection string, as copied (`?sslmode=require` is handled) |
| `BASE_URL` | this service's public URL |
| `FRONTEND_ORIGIN` | frontend URL (CORS + PayMongo redirect) |
| `JWT_SECRET` | long random string |
| `PAYMONGO_SECRET_KEY` | `sk_test_…` first, `sk_live_…` later |
| `PAYMONGO_WEBHOOK_SECRET` | from the webhook you register (below) |
| `SMMGEN_API_KEY` | from SMMGen → Account |
| `USD_TO_PHP` | provider rate conversion, update occasionally |
| `PYTHON_VERSION` | `3.11.9` (optional; `.python-version` pins 3.11) |

**Uptime pinger:** monitor `https://<service>.onrender.com/health` every 5 minutes. The sync loop runs inside the web service every `SYNC_INTERVAL_SECONDS` (default 180) as long as it's awake.

## First deploy

1. Apply the schema to Neon:
   ```
   psql "$DATABASE_URL" -f db/schema.sql
   ```
2. Register the provider (first block of `db/seed_example.sql`).
3. Deploy. ~5 s after boot the first sync fills `provider_services` from SMMGen.
4. Pick services and publish them into `services` (block 3 of `seed_example.sql`): platform, customer-facing name, tier, refill days, markup.
5. PayMongo dashboard → Developers → Webhooks → add `https://<service>/webhooks/paymongo` with event `checkout_session.payment.paid`; copy its secret into `PAYMONGO_WEBHOOK_SECRET`.
6. Test with PayMongo test keys, then switch to live after PayMongo approves the panel.

## Local dev

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in; COOKIE_SECURE=false for http://localhost
uvicorn app.main:app --reload
```
API docs at `http://localhost:8000/docs`.

## Frontend (`web/`)

Plain HTML/CSS/JS, no build step, **served by the same FastAPI service** (mounted at `/` after the API routes), so the site and API share one origin.
Pages: `/` (landing with live prices), `/login/`, `/dashboard/` (New order, Orders, Add funds via `#new`, `#orders`, `#funds`).
Design tokens (colors, fonts, radii) are CSS variables at the top of `web/assets/app.css`.

Point both `smmshiro.com` and `server.smmshiro.com` at the one Render web service.

## Not built yet

- Admin UI (services are managed with SQL for now)
- Reseller API for customers
- Rate limiting, email verification, password reset
- Provider failover / auto-reorder when a refill is ignored
