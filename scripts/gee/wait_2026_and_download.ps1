# Espera a que terminen las tareas S2_2026W* y luego re-descarga TIFs locales 2026 + build.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Repo

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$Expected = 28
$PollSeconds = 120
$LogFile = Join-Path $Repo "logs/wait_2026_and_download.log"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null

function Write-Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

Write-Log "Esperando $Expected tareas S2_2026W* (COMPLETED)..."

while ($true) {
    $status = python -u -c @"
import sys
sys.path.insert(0, 'scripts/gee')
import ee
ee.Initialize(project='teleambagr')
from export_s2 import list_child_asset_ids, DEFAULT_EXPORT_PREFIX
tasks = ee.batch.Task.list()
states = {}
active = 0
done = 0
failed = 0
for t in tasks[:120]:
    try:
        st = t.status()
        desc = str(st.get('description') or '')
        if not desc.startswith('S2_2026W'):
            continue
        state = st.get('state', '?')
        states[state] = states.get(state, 0) + 1
        if state in ('READY', 'RUNNING'):
            active += 1
        elif state == 'COMPLETED':
            done += 1
        elif state in ('FAILED', 'CANCELLED'):
            failed += 1
    except Exception:
        pass
kids = list_child_asset_ids(DEFAULT_EXPORT_PREFIX)
y26 = sum(1 for k in kids if '/Y2026_W' in k)
print(f'states={states!r}')
print(f'done={done}')
print(f'active={active}')
print(f'failed={failed}')
print(f'assets_y2026={y26}')
"@

    $done = 0
    $active = 0
    $failed = 0
    $assets = 0
    $taskLine = ""
    foreach ($line in ($status -split "`n")) {
        if ($line -match '^done=(\d+)') { $done = [int]$matches[1] }
        if ($line -match '^active=(\d+)') { $active = [int]$matches[1] }
        if ($line -match '^failed=(\d+)') { $failed = [int]$matches[1] }
        if ($line -match '^assets_y2026=(\d+)') { $assets = [int]$matches[1] }
        if ($line -match '^states=') { $taskLine = $line }
    }
    Write-Log "tasks: done=$done active=$active failed=$failed assets_y2026=$assets | $taskLine"

    if ($active -eq 0 -and ($done -ge $Expected -or $assets -ge $Expected)) {
        Write-Log "Tareas 2026 listas. Re-descargando TIFs locales..."
        break
    }
    if ($active -eq 0 -and $failed -gt 0 -and $done -lt $Expected) {
        Write-Log "AVISO: no quedan tareas activas y faltan semanas (done=$done failed=$failed). Se intenta descarga con lo disponible."
        break
    }

    Start-Sleep -Seconds $PollSeconds
}

Write-Log "=== 1/2 Borrar TIFs locales 2026 y re-descargar ==="
Get-ChildItem "data\sentinel2\S2_*_Y2026_W*.tif" -ErrorAction SilentlyContinue | Remove-Item -Force
python scripts/gee/export_s2_predio_local.py --reference E_SAZO --sync-ee-weeks --fill-missing-weeks --all-predios --force --year 2026
if ($LASTEXITCODE -ne 0) {
    Write-Log "Export local fallo (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Log "=== 2/2 Build estatico Sentinel-2 ==="
python scripts/static_site/build_sentinel2_local.py --force
$code = $LASTEXITCODE
Write-Log "Build terminado (exit $code)."
exit $code
