#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -e .env.e2e ]; then
  echo ".env.e2e already exists; leaving it unchanged" >&2
  exit 1
fi
umask 077
pgpass=$(openssl rand -hex 24)
apipass=$(openssl rand -hex 24)
botpass=$(openssl rand -hex 24)
maintpass=$(openssl rand -hex 24)
adminpass=$(openssl rand -hex 18)
ratelimit=$(openssl rand -hex 32)
cat > .env.e2e <<EOF
POSTGRES_USER=afisha_e2e
POSTGRES_PASSWORD=$pgpass
POSTGRES_DB=afisha_e2e
API_DB_PASSWORD=$apipass
BOT_DB_PASSWORD=$botpass
MAINTENANCE_DB_PASSWORD=$maintpass
RATE_LIMIT_SECRET=$ratelimit
ADMIN_USERNAME=e2e-admin
ADMIN_PASSWORD=$adminpass
ADMIN_COOKIE_SECURE=false
AI_MODE=no-ai
ADAPTIVE_AGENT_ENABLED=false
STRUCTURED_DEEP_ENABLED=false
JEV_ENABLED=false
JEV_API_KEY=
LOCAL_LLM_BASE_URL=
GROQ_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
MAX_BOT_ENABLED=false
MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=
MAX_WEBHOOK_SECRET=
POSTGRES_PORT=15433
API_PORT=18000
WEB_PORT=13000
WEB_ORIGIN=http://127.0.0.1:13000
EOF
chmod 600 .env.e2e
echo "Created .env.e2e with fresh local E2E credentials (mode 600)."
