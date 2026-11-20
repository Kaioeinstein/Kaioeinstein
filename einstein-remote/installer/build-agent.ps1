<#
  Gera os executaveis do EINSTEIN REMOTE AGENT (Windows).
  Uso (PowerShell, na pasta do projeto):
    powershell -ExecutionPolicy Bypass -File installer\build-agent.ps1
  Resultado: dist\EinsteinRemoteAgent\ e dist\EinsteinRemoteAgent-<versao>-win64.zip
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# EINSTEIN_PYTHON permite escolher o interpretador (usado no CI)
if ($env:EINSTEIN_PYTHON) { $python = $env:EINSTEIN_PYTHON; $pyArgs = @() }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $python = "py"; $pyArgs = @("-3") }
else { $python = "python"; $pyArgs = @() }

$venv = Join-Path $root ".build-venv"
if (-not (Test-Path $venv)) { & $python @pyArgs -m venv $venv }
$vpy = Join-Path $venv "Scripts\python.exe"
& $vpy -m pip install --upgrade pip | Out-Null
& $vpy -m pip install pyinstaller (Join-Path $root "agent")

& $vpy -m PyInstaller installer\agent.spec --noconfirm --clean --distpath dist --workpath installer\build\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

$version = & $vpy -c "import einstein_agent; print(einstein_agent.__version__)"
& "dist\EinsteinRemoteAgent\einstein-agent.exe" --version
$zip = "dist\EinsteinRemoteAgent-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\EinsteinRemoteAgent\*" -DestinationPath $zip
Write-Host ""
Write-Host "Pronto:" -ForegroundColor Green
Write-Host "  dist\EinsteinRemoteAgent\EinsteinRemoteAgent.exe  (bandeja)"
Write-Host "  dist\EinsteinRemoteAgent\einstein-agent.exe       (linha de comando)"
Write-Host "  $zip"
