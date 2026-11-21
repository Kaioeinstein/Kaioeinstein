# EINSTEIN REMOTE - executado pela tarefa agendada "EINSTEIN REMOTE Servidor" (criada por setup-windows-server.ps1).
# Liga o servidor HTTPS na porta 8443 e o religa se cair. Mensagens: data\server.log (a execução anterior: server.log.1).
# Atualização pelo painel: o servidor grava data\update\requested e se encerra; este laço roda scripts\update.ps1,
# religa e confere a versão nova — se ela não responder em 90 s, a anterior é restaurada.
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $data | Out-Null
$log = Join-Path $data "server.log"
$out = Join-Path $data "server-out.log"
$requested = Join-Path $data "update\requested"
$env:EINSTEIN_COOKIE_SECURE = "1"
$env:EINSTEIN_SELF_UPDATE = "1"
$env:PYTHONUNBUFFERED = "1"
$py = Join-Path $Root ".venv\Scripts\python.exe"

function Start-Server {
    foreach ($f in @($log, $out)) { if (Test-Path $f) { Move-Item $f "$f.1" -Force } }
    Start-Process -FilePath $py -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardError $log -RedirectStandardOutput $out -ArgumentList @(
            "-m", "uvicorn", "app.main:app", "--app-dir", "server", "--host", "0.0.0.0", "--port", "8443",
            "--ssl-keyfile", "certs\server.key", "--ssl-certfile", "certs\server.crt")
}
function Healthy([string]$version) {
    $o = & curl.exe -s -k --max-time 3 "https://127.0.0.1:8443/api/health" 2>$null
    return ($o -match '"status":"ok"') -and (-not $version -or $o -match ('"version":"' + [regex]::Escape($version) + '"'))
}
function Target {
    try { return (Get-Content (Join-Path $data "update\state.json") -Raw | ConvertFrom-Json).to } catch { return $null }
}

# se o servidor cair, volta em 5 s; parar a tarefa encerra este laço
while ($true) {
    $updated = $false
    if (Test-Path $requested) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\update.ps1") -Root $Root
        $updated = ($LASTEXITCODE -eq 0)
    }
    $p = Start-Server
    if ($updated) {
        $target = Target
        $ok = $false
        for ($i = 0; $i -lt 45 -and -not $p.HasExited; $i++) {
            Start-Sleep -Seconds 2
            if (Healthy $target) { $ok = $true; break }
        }
        if ($ok) {
            $newPkg = Join-Path $data "update\agent-new"
            if (Test-Path $newPkg) {  # servidor novo respondeu: o pacote novo do agente passa a ser oferecido aos PCs
                Remove-Item (Join-Path $data "update\agent") -Recurse -Force -ErrorAction SilentlyContinue
                Move-Item $newPkg (Join-Path $data "update\agent")
            }
            try {
                $sf = Join-Path $data "update\state.json"
                $st = Get-Content $sf -Raw | ConvertFrom-Json
                $st.phase = "done"
                $st.message = "Atualizado para a versão $($st.to)."
                ($st | ConvertTo-Json -Compress) | Set-Content $sf -Encoding UTF8
            } catch {}
        } else {
            if (-not $p.HasExited) { taskkill.exe /PID $p.Id /T /F | Out-Null }  # a árvore toda (o python real é filho)
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\update.ps1") -Root $Root -Restore
            Start-Sleep -Seconds 2
            $p = Start-Server
        }
    }
    $p.WaitForExit()
    Start-Sleep -Seconds 5
}
