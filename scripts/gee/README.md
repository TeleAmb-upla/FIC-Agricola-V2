# scripts/gee — Pipeline Sentinel-2 y Sentinel-1 (Earth Engine)

Genera y mantiene las ImageCollections semanales en Earth Engine y su bajada local.
Todo se exporta en **EPSG:4326**, escala 10 m.

## Sentinel-1 (radar)

Mosaicos semanales ISO (IW, VV+VH, γ0 lineal + dB) recortados a la **unión de
cuarteles FIC**, no a la huella regional S2 de Valparaíso.

```powershell
python scripts/gee/update_s1_weekly_collection.py --dry-run
python scripts/gee/update_s1_weekly_collection.py --assets-only --no-wait
python scripts/gee/update_s1_weekly_collection.py --download-only
python scripts/gee/drive_sync.py --only s1
python scripts/static_site/build_sentinel1_local.py
```

IC por defecto: `projects/teleambagr/assets/S1_weekly_valpo`  
Drive: `FIC_RASTER_S1_semanales` (`FIC_DRIVE_S1_EXPORT_FOLDER`).

## Sentinel-2 (óptico)

## Índices exportados (9)

`NDVI, NDMI, MNDWI, REDEDGE_POSITION, MCARI, GNDVI, MSAVI, EVI, PSRI` + `clear_pixel_count`.

- Compuesto temporal por **semana ISO**: **mediana** de las escenas válidas de esa semana.
- `clear_pixel_count`: suma de píxeles despejados de la semana.
- Cuantización optimizada para ahorrar espacio en Assets (ver `estimate_asset_storage.py`):
  - **Int8** (×100): `NDVI, NDMI, MNDWI, MCARI, GNDVI, MSAVI, EVI` — resolución 0.01.
  - **Int8** (×10): `PSRI` — rango físico más amplio (~±11).
  - **Int16** (×10): `REDEDGE_POSITION` (~700–750 nm) — única banda que no cabe en Int8.
  - **Int8**: `clear_pixel_count` (antes Int64; ahorro ~27% solo en esa banda).
  - Valor físico: `pixel_dn / escala` (`index_scale` en `export_s2.py`).
  - Esquema legacy (9×Int16 + Int64): el build estático lo detecta automáticamente.
- La **media** no se guarda en la colección: se usa para los **promedios espaciales**
  (zonal `Reducer.mean` en `stats_s2_shapes.py` y media espacial en el build estático).

## Archivos

| Archivo | Rol |
|---------|-----|
| `export_s2.py` | Núcleo: mosaicos semanales ISO → ImageCollection (`--empty-collection`, `--force`, `--start-year`, …) y export opcional a Drive por predio. |
| `export_s2_predio_local.py` | Baja recortes semanales por predio desde la IC a `data/sentinel2/*.tif`. |
| `stats_s2_shapes.py` | Estadística zonal (media espacial) por predio + JSON/CSV; raster anual opcional a Drive. |
| `drive_sync.py` / `copy_drive_sentinel2_local.py` | Sincroniza carpeta de Drive → `data/sentinel2`. |
| `paths.py` | Rutas y constantes EE compartidas. |
| `run_s2_fill_and_build.ps1` | Orquestación Windows: rellenar faltantes + build estático. |

El post-proceso a **WebP/JSON estáticos** vive en
`scripts/static_site/build_sentinel2_local.py` (fuera de esta carpeta).

## Flujos

### Estimar ahorro de almacenamiento (Int8 vs Int16)

```powershell
python scripts/gee/estimate_asset_storage.py
python scripts/gee/estimate_asset_storage.py --mb 305.8 --weeks 440
```

Con el asset de referencia `Y2018_W01` (305.8 MB, 16971×20308 px): el esquema nuevo
pasa de **26 B/px** a **11 B/px** (~**58% menos** por semana, ~**129 MB**/asset).

### Re-exportación completa optimizada (Int8)

> Destructivo: vacía la ImageCollection y regenera desde 2018 con cuantización Int8.

```powershell
# Solo encolar tareas en GEE (sin Drive):
scripts/gee/reexport_s2_optimized.ps1

# Encolar + esperar IC + bajar + build estático:
scripts/gee/reexport_s2_optimized.ps1 -Wait
```

Cuando las tareas GEE terminen (puede tardar horas/días según cuota):

```powershell
scripts/gee/wait_ic_and_build.ps1
```

### Eliminar un año concreto (p. ej. 2017)

```powershell
python scripts/gee/export_s2.py --delete-year 2017
```

### Re-exportación completa (vaciar colección y regenerar desde 2018)

> Destructivo: `--empty-collection` borra **todas** las imágenes de la IC destino.

```powershell
# 1) Vaciar la colección y re-encolar todos los mosaicos semanales desde 2018
python scripts/gee/export_s2.py --empty-collection --start-year 2018 --force

# 2) (opcional) esperar tareas / bajar por predio a data/sentinel2
python scripts/gee/export_s2_predio_local.py --reference E_SAZO --all-predios

# 3) Build estático (WebP + metadata/timeseries en data_static/sentinel2)
python scripts/static_site/build_sentinel2_local.py
```

### Mantenimiento incremental (semanas nuevas)

```powershell
scripts/gee/run_s2_fill_and_build.ps1
```

## Requisitos

- `earthengine authenticate` (una vez por máquina).
- Proyecto Cloud EE por defecto: `teleambagr` (override: `EE_CLOUD_PROJECT`).
- Colección destino por defecto: `projects/teleambagr/assets/S2_weekly_valpo`
  (override: `--export-prefix` / `GEE_EXPORT_PREFIX`).
