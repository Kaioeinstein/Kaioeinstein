# EINSTEIN REMOTE - executado pela tarefa agendada "EINSTEIN REMOTE Servidor" (criada por setup-windows-server.ps1).
# Liga o servidor HTTPS na porta 8443 e o religa se cair. Mensagens: data\server.log (a execução anterior: server.log.1).
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $data | Out-Null
$log = Join-Path $data "server.log"
$out = Join-Path $data "server-out.log"
$env:EINSTEIN_COOKIE_SECURE = "1"
$env:PYTHONUNBUFFERED = "1"
$py = Join-Path $Root ".venv\Scripts\python.exe"
# se o servidor cair, volta em 5 s; parar a tarefa encerra este laço
while ($true) {
    foreach ($f in @($log, $out)) { if (Test-Path $f) { Move-Item $f "$f.1" -Force } }
    $p = Start-Process -FilePath $py -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
        -RedirectStandardError $log -RedirectStandardOutput $out -ArgumentList @(
            "-m", "uvicorn", "app.main:app", "--app-dir", "server", "--host", "0.0.0.0", "--port", "8443",
            "--ssl-keyfile", "certs\server.key", "--ssl-certfile", "certs\server.crt")
    Start-Sleep -Seconds 5
}
