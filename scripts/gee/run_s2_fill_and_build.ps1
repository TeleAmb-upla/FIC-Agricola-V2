# Rellena GeoTIFF semanales S2 faltantes por predio y reconstruye data_static/sentinel2.
# Requisito: earthengine authenticate (una vez por maquina).
#
# Re-exportacion completa (vaciar la coleccion y re-generar desde 2017): ver scripts/gee/README.md.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Repo

Write-Host "=== 1/2 Export S2 por predio (solo faltantes vs E_SAZO) ===" -ForegroundColor Cyan
python scripts/gee/export_s2_predio_local.py --reference E_SAZO
if ($LASTEXITCODE -ne 0) {
    Write-Host "Export fallo. Si no hay credenciales EE, ejecuta: earthengine authenticate" -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host "=== 2/2 Build estatico Sentinel-2 (WebP + metadata) ===" -ForegroundColor Cyan
python scripts/static_site/build_sentinel2_local.py
exit $LASTEXITCODE
