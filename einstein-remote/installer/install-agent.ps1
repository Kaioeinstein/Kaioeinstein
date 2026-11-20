<#
  EINSTEIN REMOTE AGENT - instalador a partir do codigo-fonte (requer Python 3.10+).
  Para instalar sem Python, use o pacote gerado por installer\build-agent.ps1
  (ou o artefato "EinsteinRemoteAgent-win64" do GitHub Actions).

  O que faz:
    - instala o agente em %LOCALAPPDATA%\EinsteinRemote\agent (ambiente Python isolado)
    - cria o comando "einstein-agent" e um atalho "EINSTEIN REMOTE AGENT" no Menu Iniciar
    - abre o agente na bandeja; na primeira vez aparece a janela de vinculacao
  NAO ativa o inicio automatico: isso so acontece se o proprietario escolher
  "Iniciar com o Windows" no menu da bandeja (ou "einstein-agent autostart enable").

  Uso (PowerShell, na pasta do projeto):
    powershell -ExecutionPolicy Bypass -File installer\install-agent.ps1
#>
$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$target  = Join-Path $env:LOCALAPPDATA "EinsteinRemote\agent"
$venv    = Join-Path $target "venv"

if (Get-Command py -ErrorAction SilentlyContinue) { $python = "py"; $pyArgs = @("-3") }
else {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    $pyArgs = @()
    if (-not $python) { throw "Python 3.10+ nao encontrado. Instale em https://www.python.org/downloads/ (marque 'Add to PATH')." }
}

Write-Host "==> Criando ambiente em $venv"
New-Item -ItemType Directory -Force -Path $target | Out-Null
& $python @pyArgs -m venv $venv
$vpy = Join-Path $venv "Scripts\python.exe"
$vpyw = Join-Path $venv "Scripts\pythonw.exe"
& $vpy -m pip install --upgrade pip | Out-Null
Write-Host "==> Instalando o agente"
& $vpy -m pip install (Join-Path $root "agent")
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar o agente" }

# comando einstein-agent no PATH do usuario
$bin = Join-Path $target "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Set-Content -Path (Join-Path $bin "einstein-agent.cmd") -Value "@`"$venv\Scripts\einstein-agent.exe`" %*" -Encoding ASCII
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$bin", "User")
    Write-Host "==> $bin adicionado ao PATH do usuario (abra um novo terminal)."
}

# atalho no Menu Iniciar (visivel; abre a bandeja)
$ico = Join-Path $target "einstein.ico"
& $vpy -c "from einstein_agent.icons import save_ico; save_ico(r'$ico')"
$programs = [Environment]::GetFolderPath("Programs")
$lnk = Join-Path $programs "EINSTEIN REMOTE AGENT.lnk"
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $vpyw
$sc.Arguments = "-m einstein_agent tray"
$sc.IconLocation = $ico
$sc.Description = "EINSTEIN REMOTE AGENT - monitoramento autorizado pelo proprietario"
$sc.WorkingDirectory = $target
$sc.Save()
Write-Host "==> Atalho criado: Menu Iniciar > EINSTEIN REMOTE AGENT"

Write-Host ""
Write-Host "Instalado! Abrindo o agente na bandeja..." -ForegroundColor Green
Write-Host "  Na primeira vez, informe o endereco do servidor e o codigo gerado em Dispositivos > Gerar codigo."
Start-Process -FilePath $vpyw -ArgumentList "-m", "einstein_agent", "tray" -WorkingDirectory $target
