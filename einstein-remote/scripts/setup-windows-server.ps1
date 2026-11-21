<#
  EINSTEIN REMOTE - prepara este PC Windows como servidor, acessível do celular em qualquer lugar (4G).

  Uso (PowerShell, na pasta einstein-remote):
      powershell -ExecutionPolicy Bypass -File scripts\setup-windows-server.ps1

  O que faz (pode rodar de novo quantas vezes quiser):
    1. instala o Python 3.12 e o Tailscale se faltarem (winget)
    2. prepara o servidor (.venv com as dependências)
    3. conecta ao Tailscale (você entra na sua conta uma vez) e gera o certificado HTTPS do PC
    4. libera a porta 8443 no firewall SOMENTE para a rede Tailscale e a rede local
    5. cria a tarefa "EINSTEIN REMOTE Servidor" (inicia com o PC, reinicia se cair)
       e a tarefa "EINSTEIN REMOTE Certificado" (renova o certificado todo dia)
    6. impede o PC de suspender na tomada, inclusive com a tampa fechada (use -KeepSleep para não mexer)
    7. liga o servidor, confere e mostra o endereço para abrir no celular

  Parar o servidor:  ... setup-windows-server.ps1 -Stop      (ligar de novo: rode o script sem opções)
  Remover tudo:  ... setup-windows-server.ps1 -Uninstall
  Sem Tailscale (certificado próprio já em certs\server.crt/.key):  -SkipTailscale
