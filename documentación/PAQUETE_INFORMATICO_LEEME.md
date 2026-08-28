# FIC Agro — notas del pipeline (para automatización)

Te dejo esto junto con el zip para que tengas claro qué archivos entran, cómo se llaman las cosas y dónde quedan las salidas. Si algo no calza con lo que ves en el código, avísame y lo revisamos.

Repo: `fic_agro/`  
Agosto 2026.

---

## Cómo va el flujo (Sentinel-2)

En la práctica son tres pasos:

1. En Earth Engine corro `export_s2.py`, que genera los mosaicos semanales de toda la zona y los deja en la ImageCollection `S2_weekly_valpo`. Cada imagen se llama `Y2026_W05`, `Y2026_W06`, etc. (año y semana ISO).

2. Después bajo recortes por predio con `export_s2_predio_local.py`. Ahí uso los polígonos de `cuarteles.geojson` y lo que está en `config.yaml` para saber qué cuarteles van en cada predio. Los archivos quedan en `data/sentinel2/` con nombre `S2_E_SAZO_Y2026_W12.tif` y así.

3. Con esos TIF corro `build_sentinel2_local.py`, que arma los WebP, los JSON y los CSV que finalmente lee la página. Eso va a `data_static/sentinel2/`.

El mapa (`explorador.html`) solo lee `data_static/`. No toca `data/` ni GEE en el navegador.

Para dron es parecido pero aparte: entran TIF/LAS en `data/drone/`, sale todo en `data_static/drone/` vía `export_data_ortho.py`.

---

## 1. Archivos de entrada que no están en GEE

Estos son los que yo uso en local. Los que viven solo en la nube los separo al final.

### Config y predios

- `config.yaml` (raíz del repo)  
  Ahí están los 16 predios con su `s2_code`, `drone_code`, y cómo filtrar el AOI (`poligono_vuelo` o `id_cuartel`). Casi todo script parte de acá.

### Vectores y tabla de cuarteles

- `data/vectors/cuarteles/cuarteles.geojson`  
  Este es el geojson maestro: un polígono por cuartel, con `id_cuartel`, propietario, cultivo, etc.

- `data/fic_database.csv`  
  Misma info en CSV; sirve para login y para sincronizar atributos.

- `data_static/vectors/cuarteles/cuarteles.geojson`  
  Copia que se publica en la web. La genera `sync_predios_master.py` a partir del de `data/`.

- `data_static/predios_aoi.geojson`  
  Un polígono por predio (unión de sus cuarteles). Lo usan los scripts de exportación.

- `data_static/fic_database.csv` y `data_static/cuarteles_index.json`  
  Versión publicada del CSV y un índice cuartel → predio para el front.

Si cambias cuarteles o atributos, el comando que uso es:
`python scripts/data_prep/sync_predios_master.py`

### Polígonos de vuelo (origen)

- `data/vectors/kml/FIC-*.kmz` — KMZ de DJI, uno por predio en general.
- `data/vectors/kml/FIC_R_SALAZAR/FIC_R_SALAZAR.shp` — caso Salazar con varios cuarteles.
- `data/vectors/vuelos/vuelos.geojson` — todos los KMZ compilados.
- `data/vectors/comunas/comunas.shp` — solo si hay que rellenar comuna/provincia.

### Dron (crudo)

- `data/drone/{CODIGO}_YYYYMMDD_{indice}.tif`  
  Ejemplo: `E_SAZO_20260612_ndvi.tif`
- `data/drone/*.las` — LiDAR cuando hay vuelo con nube de puntos.

No los metí en el zip porque pesan mucho.

### Sentinel-2 local (intermedio)

- `data/sentinel2/S2_{S2_CODE}_Y{AAAA}_W{SS}.tif`  
  Ejemplo: `S2_L_MARTINEZ_Y2025_W50.tif`

Van 10 bandas: NDVI, NDMI, MNDWI, REDEDGE_POSITION, MCARI, GNDVI, MSAVI, EVI, PSRI y clear_pixel_count.

Esa carpeta no va a git y a veces está vacía entre corridas. Cuando empaqueté esto no había TIF ahí (estábamos regenerando la colección en GEE); por eso en el zip dejé una nota en `02_outputs_sentinel2/NOTA_tif_intermedios.txt`.

