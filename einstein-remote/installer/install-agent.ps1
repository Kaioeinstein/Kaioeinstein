<#
  EINSTEIN REMOTE AGENT - instalador (Fase 1)
  Instala o agente em %LOCALAPPDATA%\EinsteinRemote\agent num ambiente Python isolado
  e cria o comando "einstein-agent". NAO ativa inicializacao automatica: isso so acontece
  se o proprietario executar "einstein-agent autostart enable".

  Uso (PowerShell, na pasta do projeto):
    powershell -ExecutionPolicy Bypass -File installer\install-agent.ps1
#>
$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$target  = Join-Path $env:LOCALAPPDATA "EinsteinRemote\agent"
$venv    = Join-Path $target "venv"

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $python = "py"; $pyArgs = @("-3") } else {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    $pyArgs = @()
    if (-not $python) { throw "Python 3.10+ nao encontrado. Instale em https://www.python.org/downloads/ (marque 'Add to PATH')." }
}

Write-Host "==> Criando ambiente em $venv"
New-Item -ItemType Directory -Force -Path $target | Out-Null
& $python @pyArgs -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
Write-Host "==> Instalando o agente"
& "$venv\Scripts\python.exe" -m pip install (Join-Path $root "agent")

$bin = Join-Path $target "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Set-Content -Path (Join-Path $bin "einstein-agent.cmd") -Value "@`"$venv\Scripts\einstein-agent.exe`" %*" -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$bin", "User")
    Write-Host "==> $bin adicionado ao PATH do usuario (abra um novo terminal)."
}
Write-Host ""
Write-Host "Instalado! Proximos passos:" -ForegroundColor Green
Write-Host "  1. No dashboard: Dispositivos > Gerar codigo"
Write-Host "  2. einstein-agent pair --server https://SEU-SERVIDOR:8443 --code XXXX-XXXX [--ca-file ca.crt]"
Write-Host "  3. einstein-agent run"
Write-Host "  (opcional) einstein-agent autostart enable"
