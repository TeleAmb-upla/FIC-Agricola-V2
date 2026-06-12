# Rellena GeoTIFF semanales S2 faltantes por predio (RCI, RPA, …) y reconstruye data_static/sentinel2.
# Requisito: earthengine authenticate (una vez por máquina).
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Repo

Write-Host "=== 1/2 Export S2 por predio (solo faltantes vs G1) ===" -ForegroundColor Cyan
python scripts/gee/export_s2_predio_local.py --reference G1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Export falló. Si no hay credenciales EE, ejecuta: earthengine authenticate" -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host "=== 2/2 Build estático Sentinel-2 (WebP + metadata) ===" -ForegroundColor Cyan
python scripts/static_site/build_sentinel2_local.py
exit $LASTEXITCODE
