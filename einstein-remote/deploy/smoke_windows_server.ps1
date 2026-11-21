# Teste (CI Windows) do servidor como tarefa agendada: scripts\setup-windows-server.ps1 -SkipTailscale
#   powershell -ExecutionPolicy Bypass -File deploy\smoke_windows_server.ps1 -Python <python.exe>
# Requer administrador e certs\server.crt/.key (scripts/gen-cert.sh localhost 127.0.0.1).
param([string]$Python = "")
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$Task = "EINSTEIN REMOTE Servidor"
$Url = "https://127.0.0.1:8443/api/health"

function Check($cond, $msg) { if (-not $cond) { Write-Host "FALHA: $msg" -ForegroundColor Red; Show-Log; exit 1 }; Write-Host "  ok  $msg" }
function Show-Log { $l = Join-Path $Root "data\server.log"; if (Test-Path $l) { Write-Host "--- data\server.log"; Get-Content $l -Tail 30 } }
function Healthy { $o = & curl.exe -s -k --max-time 3 $Url 2>$null; return ($o -match '"status":"ok"') }
function Wait-Healthy([int]$sec) { for ($i = 0; $i -lt $sec; $i += 2) { if (Healthy) { return $true }; Start-Sleep 2 }; return $false }
function Health-Version { $o = & curl.exe -s -k --max-time 3 $Url 2>$null; if ($o -match '"version":"([^"]+)"') { return $Matches[1] }; return $null }
function Wait-Version([string]$v, [int]$sec) { for ($i = 0; $i -lt $sec; $i += 5) { if ((Health-Version) -eq $v) { return $true }; Start-Sleep 5 }; return $false }
function State { try { return Get-Content (Join-Path $Root "data\update\state.json") -Raw | ConvertFrom-Json } catch { return $null } }
# pacote como o do GitHub (pasta raiz + einstein-remote), com outra versão e, opcionalmente, um servidor quebrado
function Make-Source([string]$version, [switch]$Broken) {
    $work = Join-Path $env:TEMP "einstein-src-$version"
    if (Test-Path $work) { Remove-Item $work -Recurse -Force }
    $dst = Join-Path $work "Kaioeinstein-teste\einstein-remote"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    foreach ($d in "server", "web", "scripts", "agent", "installer") {
        robocopy (Join-Path $Root $d) (Join-Path $dst $d) /E /XD __pycache__ build /NFL /NDL /NJH /NJS /NP | Out-Null
    }
    $init = Join-Path $dst "server\app\__init__.py"
    (Get-Content $init -Raw) -replace '__version__ = "[^"]+"', "__version__ = `"$version`"" | Set-Content $init -Encoding UTF8
    $ainit = Join-Path $dst "agent\einstein_agent\__init__.py"
    (Get-Content $ainit -Raw) -replace '__version__ = "[^"]+"', "__version__ = `"$version`"" | Set-Content $ainit -Encoding UTF8
    if ($Broken) { Set-Content (Join-Path $dst "server\app\main.py") 'raise SystemExit("versao quebrada de proposito")' -Encoding UTF8 }
    $zip = Join-Path $Root "data\update\local-source.zip"
    New-Item -ItemType Directory -Force -Path (Split-Path $zip) | Out-Null
    Compress-Archive -Path (Join-Path $work "*") -DestinationPath $zip -Force
}
function Request-Update {
    Set-Content (Join-Path $Root "data\update\requested") '{"by":"ci"}' -Encoding UTF8
    # o pedido normal vem do painel (o servidor se encerra); aqui encerramos o processo do servidor
    $l = Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($l) { Stop-Process -Id $l.OwningProcess -Force }
}
function Wait-Down([int]$sec) { for ($i = 0; $i -lt $sec; $i += 1) { if (-not (Healthy)) { return $true }; Start-Sleep 1 }; return $false }

