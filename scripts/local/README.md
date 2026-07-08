# Local (Mac) legacy ops — not used on Railway

Everything in this folder belongs to the old "runs on my Mac with an ngrok
tunnel" setup. The Railway deployment does **not** use any of it.

| Script | Purpose |
| --- | --- |
| `common.sh` | shared env/interpreter resolution for the scripts below |
| `restart.sh` / `ensure-running.sh` / `show-url.sh` | restart the local app + ngrok tunnel, keep it alive, print the public URL |
| `start-trading.sh` | local all-in-one entrypoint (web + scheduler) |
| `setup-ngrok-domain.sh` | configure a permanent ngrok domain |
| `install-launchd.sh` / `uninstall-launchd.sh` | register/remove macOS launchd agents |
| `install-gitsync.sh` / `git-sync.sh` / `hooks/` | auto-push commits from the Mac |
| `launchd/` | launchd plist templates |

Cloud entrypoints live one level up in `scripts/`:
`start-web.sh` (Railway web service), `start-worker.sh` (optional worker
service), `deploy-cloud.sh`, plus the shared tools `backtest.py`,
`backfill.py`, `health_check.py`, and `storage_smoke.py`.
