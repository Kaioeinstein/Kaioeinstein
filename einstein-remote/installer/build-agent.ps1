<#
  Gera os executaveis e o instalador do EINSTEIN REMOTE AGENT (Windows).
  Uso (PowerShell, na pasta do projeto):
    powershell -ExecutionPolicy Bypass -File installer\build-agent.ps1
  Resultado:
    dist\EinsteinRemoteAgent\                         (pasta do programa)
    dist\EinsteinRemoteAgent-<versao>-win64.zip       (versao portatil)
    dist\EinsteinRemoteAgent-<versao>-Setup.exe       (instalador; requer Inno Setup 6)

  Opcional:
    EINSTEIN_PYTHON         interpretador a usar
    EINSTEIN_ISCC           caminho do ISCC.exe (padrao: procura o Inno Setup 6 instalado)
    EINSTEIN_SIGN_PFX       certificado de assinatura de codigo (.pfx) — assina .exe e instalador
    EINSTEIN_SIGN_PASSWORD  senha do .pfx
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($env:EINSTEIN_PYTHON) { $python = $env:EINSTEIN_PYTHON; $pyArgs = @() }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $python = "py"; $pyArgs = @("-3") }
else { $python = "python"; $pyArgs = @() }

$venv = Join-Path $root ".build-venv"
if (-not (Test-Path $venv)) { & $python @pyArgs -m venv $venv }
$vpy = Join-Path $venv "Scripts\python.exe"
& $vpy -m pip install --upgrade pip | Out-Null
& $vpy -m pip install pyinstaller (Join-Path $root "agent")
if ($LASTEXITCODE -ne 0) { throw "pip install falhou" }

& $vpy -m PyInstaller installer\agent.spec --noconfirm --clean --distpath dist --workpath installer\build\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

$version = & $vpy -c "import einstein_agent; print(einstein_agent.__version__)"
& "dist\EinsteinRemoteAgent\einstein-agent.exe" --version
if ($LASTEXITCODE -ne 0) { throw "einstein-agent.exe nao executou" }

# ---------------------------------------------------------------- assinatura (opcional)
function Find-SignTool {
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (-not (Test-Path $kits)) { return $null }
    Get-ChildItem $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "\\x64\\" } | Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
$signtool = $null
if ($env:EINSTEIN_SIGN_PFX) {
    $signtool = Find-SignTool
    if (-not $signtool) { throw "EINSTEIN_SIGN_PFX definido, mas signtool.exe (Windows SDK) nao foi encontrado" }
}
function Sign-Files([string[]]$files) {
    if (-not $signtool) { return }
    & $signtool sign /f $env:EINSTEIN_SIGN_PFX /p $env:EINSTEIN_SIGN_PASSWORD /fd SHA256 `
        /tr http://timestamp.digicert.com /td SHA256 /d "EINSTEIN REMOTE AGENT" @files
    if ($LASTEXITCODE -ne 0) { throw "Assinatura falhou" }
}
Sign-Files @("dist\EinsteinRemoteAgent\EinsteinRemoteAgent.exe", "dist\EinsteinRemoteAgent\einstein-agent.exe")

# ---------------------------------------------------------------- zip portatil
$zip = "dist\EinsteinRemoteAgent-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\EinsteinRemoteAgent\*" -DestinationPath $zip

# ---------------------------------------------------------------- instalador (Inno Setup 6)
$iscc = $env:EINSTEIN_ISCC
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
$setup = "dist\EinsteinRemoteAgent-$version-Setup.exe"
if ($iscc) {
    & $iscc /Q "/DAppVersion=$version" "/DSourceDir=$root\dist\EinsteinRemoteAgent" installer\EinsteinRemoteAgent.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup falhou" }
    Sign-Files @($setup)
} else {
    Write-Warning "Inno Setup 6 nao encontrado (https://jrsoftware.org/isdl.php) — instalador nao gerado, apenas o .zip."
    $setup = $null
}

Write-Host ""
Write-Host "Pronto (versao $version):" -ForegroundColor Green
Write-Host "  dist\EinsteinRemoteAgent\EinsteinRemoteAgent.exe  (bandeja)"
Write-Host "  dist\EinsteinRemoteAgent\einstein-agent.exe       (linha de comando)"
Write-Host "  $zip"
if ($setup) { Write-Host "  $setup" }
if ($signtool) { Write-Host "  (executaveis assinados)" }
