# Espera a que la ImageCollection S2_weekly_valpo tenga todas las imagenes semanales
# y luego baja recortes por predio + build estatico.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Repo

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$ExpectedImages = 448
$PollMinutes = 5
$LogFile = Join-Path $Repo "logs/wait_ic_and_build.log"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null

function Write-Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

while ($true) {
    $status = python -u -c @"
import sys
sys.path.insert(0, 'scripts/gee')
import ee
ee.Initialize(project='teleambagr')
from export_s2 import list_child_asset_ids, DEFAULT_EXPORT_PREFIX
tasks = ee.batch.Task.list()
states = {}
for t in tasks:
    try:
        st = t.status()['state']
        states[st] = states.get(st, 0) + 1
    except Exception:
        pass
prefix = DEFAULT_EXPORT_PREFIX
kids = list_child_asset_ids(prefix)
images = 0
for k in kids:
    try:
        if str(ee.data.getAsset(k).get('type','')).upper() == 'IMAGE':
            images += 1
    except Exception:
        pass
print(f'tasks={states!r}')
print(f'images={images}')
"@

    $images = 0
    foreach ($line in ($status -split "`n")) {
        if ($line -match '^images=(\d+)') { $images = [int]$matches[1] }
        if ($line -match '^tasks=') { $taskLine = $line }
    }
    Write-Log "IC: $images / $ExpectedImages imagenes | $taskLine"

    if ($images -ge $ExpectedImages) {
        Write-Log "Coleccion completa. Iniciando export local + build estatico."
        break
    }

    Start-Sleep -Seconds ($PollMinutes * 60)
}

$s2 = Join-Path $Repo "data/sentinel2"
if (Test-Path $s2) {
    $old = Get-ChildItem "$s2/S2_*.tif" -ErrorAction SilentlyContinue
    if ($old) {
        Write-Log "Eliminando $($old.Count) TIF locales antiguos (formato previo) en data/sentinel2/"
        Remove-Item "$s2/S2_*.tif" -Force
    }
}

Write-Log "=== 1/2 Export S2 por predio (calendario EE + semanas faltantes) ==="
python scripts/gee/export_s2_predio_local.py --reference E_SAZO --sync-ee-weeks --fill-missing-weeks
if ($LASTEXITCODE -ne 0) {
    Write-Log "Export fallo (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Log "=== 2/2 Build estatico Sentinel-2 ==="
python scripts/static_site/build_sentinel2_local.py
$code = $LASTEXITCODE
Write-Log "Build terminado (exit $code)."
exit $code
