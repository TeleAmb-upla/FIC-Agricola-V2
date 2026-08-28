# Re-exportación completa S2_weekly_valpo con cuantización Int8 optimizada.
# 1) Vacía la ImageCollection y encola todos los mosaicos semanales desde 2018.
# 2) (opcional -Wait) Espera a que terminen las tareas y baja + build estático.
#
# Requisito: earthengine authenticate con cuenta que tenga acceso a teleambagr.
param(
    [switch]$Wait,
    [switch]$SkipLocalWipe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Repo

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$LogDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "reexport_s2_optimized.log"

function Write-Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

Write-Log "=== Verificando Earth Engine (teleambagr) ==="
python -u -c @"
import sys
sys.path.insert(0, 'scripts/gee')
import ee
ee.Initialize(project='teleambagr')
from export_s2 import list_child_asset_ids, DEFAULT_EXPORT_PREFIX
kids = list_child_asset_ids(DEFAULT_EXPORT_PREFIX)
print(f'OK assets={len(kids)}')
"@
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: sin acceso a teleambagr. Ejecuta: earthengine authenticate --force"
    exit 1
}

Write-Log "=== 1/1 Vaciar coleccion + re-encolar mosaicos Int8 (2018 -> hoy) ==="
Write-Log "Solo ImageCollection (sin Drive). Descarga local despues con wait_ic_and_build.ps1"
python scripts/gee/export_s2.py `
    --empty-collection `
    --start-year 2018 `
    --force `
    --no-drive-weekly-ic `
    --no-wait-drive-tasks 2>&1 | Tee-Object -FilePath (Join-Path $LogDir "reexport_s2_optimized_export.log") -Append
if ($LASTEXITCODE -ne 0) {
    Write-Log "Export fallo (exit $LASTEXITCODE). Revisa logs/reexport_s2_optimized_export.log"
    exit $LASTEXITCODE
}

Write-Log "Tareas encoladas en Earth Engine. Revisa la pestaña Tasks en code.earthengine.google.com"

if (-not $Wait) {
    Write-Log "Para esperar + bajar + build: scripts/gee/wait_ic_and_build.ps1"
    exit 0
}

Write-Log "=== Esperando IC completa + descarga + build ==="
if (-not $SkipLocalWipe) {
    $s2 = Join-Path $Repo "data/sentinel2"
    if (Test-Path $s2) {
        $n = (Get-ChildItem "$s2/*.tif" -ErrorAction SilentlyContinue | Measure-Object).Count
        if ($n -gt 0) {
            Write-Log "Eliminando $n TIF locales antiguos en data/sentinel2/"
            Remove-Item "$s2/S2_*.tif" -Force
        }
    }
}

& (Join-Path $PSScriptRoot "wait_ic_and_build.ps1")
exit $LASTEXITCODE
