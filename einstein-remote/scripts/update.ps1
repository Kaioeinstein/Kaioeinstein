# EINSTEIN REMOTE - atualização do servidor (chamado por server-service.ps1 quando o dono toca em "Atualizar").
# 1. baixa o código do repositório do projeto (endereço fixo, HTTPS)   2. guarda cópia da versão atual
# 3. troca os arquivos do programa (dados, chaves e certificados ficam)  4. instala dependências
# 5. monta o pacote do agente para os PCs se atualizarem sozinhos
# Se a versão nova não responder, server-service.ps1 restaura a cópia (Restore-Backup abaixo).
param([string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path, [switch]$Restore)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest fica muito mais rápido
$Up = Join-Path $Root "data\update"
$Dirs = @("server", "web", "scripts", "agent", "installer")
$ZipUrl = "https://codeload.github.com/Kaioeinstein/Kaioeinstein/zip/refs/heads/ccr-729bf759-udovmu"
New-Item -ItemType Directory -Force -Path $Up | Out-Null
$log = Join-Path $Up "update.log"

function Log($m) { [IO.File]::AppendAllText($log, "$(Get-Date -Format s) $m`r`n") }
function State($phase, $msg, $extra = @{}) {
    $s = @{ phase = $phase; message = $msg; ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
    $old = $null
    try { $old = Get-Content (Join-Path $Up "state.json") -Raw | ConvertFrom-Json } catch {}
    foreach ($k in @("from", "to", "by")) { if ($old -and $old.$k) { $s[$k] = $old.$k } }
    foreach ($k in $extra.Keys) { $s[$k] = $extra[$k] }
    ($s | ConvertTo-Json -Compress) | Set-Content (Join-Path $Up "state.json") -Encoding UTF8
    Log "[$phase] $msg"
}
function Version($dir) {
    $t = Get-Content (Join-Path $dir "server\app\__init__.py") -Raw
    if ($t -match '__version__\s*=\s*"(\d+\.\d+\.\d+)"') { return $Matches[1] }
    return "0.0.0"
}
# programas externos: as mensagens deles (stderr) não podem virar erro fatal no PowerShell 5.1
function Run([string]$exe, [string[]]$argv, [string]$name) {
    $quoted = $argv | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
    $p = Start-Process -FilePath $exe -ArgumentList $quoted -WorkingDirectory $Root -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $Up "$name.out.log") -RedirectStandardError (Join-Path $Up "$name.err.log")
    return $p.ExitCode
}
function Mirror($from, $to) {
    robocopy $from $to /MIR /XD __pycache__ build /XF *.pyc /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "falha ao copiar $from (robocopy $LASTEXITCODE)" }
}

# ------------------------------------------------------------------ restaurar (chamado se a nova não subir)
if ($Restore) {
    $bk = Join-Path $Up "backup"
    if (-not (Test-Path (Join-Path $bk "server"))) { Log "sem cópia para restaurar"; exit 1 }
    foreach ($d in $Dirs) { if (Test-Path (Join-Path $bk $d)) { Mirror (Join-Path $bk $d) (Join-Path $Root $d) } }
    Run (Join-Path $Root ".venv\Scripts\python.exe") @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", (Join-Path $Root "server\requirements.txt")) "pip-restore" | Out-Null
    Remove-Item (Join-Path $Up "agent-new") -Recurse -Force -ErrorAction SilentlyContinue
    State "rolled_back" "A versão nova não respondeu; a anterior foi restaurada."
    exit 0
}

