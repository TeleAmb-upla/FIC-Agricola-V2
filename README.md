# FIC Agro — Dron y Sentinel-2 (explorador web)

Proyecto con la **misma convención de carpetas** que `wetland_ortho_monitoring`: configuración YAML, utilidades de pipeline, exportación a `data_static/` y frontend en `index.html`.

- **Dron multiespectral**: fuente principal (`data/drone/...`).
- **Sentinel-2**: desactivada por defecto (`enabled: false` en `config.yaml`); activa la fuente cuando quieras series de referencia.

## Estructura

```text
fic_agro/
├── config.yaml
├── pipeline_utils.py
├── download_sentinel2_gee.py
├── export_data_ortho.py
├── migrate_data_folders.py
├── index.html
├── test_imports.py
├── requirements.txt
├── data/
│   ├── shapefiles/
│   ├── sentinel2/
│   └── drone/
├── data_static/
│   ├── sources_manifest.json
│   ├── wetlands_aoi.geojson
│   ├── sentinel2/
│   └── drone/
└── documentación/
```

## Convención de entrada (dron)

- `data/drone/{lote_id}/ndvi/{año}_{estacion}.tif`
- `data/drone/{lote_id}/ndwi/{año}_{estacion}.tif`
- `data/drone/{lote_id}/rgb/{año}_{estacion}.tif`

El lote de ejemplo en config es `lote_demo`. Sustituye o agrega entradas bajo `wetlands` en `config.yaml` cuando tengas tus polígonos.

## Exportar datos estáticos

```bash
cd fic_agro
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python export_data_ortho.py
```

## Ver el mapa

Sirve la carpeta del proyecto (no abras `file://` si quieres evitar límites CORS con `fetch`):

```bash
python -m http.server 8090
```

Abre `http://localhost:8090/index.html`.

## Nota sobre `data/`

El `.gitignore` ignora `data/` como en el proyecto de humedales; los GeoTIFF y shapefiles suelen vivir solo en tu máquina.
