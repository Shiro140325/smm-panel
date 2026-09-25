# End-to-end test (local Postgres + mock provider)

Needs a throwaway Postgres database with `db/schema.sql` applied.

```
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL=postgresql://postgres:x@127.0.0.1:5432/smm_test
export JWT_SECRET=test-secret-test-secret COOKIE_SECURE=false
export PAYMONGO_WEBHOOK_SECRET=whsk_test MOCK_KEY=mockkey SYNC_ENABLED=false USD_TO_PHP=58 SERVICES_CACHE_SECONDS=0 FX_BUFFER_PCT=0
export PAYMONGO_SECRET_KEY=sk_test_x PAYMONGO_API_BASE=http://127.0.0.1:9001/v1 ADMIN_PASS=test-admin-pass

uvicorn tests.mock_provider:app --port 9001 &
uvicorn app.main:app --port 8000 &
python tests/e2e.py
```

Covers: auth, pricing, webhook signature + idempotent credit, amount mismatch, order debit/overspend lock,
provider rejection refund, partial refund via sync, refills, per-user isolation.
Run it on a fresh database each time.
