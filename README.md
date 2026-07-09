# FIC Agro — Dron y Sentinel-2 (explorador web)

Proyecto con convención similar a `wetland_ortho_monitoring`: `config.yaml`, utilidades en `scripts/`, exportación a `data_static/` y frontend (`index.html` + `explorador.html`).

- **Dron multiespectral**: fuente principal (`data/drone/...`).
- **Sentinel-2**: desactivada por defecto (`enabled: false` en `config.yaml`); activa la fuente cuando quieras series de referencia.

## Estructura

```text
fic_agro/
├── config.yaml
├── index.html
├── explorador.html
├── requirements.txt
├── assets/
├── scripts/
│   ├── static_site/       # export_data_ortho, pipeline_utils → data_static/
│   ├── data_prep/         # KMZ → GeoJSON, cuarteles, sync
│   └── gee/               # export_s2, Drive sync, previews, estadísticas
├── data/
│   ├── vectors/           # kml/, vuelos/, cuarteles/
│   ├── sentinel2/
│   └── drone/
├── data_static/
│   ├── vectors/cuarteles/ # copia desplegada de cuarteles.geojson
│   ├── sentinel2/
│   └── drone/
└── documentación/
```

## Carpeta `data/vectors/`

```text
data/vectors/
├── kml/              # KMZ/KML originales (polígonos de vuelo DJI)
├── vuelos/           # vuelos.geojson (compilado desde kml/)
└── cuarteles/        # cuarteles.geojson (id_cuartel, fuente de verdad)
```

Fuente de verdad espacial por predio: ``data_static/predios_aoi.geojson``.  
``sync_predios_master.py`` propaga esas geometrías a ``cuarteles.geojson`` y sincroniza ``superficie`` al CSV.  
Copia desplegada de cuarteles: ``data_static/vectors/cuarteles/cuarteles.geojson``.

Scripts útiles:

```bash
python scripts/data_prep/shp_to_cuarteles_geojson.py --sync   # cuarteles.shp → geojson + CSV
python scripts/data_prep/build_vuelos_geojson.py   # KMZ → data/vectors/vuelos/
python scripts/data_prep/sync_predios_master.py    # cuarteles → CSV, índice, data_static
```

## Convención de entrada (dron)

- `data/drone/{lote_id}/ndvi/{año}_{estacion}.tif`
- `data/drone/{lote_id}/ndwi/{año}_{estacion}.tif`
- `data/drone/{lote_id}/rgb/{año}_{estacion}.tif`

El lote de ejemplo en config es `lote_demo`. Sustituye o agrega entradas bajo `predios` en `config.yaml` cuando tengas tus polígonos.

## Exportar datos estáticos

```bash
cd fic_agro
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dron multiespectral (campañas de vuelo en data/drone/...)
python scripts/static_site/export_data_ortho.py

# Sentinel-2 local: agrega data/sentinel2/*.tif → data_static/sentinel2/{rasters,csv,metadata.json,timeseries.json}
python scripts/static_site/build_sentinel2_local.py --force
```

El pipeline Sentinel-2 produce, por predio, agregados temporales pixel-a-pixel:

- **Anual**: mediana de cada año histórico vs mediana del año en curso (pills de año).
- **Mensual**: mediana histórica por mes vs mes en curso (pills 1..12).
- **Semanal**: mediana histórica por semana ISO vs semana en curso (pills 1..52).
- **CSV / serie temporal**: mediana, P25, P75 hist. + valores semanales del año actual,
  por banda. Se grafica en el frontend (toggle pill, banda seleccionable).

## Ver el mapa

Sirve la carpeta del proyecto (no abras `file://` si quieres evitar límites CORS con `fetch`):

```bash
python -m http.server 8090
```

Abre `http://localhost:8090/index.html`.

## Nota sobre `data/`

El `.gitignore` ignora `data/` como en el proyecto de humedales; los GeoTIFF y vectores locales suelen vivir solo en tu máquina.
