# Stop using Railway after Mac migration

After local Postgres and Tailscale Serve work:

- Use the Tailscale dashboard URL, not the Railway URL.
- Keep `DATABASE_URL` pointed at localhost on the Mac mini.
- Run collectors/research on the Mac mini only.
- Treat Railway data as old state unless you intentionally migrate it.
