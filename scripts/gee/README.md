# scripts/gee — Pipeline Sentinel-2 (Earth Engine)

Genera y mantiene la ImageCollection semanal S2 en Earth Engine y su bajada local
por predio. Todo se exporta en **EPSG:4326**, escala 10 m.

## Índices exportados (9)

`NDVI, NDMI, MNDWI, REDEDGE_POSITION, MCARI, GNDVI, MSAVI, EVI, PSRI` + `clear_pixel_count`.

- Compuesto temporal por **semana ISO**: **mediana** de las escenas válidas de esa semana.
- `clear_pixel_count`: suma de píxeles despejados de la semana.
- Cuantización **Int16** con escala por banda (`INDEX_INT16_SCALE_BY_BAND`):
  todas ×1000 excepto `REDEDGE_POSITION` ×10 (es una longitud de onda ~700–740 nm y
  ×1000 desbordaría Int16). El divisor real por banda se propaga a los metadatos del
  frontend para reconstruir el valor físico (`valor ≈ DN / escala`).
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

### Re-exportación completa (vaciar colección y regenerar desde 2017)

> Destructivo: `--empty-collection` borra **todas** las imágenes de la IC destino.

```powershell
# 1) Vaciar la colección y re-encolar todos los mosaicos semanales desde 2017
python scripts/gee/export_s2.py --empty-collection --start-year 2017 --force

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
