-- SMM panel schema (PostgreSQL / Neon)
-- Apply once: psql "$DATABASE_URL" -f db/schema.sql

create table if not exists users (
  id            bigserial primary key,
  email         text not null,
  password_hash text not null,
  is_admin      boolean not null default false,
  created_at    timestamptz not null default now()
);
create unique index if not exists users_email_lower on users (lower(email));

-- Balance = sum(delta). Never update balances in place.
create table if not exists ledger (
  id         bigserial primary key,
  user_id    bigint not null references users(id),
  delta      numeric(12,2) not null,
  reason     text not null,            -- topup | order | refund | adjustment
  ref        text,                     -- topup id / order id
  created_at timestamptz not null default now()
);
create index if not exists ledger_user on ledger (user_id);
-- one refund per order, one credit per top-up
create unique index if not exists ledger_unique_ref on ledger (reason, ref) where reason in ('topup', 'refund');

create table if not exists topups (
  id           uuid primary key,
  user_id      bigint not null references users(id),
  amount_php   integer not null check (amount_php > 0),
  method       text not null,          -- gcash | paymaya
  checkout_id  text,
  checkout_url text,
  status       text not null default 'pending',   -- pending | credited
  created_at   timestamptz not null default now(),
  credited_at  timestamptz
);
create index if not exists topups_user on topups (user_id, created_at desc);

-- Upstream panels. API keys live in env vars, never in the DB.
create table if not exists providers (
  id          serial primary key,
  name        text not null unique,
  api_url     text not null,
  api_key_env text not null,           -- e.g. SMMGEN_API_KEY
  currency    text not null default 'USD',
  active      boolean not null default true
);

-- Raw catalog synced from each provider (action=services)
create table if not exists provider_services (
  provider_id         int not null references providers(id),
  provider_service_id bigint not null,
  name                text not null,
  category            text,
  type                text,
  rate                numeric(14,6) not null,   -- per 1000, provider currency
  min_qty             integer not null,
  max_qty             integer not null,
  refill              boolean not null default false,
  cancel              boolean not null default false,
  raw                 jsonb,
  updated_at          timestamptz not null default now(),
  primary key (provider_id, provider_service_id)
);

-- What customers see. Each row maps to one provider service; switching providers = update this row.
create table if not exists services (
  id                  serial primary key,
  provider_id         int not null references providers(id),
  provider_service_id bigint not null,
  platform            text not null,          -- tiktok | facebook | instagram | youtube | x | ... | other
  category            text,                   -- Followers | Likes | Views | ... (site name for "other")
  name                text not null,          -- e.g. "TikTok Followers"
  tier                text not null,          -- Basic | HQ | Non-drop | PH | Real · PH
  description         text,
  start_time          text,                   -- "0–6 hrs"
  speed               text,                   -- "Up to 5K / day"
  drop_risk           text,                   -- Lowest | Low | Moderate | Likely
  refill_days         integer not null default 0,   -- 0 = no refill
  markup_pct          numeric(6,2),           -- null = tiered by provider rate (see app/pricing.py)
  auto                boolean not null default false,   -- true = imported by app/catalog.py
  active              boolean not null default true,
  sort                integer not null default 0,
  foreign key (provider_id, provider_service_id) references provider_services (provider_id, provider_service_id)
);

-- upgrades for databases created before the full-catalog import
alter table services add column if not exists category text;
alter table services add column if not exists auto boolean not null default false;
alter table services add column if not exists hidden boolean not null default false;   -- hidden by the owner in /admin
alter table services alter column markup_pct drop not null;
alter table services alter column markup_pct drop default;
create unique index if not exists services_auto_psid on services (provider_id, provider_service_id) where auto;
create index if not exists services_active on services (platform, sort, id) where active;

create table if not exists orders (
  id                bigserial primary key,
  user_id           bigint not null references users(id),
  service_id        int not null references services(id),
  provider_id       int not null references providers(id),
  provider_order_id bigint,
  link              text not null,
  quantity          integer not null check (quantity > 0),
  price_php         numeric(12,2) not null,
  cost              numeric(14,6),          -- provider charge, provider currency
  status            text not null default 'creating',
    -- creating | pending | in_progress | completed | partial | canceled | failed | needs_review
  comments          text,                   -- custom comments, one per line
  start_count       integer,
  remains           integer,
  completed_at      timestamptz,
  cancel_requested_at timestamptz,          -- customer asked the provider to cancel
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
alter table orders add column if not exists comments text;   -- for databases created before this column
alter table orders add column if not exists cancel_requested_at timestamptz;
create index if not exists orders_user on orders (user_id, created_at desc);
create index if not exists orders_sync on orders (provider_id, status);

create table if not exists provider_refills (
  id                 bigserial primary key,
  order_id           bigint not null references orders(id),
  provider_id        int not null references providers(id),
  provider_refill_id bigint not null,
  status             text not null default 'pending',   -- pending | completed | rejected | ignored
  requested_at       timestamptz not null default now(),
  resolved_at        timestamptz
);
create index if not exists provider_refills_sync on provider_refills (provider_id, status);
create unique index if not exists provider_refills_one_pending on provider_refills (order_id) where status = 'pending';
