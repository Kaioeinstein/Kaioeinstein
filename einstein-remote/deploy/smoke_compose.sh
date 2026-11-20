#!/usr/bin/env bash
# Teste do servidor em produção: docker compose (servidor + Caddy com HTTPS) e um agente REAL via wss://.
#   cd einstein-remote && bash deploy/smoke_compose.sh
# Requer docker compose, python3 com as dependências do agente (pip install ./agent) e as portas 80/443 livres.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=ersmoke
WORK=$(mktemp -d)
export EINSTEIN_DOMAIN=localhost EINSTEIN_TLS=internal
COMPOSE=(docker compose -p "$PROJECT")
AGENT_PID=""

cleanup() {
  [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true
  "${COMPOSE[@]}" logs --tail 30 server || true
  "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
fail() { echo "FALHA: $*" >&2; exit 1; }
ok() { echo "  ok  $*"; }
json() { python3 -c "import sys,json; print(json.load(sys.stdin)$1)"; }
H=(-H "X-Requested-With: EinsteinRemote" -H "Content-Type: application/json")

BUILD_FLAG=--build
[ -n "${EINSTEIN_NO_BUILD:-}" ] && BUILD_FLAG=--no-build  # imagem já construída
"${COMPOSE[@]}" up -d "$BUILD_FLAG"
for _ in $(seq 60); do
  "${COMPOSE[@]}" cp caddy:/data/caddy/pki/authorities/local/root.crt "$WORK/ca.crt" >/dev/null 2>&1 \
    && curl -sf --cacert "$WORK/ca.crt" https://localhost/api/health >/dev/null && break
  sleep 1
done
curl -sf --cacert "$WORK/ca.crt" https://localhost/api/health | grep -q '"status":"ok"' || fail "servidor não responde via HTTPS"
ok "HTTPS com certificado válido para a CA do Caddy"

[ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost/)" = "308" ] || fail "HTTP não redireciona para HTTPS"
ok "HTTP redireciona para HTTPS"
curl -sI --cacert "$WORK/ca.crt" https://localhost/ | grep -qi "^content-security-policy:" || fail "cabeçalhos de segurança ausentes"
ok "painel servido com cabeçalhos de segurança"

C=(--cacert "$WORK/ca.crt")
TOKEN=$(curl -sf "${C[@]}" "${H[@]}" https://localhost/api/auth/setup -d '{"username":"dono","password":"Producao#2026x"}' | json "['access_token']")
curl -s "${C[@]}" "${H[@]}" https://localhost/api/auth/login -d '{"username":"dono","password":"senha-errada-1"}' >/dev/null
CADDY_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$("${COMPOSE[@]}" ps -q caddy)")
AUDIT_IP=$("${COMPOSE[@]}" exec -T server python -c \
  "import sqlite3; print(sqlite3.connect('/data/einstein.db').execute(\"select ip from audit_log where action='auth.login_failed'\").fetchone()[0])")
[ -n "$AUDIT_IP" ] && [ "$AUDIT_IP" != "$CADDY_IP" ] || fail "servidor registrou o IP do proxy ($AUDIT_IP) e não o do visitante"
ok "IP real do visitante atrás do proxy ($AUDIT_IP; proxy = $CADDY_IP)"

CODE=$(curl -sf "${C[@]}" "${H[@]}" -H "Authorization: Bearer $TOKEN" https://localhost/api/pairing-codes \
  -d '{"device_name":"PC produção"}' | json "['code']")
export PYTHONPATH="$PWD/agent${PYTHONPATH:+:$PYTHONPATH}"  # agente do código-fonte
export EINSTEIN_AGENT_DIR="$WORK/agent" EINSTEIN_AGENT_LOG_DIR="$WORK/logs"
python3 -m einstein_agent pair --server https://localhost --code "$CODE" --ca-file "$WORK/ca.crt" >/dev/null || fail "pareamento via HTTPS"
python3 -m einstein_agent run >"$WORK/agent.log" 2>&1 &
AGENT_PID=$!
for _ in $(seq 30); do
  STATE=$(curl -sf "${C[@]}" -H "Authorization: Bearer $TOKEN" https://localhost/api/devices | json "[0]['state']" || true)
  [ "$STATE" = "online" ] && break
  sleep 1
done
[ "$STATE" = "online" ] || { cat "$WORK/agent.log"; fail "agente não ficou online via wss://"; }
ok "agente real vinculado e online via wss:// pelo Caddy"

"${COMPOSE[@]}" restart server >/dev/null
for _ in $(seq 60); do
  STATE=$(curl -sf "${C[@]}" -H "Authorization: Bearer $TOKEN" https://localhost/api/devices 2>/dev/null | json "[0]['state']" 2>/dev/null || true)
  [ "$STATE" = "online" ] && break
  sleep 1
done
[ "$STATE" = "online" ] || fail "após reiniciar o contêiner, dados ou reconexão do agente perdidos"
ok "contêiner reiniciado: conta e dispositivo preservados no volume, agente reconectou sozinho"
echo
echo "Produção (Docker + HTTPS) OK."