Write-Host "1. Instalação (sem Tailscale, certificado próprio)"
$setupArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\setup-windows-server.ps1", "-SkipTailscale", "-KeepSleep")
if ($Python) { $setupArgs += @("-Python", $Python) }
& powershell.exe @setupArgs
Check ($LASTEXITCODE -eq 0) "setup-windows-server.ps1 concluiu"
Check (Healthy) "servidor HTTPS respondendo em $Url"
$t = Get-ScheduledTask -TaskName $Task
Check ($t.Triggers[0].CimClass.CimClassName -eq "MSFT_TaskBootTrigger") "tarefa inicia com o PC (AtStartup)"
Check ($t.Principal.LogonType -eq "S4U") "roda com o usuário, sem senha guardada (S4U)"
Check ([bool](Get-NetFirewallRule -DisplayName "EINSTEIN REMOTE servidor (8443)" -ErrorAction SilentlyContinue)) "regra de firewall criada"
$filter = Get-NetFirewallRule -DisplayName "EINSTEIN REMOTE servidor (8443)" | Get-NetFirewallAddressFilter
Check (($filter.RemoteAddress -join ",") -match "100.64.0.0") "firewall restrito à rede Tailscale/local ($($filter.RemoteAddress -join ', '))"

Write-Host "2. Parar (-Stop) encerra o servidor sem deixar processo"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\setup-windows-server.ps1 -Stop
Check (Wait-Down 20) "servidor parado"
Start-Sleep 8
Check (-not (Healthy)) "continua parado (o laço de religar foi encerrado junto)"

Write-Host "3. Ligar de novo"
Start-ScheduledTask -TaskName $Task
Check (Wait-Healthy 60) "servidor voltou"

Write-Host "4. Queda do servidor: a tarefa religa sozinha"
$listener = Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
Check ([bool]$listener) "processo do servidor encontrado (PID $($listener.OwningProcess))"
Stop-Process -Id $listener.OwningProcess -Force
Check (Wait-Down 15) "servidor caiu"
Check (Wait-Healthy 60) "servidor religado automaticamente"

Write-Host "5. Atualização pelo serviço (como o botão Atualizar do painel)"
$before = Health-Version
Make-Source "9.9.0"
Request-Update
Check (Wait-Version "9.9.0" 900) "servidor religou na versão nova (9.9.0; antes $before)"
Start-Sleep 5
Check ((State).phase -eq "done") "estado da atualização: concluída"
$pkg = Get-Content (Join-Path $Root "data\update\agent\package.json") -Raw | ConvertFrom-Json
Check ($pkg.version -eq "9.9.0" -and $pkg.sha256.Length -eq 64) "pacote do agente 9.9.0 montado (SHA-256 $($pkg.sha256.Substring(0, 12))…)"
Check (Test-Path (Join-Path $Root "data\update\backup\server\app\main.py")) "cópia da versão anterior guardada"

Write-Host "6. Versão nova quebrada: a anterior volta sozinha"
Make-Source "9.9.1" -Broken
Request-Update
Check (Wait-Version "9.9.0" 900) "servidor de volta na versão 9.9.0 após a falha"
Start-Sleep 5
Check ((State).phase -eq "rolled_back") "estado: versão anterior restaurada ($((State).message))"
Check (-not ((Get-Content (Join-Path $Root "server\app\main.py") -Raw) -match "quebrada")) "arquivos da versão quebrada removidos"
$pkg = Get-Content (Join-Path $Root "data\update\agent\package.json") -Raw | ConvertFrom-Json
Check ($pkg.version -eq "9.9.0" -and -not (Test-Path (Join-Path $Root "data\update\agent-new"))) "pacote do agente da versão quebrada descartado"

Write-Host "7. Remover"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\setup-windows-server.ps1 -Uninstall
Check (-not (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue)) "tarefa removida"
Check (-not (Get-NetFirewallRule -DisplayName "EINSTEIN REMOTE servidor (8443)" -ErrorAction SilentlyContinue)) "regra de firewall removida"
Check (Wait-Down 20) "servidor desligado"
Write-Host ""
Write-Host "Servidor Windows (início automático) OK." -ForegroundColor Green
