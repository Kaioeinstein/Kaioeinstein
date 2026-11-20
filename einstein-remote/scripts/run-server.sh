#!/usr/bin/env bash
# Inicia o servidor EINSTEIN REMOTE.
# Uso: ./scripts/run-server.sh            (desenvolvimento, http://127.0.0.1:8000)
#      ./scripts/run-server.sh --tls      (produção, https://0.0.0.0:8443 com certs/server.{key,crt})
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" == "--tls" ]]; then
  export EINSTEIN_COOKIE_SECURE=1
  exec uvicorn app.main:app --app-dir server --host 0.0.0.0 --port 8443 \
    --ssl-keyfile certs/server.key --ssl-certfile certs/server.crt
fi
exec uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8000
