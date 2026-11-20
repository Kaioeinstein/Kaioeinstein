# EINSTEIN REMOTE - renova o certificado HTTPS do Tailscale (tarefa "EINSTEIN REMOTE Certificado", diária).
# O Tailscale só emite um novo quando o atual está perto de vencer; se mudar, o servidor é reiniciado.
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$nameFile = Join-Path $Root "data\tailscale-name.txt"
if (-not (Test-Path $nameFile)) { exit 0 }
$name = (Get-Content $nameFile -Raw).Trim()
$cert = Join-Path $Root "certs\server.crt"
$key = Join-Path $Root "certs\server.key"
$before = if (Test-Path $cert) { (Get-FileHash $cert).Hash } else { "" }
Start-Process -FilePath (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe") -NoNewWindow -Wait `
    -RedirectStandardError (Join-Path $Root "data\cert-renew.log") -RedirectStandardOutput (Join-Path $Root "data\cert-renew-out.log") `
    -ArgumentList @("cert", "--cert-file", "`"$cert`"", "--key-file", "`"$key`"", $name)
$after = if (Test-Path $cert) { (Get-FileHash $cert).Hash } else { "" }
if ($after -and $after -ne $before) {
    & (Join-Path $PSScriptRoot "setup-windows-server.ps1") -Stop | Out-Null
    Start-Sleep -Seconds 3
    Start-ScheduledTask -TaskName "EINSTEIN REMOTE Servidor"
}
