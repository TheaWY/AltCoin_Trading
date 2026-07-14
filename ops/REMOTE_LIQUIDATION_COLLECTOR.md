# Remote liquidation collector

The forced-liquidation stream (`!forceOrder@arr`) is the highest-priority data
gap: forced selling is the purest non-informational weakness, and we proved
weakness mean-reverts (+0.72%/72h, n=5,971, every regime). It is **forward-only**
— Binance removed the `allForceOrders` REST endpoints (verified 404 on
2026-07-15), so there is no backfill. Every day not collecting is permanent loss.

## Why a separate host

The Binance **futures** websocket data-plane is **geo-blocked** from the Mac
mini's egress:

- `wss://fstream.binance.com/...` — TLS handshake and SUBSCRIBE both succeed
  (`{"result":null,"id":1}`), but **zero data frames** ever arrive.
- `wss://fstream-mm.binance.com/...` — **HTTP 403**.
- Spot websockets (`stream.binance.com`, `data-stream.binance.vision`) work
  fine; futures **REST** (ccxt) works fine — that's how we already collect
  funding / OI / LSR.

`!forceOrder@arr` lives only on the futures websocket, so it must originate from
a host in a Binance-permitted region. That host writes to the **same Postgres**
over Tailscale.

## Provisioning

1. **Provision a small VPS** (~$5/mo) in a permitted region. Install Python
   3.11+, clone this repo to `/opt/AltCoin_Trading`, create `.venv`, and
   `pip install` the project deps (needs `websockets`, `psycopg`,
   `psycopg_pool`).

2. **Join it to the tailnet**: `tailscale up`. Confirm it can reach the Mac mini
   (`tailscale ping mac-mini-pc1` → `100.88.26.107`).

3. **Expose Postgres to the tailnet on the Mac mini** (currently `localhost`-only):
   - `postgresql.conf`: `listen_addresses = 'localhost,100.88.26.107'`
   - `pg_hba.conf`: add
     `host  altcoin_trading  altcoin  100.64.0.0/10  scram-sha-256`
     (the Tailscale CGNAT range — do **not** open Postgres to the public internet).
   - Reload Postgres. Verify from the VPS:
     `psql "postgresql://altcoin:...@100.88.26.107:5432/altcoin_trading" -c '\dt'`

4. **Configure the unit**: edit `ops/liquidation-collector.service` —
   `WorkingDirectory`, `User`, and the `DATABASE_URL` (the collector **refuses
   to start without `DATABASE_URL`** so data is never stranded in a local
   SQLite file).

5. **Enable**:
   ```
   sudo cp ops/liquidation-collector.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now liquidation-collector
   journalctl -u liquidation-collector -f
   ```

## Verify it's collecting

After a few minutes (liquidations are sparse in calm markets; busier in
volatility):

```sql
SELECT COUNT(*) FROM liquidations;
SELECT symbol, long_liq_notional, short_liq_notional, liq_count
FROM liquidation_agg_1h ORDER BY timestamp DESC LIMIT 10;
```

The tables are created lazily on first write (`ensure_schema`). A point-in-time
guard drops any event stamped past `now + 120s`.

## Invariants

- **Run EXACTLY ONE instance.** Raw rows dedupe via a UNIQUE constraint, but the
  1h aggregate is **additive per flush** — two live collectors on the same DB
  would double-count it. Do **not** also set `LIQUIDATION_STREAM_ENABLED=true`
  on the Mac mini worker.
- Fault isolation is layered: the stream never raises (catches + reconnects),
  and `Restart=always` covers a hard process death. It has no path to the
  trading cycle — it's a different process on a different host.
