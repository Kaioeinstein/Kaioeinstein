# Inicia o servidor EINSTEIN REMOTE no Windows.
# Uso: .\scripts\run-server.ps1          (desenvolvimento)
#      .\scripts\run-server.ps1 -Tls     (HTTPS na porta 8443, requer certs\server.key e certs\server.crt)
param([switch]$Tls)
Set-Location (Join-Path $PSScriptRoot "..")
if ($Tls) {
    $env:EINSTEIN_COOKIE_SECURE = "1"
    uvicorn app.main:app --app-dir server --host 0.0.0.0 --port 8443 --ssl-keyfile certs\server.key --ssl-certfile certs\server.crt
} else {
    uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8000
}
