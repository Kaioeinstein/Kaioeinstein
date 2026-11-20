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

Write-Host "5. Remover"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\setup-windows-server.ps1 -Uninstall
Check (-not (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue)) "tarefa removida"
Check (-not (Get-NetFirewallRule -DisplayName "EINSTEIN REMOTE servidor (8443)" -ErrorAction SilentlyContinue)) "regra de firewall removida"
Check (Wait-Down 20) "servidor desligado"
Write-Host ""
Write-Host "Servidor Windows (início automático) OK." -ForegroundColor Green