### Lo que sí está en GEE (no es archivo local)

- `projects/teleambagr/assets/vectores/Area_Agricola_Reg_Valpo_2025` — AOI grande para armar los mosaicos semanales.
- `projects/teleambagr/assets/S2_weekly_valpo` — la ImageCollection con los `Y2026_W05`, etc.

Los recortes por predio no los subo a GEE como asset aparte; los bajo directo a `data/sentinel2/` con el script de Python.

---

## 2. Patrones de nombres

Esto es lo que me costó más seguir cuando miré el código, así que lo dejo explícito.

### Mosaicos en GEE (imagen grande semanal)

- Nombre: `Y{aaaa}_W{ss}` con semana ISO de dos dígitos.  
  Ejemplo: `Y2026_W05`  
- Ruta: `projects/teleambagr/assets/S2_weekly_valpo/Y2026_W05`

Solo llega hasta la última semana ISO **completa** (la semana en curso no entra).

### TIF por predio (intermedio local)

- `S2_{S2_CODE}_Y{aaaa}_W{ss}.tif`  
  El `S2_CODE` es el de `config.yaml` en mayúsculas (`E_SAZO`, `L_MARTINEZ`, …).

### Lo que publica el sitio — Sentinel-2 (`data_static/sentinel2/`)

WebP del mapa:
- Anual: `S2_E_SAZO_annual_2025_NDVI.webp`
- Mensual: `S2_E_SAZO_monthly_current_06_NDVI.webp`
- Semanal: `S2_E_SAZO_weekly_current_12_NDVI.webp`

En los WebP el predio va en mayúsculas (`E_SAZO`). En los CSV va en minúsculas como clave del config (`e_sazo`).

CSV:
- `e_sazo_timeseries.csv`
- `e_sazo_timeseries_monthly.csv`

JSON:
- `metadata.json` — índice de capas, rangos de color, etc.
- `timeseries.json` — datos para los gráficos

### Dron (`data_static/drone/`)

WebP: `E_SAZO_2026_06_12_ndvi.webp` (código dron, fecha con guiones bajos, índice).

CSV: `e_sazo_timeseries.csv` (misma lógica de minúsculas que arriba).

LiDAR: `r_salazar_20260626.json` en `pointclouds/`.

### Raíz de `data_static/`

- `sources_manifest.json` — el explorador arranca leyendo este archivo.
- `fic_database.csv` — login.
- `cuarteles_index.json` — cruza cuartel con predio.

---

## 3. Qué trae el zip

```
PAQUETE_INFORMATICO/
  LEEME.md
  config.yaml
  requirements.txt
  01_inputs/          → entradas locales (geojson, csv, kmz de ejemplo)
  02_outputs_sentinel2/   → salidas S2 del sitio (+ nota si faltan TIF intermedios)
  03_outputs_drone/       → salidas dron
  04_outputs_shared/      → manifest, csv publicado, cuarteles desplegados
```

En cada carpeta de salida hay al menos 3 archivos de ejemplo (csv, webp, json según corresponda). Los `timeseries.json` van recortados porque el archivo completo es enorme.

No incluí:
- TIF/LAS crudos de dron
- TIF de `data/sentinel2/` (si no hay en disco, no hay qué copiar)
- nada de GEE (eso es solo en la nube)

---

## 4. Comandos que uso yo

Sentinel-2 (semanas nuevas):

```powershell
scripts/gee/run_s2_fill_and_build.ps1
```

O a mano:

```powershell
python scripts/gee/export_s2_predio_local.py --reference E_SAZO --sync-ee-weeks --fill-missing-weeks
python scripts/static_site/build_sentinel2_local.py
```

Dron después de un vuelo:

```powershell
python scripts/static_site/export_data_ortho.py
```

Sincronizar cuarteles / CSV / geojson publicado:

```powershell
python scripts/data_prep/sync_predios_master.py
```

En el repo hay más detalle en `documentación/ARQUITECTURA.md` si necesitas ver cómo se conecta script con script.

Proyecto EE: `teleambagr`  
Colección: `projects/teleambagr/assets/S2_weekly_valpo`

Cualquier duda me escribes.