#>
param(
    [switch]$Uninstall,
    [switch]$Stop,
    [switch]$SkipTailscale,
    [switch]$KeepSleep,
    [string]$Python = "",
    [string]$RunAsUser = ""
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TaskName = "EINSTEIN REMOTE Servidor"
$RenewName = "EINSTEIN REMOTE Certificado"
$RuleName = "EINSTEIN REMOTE servidor (8443)"
$Port = 8443
if (-not $RunAsUser) { $RunAsUser = "$env:USERDOMAIN\$env:USERNAME" }

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    ok  $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host ""; Write-Host "ERRO: $msg" -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------ administrador
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Pedindo permissão de administrador (para a tarefa de início automático e o firewall)..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "`"$PSCommandPath`"", "-RunAsUser", "`"$RunAsUser`"")
    foreach ($k in $PSBoundParameters.Keys) {
        if ($k -eq "RunAsUser") { continue }
        $v = $PSBoundParameters[$k]
        if ($v -is [switch]) { if ($v.IsPresent) { $argList += "-$k" } } else { $argList += @("-$k", "`"$v`"") }
    }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -WorkingDirectory $Root
    exit 0
}
Set-Location $Root

# ------------------------------------------------------------------ parar / remover
function Stop-Server {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # garante que nenhum processo do servidor DESTA pasta continue rodando
    $venv = (Join-Path $Root ".venv").ToLower()
    # lista completa ANTES de encerrar (o processo filho é reconhecido pelo pai, que está na .venv)
    $found = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine -match "uvicorn" -and $_.CommandLine -match "app\.main:app" -and
        ((($_.ExecutablePath + "").ToLower().StartsWith($venv)) -or ($_.CommandLine.ToLower().Contains($venv)) -or
         ((Get-CimInstance Win32_Process -Filter "ProcessId = $($_.ParentProcessId)" -ErrorAction SilentlyContinue).ExecutablePath + "").ToLower().StartsWith($venv))
    })
    foreach ($proc in $found) { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue }
}
if ($Stop) {
    Step "Parando o servidor"
    Stop-Server
    Ok "servidor parado (volta sozinho no próximo início do PC; para ligar agora, rode o script sem opções)"
    exit 0
}
if ($Uninstall) {
    Step "Removendo o início automático do servidor"
    Stop-Server
    foreach ($t in @($TaskName, $RenewName)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Ok "tarefa '$t' removida"
        }
    }
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Ok "regra de firewall removida"
    Write-Host ""
    Write-Host "Pronto. Seus dados (conta, dispositivos) continuam em $Root\data." -ForegroundColor Green
    exit 0
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Winget-Install($id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget não encontrado. Instale '$id' manualmente e rode este script de novo."
    }
    winget install -e --id $id --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { Fail "winget não conseguiu instalar $id (código $LASTEXITCODE)" }
    Refresh-Path
}

# ------------------------------------------------------------------ 1. Python
Step "Python"
function Find-Python {
    if ($Python) { return $Python }
    foreach ($ver in @("-3.12", "-3.13", "-3.11", "-3")) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $exe = & py $ver -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) {
                $ok = & $exe -c "import sys; print(sys.version_info >= (3, 10))"
                if ($ok -eq "True") { return $exe }
            }
        }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Write-Host "    Python não encontrado - instalando o Python 3.12..."
    Winget-Install "Python.Python.3.12"
    $py = Find-Python
    if (-not $py) { Fail "Python instalado, mas não encontrado. Feche esta janela e rode o script de novo." }
}
Ok "Python: $py"

# ------------------------------------------------------------------ 2. servidor
Step "Preparando o servidor"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $Root ".venv"); if ($LASTEXITCODE -ne 0) { Fail "não foi possível criar .venv" } }
& $venvPy -m pip install --disable-pip-version-check -q --upgrade pip
& $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Root "server\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "falha ao instalar as dependências do servidor" }
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data"), (Join-Path $Root "certs") | Out-Null
Ok "dependências instaladas em .venv"

# ------------------------------------------------------------------ 3. Tailscale + certificado
$certFile = Join-Path $Root "certs\server.crt"
$keyFile = Join-Path $Root "certs\server.key"
$hostName = "127.0.0.1"
if ($SkipTailscale) {
    Step "Certificado (sem Tailscale)"
    if (-not ((Test-Path $certFile) -and (Test-Path $keyFile))) { Fail "faltam certs\server.crt e certs\server.key (gere com scripts\gen-cert.sh)" }
    Ok "usando o certificado existente em certs\"
} else {
    Step "Tailscale (acesso do celular em qualquer lugar)"
    $ts = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (-not (Test-Path $ts)) {
        Write-Host "    Instalando o Tailscale..."
        Winget-Install "Tailscale.Tailscale"
        if (-not (Test-Path $ts)) { Fail "Tailscale não encontrado após a instalação ($ts)" }
    }
    function Ts-Status { try { (& $ts status --json 2>$null | Out-String) | ConvertFrom-Json } catch { $null } }
    $st = Ts-Status
    if (-not $st -or $st.BackendState -ne "Running") {
        $gui = Join-Path $env:ProgramFiles "Tailscale\tailscale-ipn.exe"
        if (Test-Path $gui) { Start-Process $gui }
        Write-Host ""
        Write-Host "    >>> Entre na sua conta Tailscale: clique no ícone do Tailscale perto do relógio > Log in." -ForegroundColor Yellow
        Write-Host "    >>> No celular, instale o app Tailscale e entre com a MESMA conta." -ForegroundColor Yellow
        Write-Host "    Aguardando o login (até 10 minutos)..."
        $deadline = (Get-Date).AddMinutes(10)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 3
            $st = Ts-Status
            if ($st -and $st.BackendState -eq "Running") { break }
        }
        if (-not $st -or $st.BackendState -ne "Running") { Fail "o Tailscale não conectou. Entre na conta e rode o script de novo." }
    }
    $hostName = ($st.Self.DNSName).TrimEnd(".")
    if (-not $hostName) { Fail "o Tailscale não informou o nome do PC. Ative o MagicDNS em https://login.tailscale.com/admin/dns" }
    Ok "conectado como $hostName"

    Step "Certificado HTTPS do Tailscale"
    while ($true) {
        & $ts cert --cert-file $certFile --key-file $keyFile $hostName
        if ($LASTEXITCODE -eq 0) { break }
        Write-Host ""
        Write-Host "    >>> Ative 'HTTPS Certificates' no painel do Tailscale (abrindo no navegador)." -ForegroundColor Yellow
        Start-Process "https://login.tailscale.com/admin/dns"
        Read-Host "    Depois de clicar em 'Enable', pressione Enter para tentar de novo"
    }
    Set-Content -Path (Join-Path $Root "data\tailscale-name.txt") -Value $hostName -Encoding ASCII
    Ok "certificado válido para $hostName (renovado automaticamente)"
}

# ------------------------------------------------------------------ 4. firewall
Step "Firewall"
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
    -RemoteAddress @("100.64.0.0/10", "LocalSubnet") -Profile Any `
    -Description "Painel EINSTEIN REMOTE: só aparelhos da sua rede Tailscale e da rede local" | Out-Null
Ok "porta $Port liberada só para a rede Tailscale (100.64.0.0/10) e a rede local"

# ------------------------------------------------------------------ 5. tarefas agendadas
Step "Início automático com o Windows"
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Limited
$service = Join-Path $Root "scripts\server-service.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $Root `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$service`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT1M"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
Stop-Server
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Servidor do painel EINSTEIN REMOTE (HTTPS porta $Port). Remover: scripts\setup-windows-server.ps1 -Uninstall" -Force | Out-Null
Ok "tarefa '$TaskName' (inicia com o PC, mesmo antes de entrar no Windows; reinicia se cair)"
# o agente da bandeja usa isto em "Reparar o servidor deste PC" / "Diagnóstico"
try {
    $hintDir = Join-Path $env:LOCALAPPDATA "EinsteinRemote"
    New-Item -ItemType Directory -Force -Path $hintDir | Out-Null
    Set-Content -Path (Join-Path $hintDir "server-dir.txt") -Value $Root -Encoding UTF8
} catch {}

if (-not $SkipTailscale) {
    $renew = Join-Path $Root "scripts\renew-cert.ps1"
    $rAction = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $Root `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$renew`""
    $rTrigger = New-ScheduledTaskTrigger -Daily -At "04:17"
    $rSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    # roda como SYSTEM: precisa reiniciar a tarefa do servidor quando o certificado muda
    $rPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $RenewName -Action $rAction -Trigger $rTrigger -Settings $rSettings -Principal $rPrincipal `
        -Description "Renova o certificado HTTPS do Tailscale para o EINSTEIN REMOTE" -Force | Out-Null
    Ok "tarefa '$RenewName' (renova o certificado todo dia às 04:17)"
}

# ------------------------------------------------------------------ 6. energia
if (-not $KeepSleep) {
    Step "Energia"
    powercfg /change standby-timeout-ac 0 | Out-Null
    powercfg /change hibernate-timeout-ac 0 | Out-Null
    # notebook: fechar a tampa NA TOMADA não suspende mais (na bateria continua suspendendo, para não esquentar na mochila)
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    Ok "na tomada o PC não suspende mais - nem com a tampa fechada (a tela ainda pode desligar)"
}

# ------------------------------------------------------------------ 7. ligar e conferir
Step "Ligando o servidor"
Start-ScheduledTask -TaskName $TaskName
$url = "https://${hostName}:$Port"
$curlArgs = @("-s", "--max-time", "4")
if ($SkipTailscale) { $curlArgs += "-k" }  # certificado próprio: o PC não confia nele por padrão
$okHealth = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 2
    $out = & curl.exe @curlArgs "$url/api/health" 2>$null
    if ($out -match '"status":"ok"') { $okHealth = $true; break }
}
if (-not $okHealth) {
    Write-Host ""
    Write-Host "Últimas linhas de data\server.log:" -ForegroundColor Yellow
    if (Test-Path (Join-Path $Root "data\server.log")) { Get-Content (Join-Path $Root "data\server.log") -Tail 25 }
    Fail "o servidor não respondeu em $url"
}
Ok "servidor respondendo: $out"

Write-Host ""
Write-Host "================================================================" -ForegroundColor Magenta
Write-Host "  EINSTEIN REMOTE está no ar" -ForegroundColor Magenta
Write-Host "  Abra no celular (com o app Tailscale ligado):" -ForegroundColor Magenta
Write-Host "      $url" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Magenta
Write-Host "  1. No primeiro acesso, crie a conta do proprietário e ative o 2FA."
Write-Host "  2. Dispositivos > Gerar código > no agente da bandeja: Vincular,"
Write-Host "     servidor $url (sem arquivo de certificado)."
Write-Host "  3. No agente, ative 'Iniciar com o Windows'."
Write-Host "  Registro do servidor: $Root\data\server.log"
Write-Host "  Desfazer tudo: scripts\setup-windows-server.ps1 -Uninstall"
if (-not $SkipTailscale) { Start-Process $url }