$req = Join-Path $Up "requested"
$by = "painel"
try { $by = (Get-Content $req -Raw | ConvertFrom-Json).by } catch {}
Remove-Item $req -Force -ErrorAction SilentlyContinue
$current = Version $Root
$swapped = $false
try {
    State "downloading" "Baixando a versão nova…" @{ from = $current; by = $by }
    $zip = Join-Path $Up "src.zip"
    $src = Join-Path $Up "src"
    $local = Join-Path $Up "local-source.zip"  # pacote local (testes ou PC sem internet), usado uma vez
    if (Test-Path $local) { Move-Item $local $zip -Force }
    else { Invoke-WebRequest $ZipUrl -OutFile $zip -UseBasicParsing -TimeoutSec 300 }
    if (Test-Path $src) { Remove-Item $src -Recurse -Force }
    Expand-Archive $zip $src -Force
    $new = Get-ChildItem $src -Directory -Recurse -Depth 1 | Where-Object { Test-Path (Join-Path $_.FullName "server\app\__init__.py") } | Select-Object -First 1
    if (-not $new) { throw "pacote baixado não contém o servidor" }
    $newRoot = $new.FullName
    $target = Version $newRoot
    State "installing" "Instalando a versão $target…" @{ to = $target }

    $bk = Join-Path $Up "backup"
    if (Test-Path $bk) { Remove-Item $bk -Recurse -Force }  # a cópia é sempre da versão em uso AGORA
    foreach ($d in $Dirs) { if (Test-Path (Join-Path $Root $d)) { Mirror (Join-Path $Root $d) (Join-Path $bk $d) } }
    $swapped = $true
    foreach ($d in $Dirs) { if (Test-Path (Join-Path $newRoot $d)) { Mirror (Join-Path $newRoot $d) (Join-Path $Root $d) } }
    foreach ($f in @("CHANGELOG.md", "README.md")) { if (Test-Path (Join-Path $newRoot $f)) { Copy-Item (Join-Path $newRoot $f) $Root -Force } }
    $code = Run (Join-Path $Root ".venv\Scripts\python.exe") @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", (Join-Path $Root "server\requirements.txt")) "pip-server"
    if ($code -ne 0) { throw "falha ao instalar as dependências do servidor (veja data\update\pip-server.err.log)" }

    # pacote do agente (os PCs vinculados se atualizam sozinhos ao conectar)
    try {
        State "building_agent" "Montando o agente $target (alguns minutos)…"
        $bv = Join-Path $Root ".build-venv"
        if (-not (Test-Path (Join-Path $bv "Scripts\python.exe"))) {
            Run (Join-Path $Root ".venv\Scripts\python.exe") @("-m", "venv", $bv) "venv-build" | Out-Null
        }
        $bpy = Join-Path $bv "Scripts\python.exe"
        if ((Run $bpy @("-m", "pip", "install", "--disable-pip-version-check", "-q", "pyinstaller", (Join-Path $Root "agent")) "pip-agent") -ne 0) { throw "pip (agente) falhou" }
        $dist = Join-Path $Up "dist"
        $code = Run $bpy @("-m", "PyInstaller", "installer\agent.spec", "--noconfirm", "--clean", "--distpath", $dist, "--workpath", (Join-Path $Up "work")) "pyinstaller"
        $pkgDir = Join-Path $dist "EinsteinRemoteAgent"
        if (-not (Test-Path (Join-Path $pkgDir "EinsteinRemoteAgent.exe"))) { throw "PyInstaller não gerou o agente" }
        $agentDir = Join-Path $Up "agent-new"  # só vira o pacote oferecido depois que o servidor novo responder
        if (Test-Path $agentDir) { Remove-Item $agentDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $agentDir | Out-Null
        $file = "EinsteinRemoteAgent-$target.zip"
        Compress-Archive -Path (Join-Path $pkgDir "*") -DestinationPath (Join-Path $agentDir $file)
        $item = Get-Item (Join-Path $agentDir $file)
        @{ version = $target; file = $file; size = $item.Length; sha256 = (Get-FileHash $item.FullName -Algorithm SHA256).Hash.ToLower() } |
            ConvertTo-Json -Compress | Set-Content (Join-Path $agentDir "package.json") -Encoding UTF8
        Remove-Item $dist, (Join-Path $Up "work") -Recurse -Force -ErrorAction SilentlyContinue
        Log "pacote do agente pronto: $file"
    } catch {
        Log "pacote do agente não montado: $_ (o servidor foi atualizado mesmo assim)"
    }
    Remove-Item $zip, $src -Recurse -Force -ErrorAction SilentlyContinue
    State "restarting" "Religando o servidor na versão $target…"
    exit 0
} catch {
    State "failed" "Falha na atualização: $_ (nada foi alterado ou a versão anterior será restaurada)"
    # só restaura se já tinha trocado arquivos (antes disso, nada mudou)
    if ($swapped) {
        try { & $PSCommandPath -Root $Root -Restore | Out-Null } catch {}
    }
    exit 1
}
