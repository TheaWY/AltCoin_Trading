# Railway → Mac mini migration notes

## What changes

Old:

```text
Mac mini worker → Railway Postgres → Railway dashboard
```

New:

```text
Mac mini worker → local Mac mini Postgres → local Mac mini dashboard → Tailscale private HTTPS
```

## What not to use anymore

- Do not set `DATABASE_URL` to Railway in `.env`.
- Do not rely on Railway deploys for dashboard changes.
- Do not run data collectors on Railway.

## What stays the same

- Same FastAPI app: `src.api.main:app`
- Same dashboard routes: `/dashboard`, `/experiments`, `/dashboard/full`
- Same storage abstraction: `src.data.storage.get_storage()`
- Same paper-trading mode and research stack

## Clean migration path

1. Create local Postgres with `scripts/setup_macmini_postgres.sh`.
2. Replace `.env` with `.env.macmini.example` and local `DATABASE_URL`.
3. Bootstrap local candles/metrics/categories with `scripts/bootstrap_macmini_local.sh`.
4. Reset paper portfolio to ₩1,000,000 equivalent.
5. Start launchd services with `ops/install_macmini_server.sh`.
6. Start Tailscale Serve with `ops/serve_tailscale.sh`.
7. Stop using the Railway dashboard URL.

## Data migration choice

Recommended for now: start fresh locally.

Reason: the current system is still paper/research mode. Fresh local data avoids mixing old Railway paper state with new local services.

Migrate Railway data only if you need historical paper trades or experiment records. Use `pg_dump`/`pg_restore` later, not partial manual copies.
