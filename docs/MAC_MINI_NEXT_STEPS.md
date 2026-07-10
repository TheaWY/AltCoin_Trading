# Immediate next steps

1. Pull `macmini-tailscale-server` on the Mac mini.
2. Run `bash scripts/setup_macmini_postgres.sh`.
3. Copy `.env.macmini.example` to `.env` and paste the generated local `DATABASE_URL`.
4. Run `bash scripts/bootstrap_macmini_local.sh`.
5. Run `bash ops/install_macmini_server.sh`.
6. Run `bash ops/serve_tailscale.sh`.
7. Open the Tailscale URL at `/dashboard`.

Do not use the old Railway dashboard URL after this migration.
