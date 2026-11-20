#!/usr/bin/env bash
# Gera uma CA local + certificado do servidor (para uso em rede própria/testes).
# Uso: ./scripts/gen-cert.sh meu-servidor.local 192.168.0.10
set -euo pipefail
# Git Bash (Windows) converteria "/CN=..." num caminho de arquivo
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"
cd "$(dirname "$0")/.."
HOST="${1:-localhost}"; IP="${2:-127.0.0.1}"
mkdir -p certs && cd certs
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes -keyout ca.key -out ca.crt -subj "/CN=Einstein Remote Local CA"
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=$HOST"
printf "subjectAltName=DNS:%s,DNS:localhost,IP:%s,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n" "$HOST" "$IP" > ext.cnf
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -sha256 -extfile ext.cnf -out server.crt
rm -f server.csr ext.cnf
chmod 600 ca.key server.key
echo "Pronto: certs/server.crt, certs/server.key. Copie certs/ca.crt para o PC do agente (--ca-file)."
