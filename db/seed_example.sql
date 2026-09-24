-- 1) Register providers (API keys stay in env vars; set SMMGEN_API_KEY etc. on Render)
insert into providers (name, api_url, api_key_env, currency)
values ('smmgen', 'https://my.smmgen.com/api/v2', 'SMMGEN_API_KEY', 'USD')
on conflict (name) do nothing;

-- 2) Deploy once and wait for the first sync (~5s after boot) to fill provider_services,
--    or run: python -c "import asyncio; from app.workers.sync import run_sync_once; asyncio.run(run_sync_once())"
--    Then browse it:
--    select provider_service_id, category, name, rate, min_qty, max_qty, refill
--      from provider_services where provider_id = 1 and name ilike '%tiktok%follow%' order by rate;

-- 3) Publish services customers can order (replace provider_service_id with real IDs)
-- insert into services (provider_id, provider_service_id, platform, name, tier, description,
--                       start_time, speed, drop_risk, refill_days, markup_pct, sort)
-- values
--   (1, 1234, 'tiktok', 'TikTok Followers', 'Basic', 'Automated or low-activity accounts. Cheapest; drops likely.',
--    '0–1 hr', 'Up to 10K / day', 'High', 0, 80, 10),
--   (1, 5678, 'tiktok', 'TikTok Followers', 'HQ', 'Accounts with photos and posts. Drops covered by refill.',
--    '0–6 hrs', 'Up to 5K / day', 'Moderate', 30, 60, 20);

-- Refill scorecard per provider (last 30 days)
-- select p.name, count(*) refills,
--        avg((r.status = 'completed')::int)::numeric(4,2) success_rate,
--        percentile_cont(0.5) within group (order by r.resolved_at - r.requested_at) median_time
--   from provider_refills r join providers p on p.id = r.provider_id
--  where r.requested_at > now() - interval '30 days' and r.status <> 'pending'
--  group by p.name;
