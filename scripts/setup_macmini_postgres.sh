#!/bin/bash
# Create a local Postgres database/user for Mac mini server mode.
# Run from repo root: bash scripts/setup_macmini_postgres.sh
set -euo pipefail

DB_NAME="${ALTCOIN_DB_NAME:-altcoin_trading}"
DB_USER="${ALTCOIN_DB_USER:-altcoin}"
DB_PASS="${ALTCOIN_DB_PASS:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)}"
PG_SERVICE="postgresql@16"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install Homebrew first: https://brew.sh"
  exit 1
fi

if ! brew list "$PG_SERVICE" >/dev/null 2>&1; then
  echo "Installing $PG_SERVICE ..."
  brew install "$PG_SERVICE"
fi

echo "Starting $PG_SERVICE ..."
brew services start "$PG_SERVICE" >/dev/null || true

# Homebrew Postgres usually creates a superuser matching the macOS username.
# This block is idempotent.
echo "Creating role/database if missing ..."
psql postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
      CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
   ELSE
      ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASS}';
   END IF;
END
\$\$;
SQL

if ! psql postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  createdb -O "$DB_USER" "$DB_NAME"
fi

psql "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
GRANT ALL ON SCHEMA public TO ${DB_USER};
ALTER SCHEMA public OWNER TO ${DB_USER};
SQL

URL="postgresql://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}"

echo ""
echo "Local Postgres is ready."
echo "DATABASE_URL=${URL}"
echo ""
echo "Next steps:"
echo "  1) open -e .env"
echo "  2) replace any Railway DATABASE_URL with the line above"
echo "  3) source .venv/bin/activate && python scripts/macmini_status.py"
echo ""
echo "Password was generated/updated for user '${DB_USER}'. Save it in .env."
