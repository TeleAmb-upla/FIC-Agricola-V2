#!/usr/bin/env python3
"""
Exporta mosaicos semanales Sentinel-2 a Earth Engine Assets: Cloud Score+ y, por pixel,
solo bandas de índices compuestos (NDVI, NDMI, índices tipo Sentinel Hub / SNAP, etc.)
más ``clear_pixel_count`` — sin bandas espectrales crudas del sensor.

Autenticación: ``earthengine authenticate``. Proyecto Cloud por defecto: ``teleambagr``
(``EE_CLOUD_PROJECT`` / ``--project``).

Salida bajo ``--export-prefix``; se crea la ImageCollection vacía si falta (salvo
``--skip-ensure-destination``). Por defecto **no** re-encola semanas cuyo asset hijo ya exista
(``--force`` para ignorar y volver a exportar todo).

**Incremental (por defecto, sin ``--force``):** lista hijos bajo ``--export-prefix``,
comprueba semana a semana (vía auditoría ligera) qué mosaicos faltan y **solo construye
el grafo GEE para el rango de años que aún tienen semanas pendientes**; los años cuyas
semanas con datos ya están exportadas se omiten por completo (ahorro de memoria y tiempo).

Si Earth Engine devuelve *User memory limit exceeded*, usa ``--export-half first`` y luego
``--export-half second`` (misma ``--year`` o rango): exporta la mitad de las semanas ISO
del año civil por ejecución, con menos grafo por tanda.

Por defecto imprime una **auditoría por semana** (escenas S2 vs pipeline con Cloud Score+,
y si se encola, omite por duplicado o no hay datos). Usa ``--no-audit`` para silenciarla.

Años: ``--year`` o ``--start-year`` / ``--end-year``; si no, ``GEE_START_YEAR`` /
``DEFAULT_START_YEAR``, y para el fin ``GEE_END_YEAR`` o ``DEFAULT_END_YEAR`` en el código
(se limita igual al año calendario actual en tiempo de ejecución).

Las semanas son **ISO 8601** (lunes=sí, domingo=sí): ventana [lunes 00:00 UTC, lunes+7d) exclusivo.
Solo se usan escenas **hasta la última semana ISO completa**: el fin del intervalo es el **lunes 00:00 UTC
de la semana en curso** (exclusivo), así no entra la semana parcial actual.

Los assets se nombran ``Y{iso_year}_W{iso_week:02d}`` (año/semana ISO, p. ej. borde de enero).

**Proceso 1 — ImageCollection en Earth Engine:** por defecto se ejecuta **antes** de Drive: encola
mosaicos semanales faltantes (incremental o ``--force``). Omitir ese paso: ``--no-export-assets`` o
``--predios-drive-only``. El flag ``--export-assets`` se mantiene por compatibilidad (sin efecto).

**Proceso 2 — Drive (por defecto):** para cada semana ``Y*_W*`` ya guardada en la ImageCollection y cada
predio del GeoJSON, encola un GeoTIFF recortado al predio. La API de GEE **no crea subcarpetas** con
``/`` en ``folder``: un solo nombre literal. **Semanales y compuestos** van a la **misma** carpeta
Drive: nombre colapsado de ``--drive-folder-root`` (p. ej. ``FIC_RASTER_S2_semanales_por_predio``);
predio y semana van
solo en el nombre de archivo ``S2_<PREDIO>_Yxxxx_Www.tif``.
Las semanas que esta misma corrida acaba de encolar a la IC **no** entran a Drive hasta que existan en
GEE (en la práctica suele hacer falta una segunda ejecución cuando ya terminaron las tareas IC).
No usa el rango ``--year`` / ``--start-year`` / ``--end-year``. Desactivar: ``--no-drive-weekly-ic``.

**Proceso 3 — Drive (opcional):** compuestos multi-banda (annual, monthly, …) solo con
``--export-predios-composites``. Desactivar esos: ``--no-drive-predios``. Misma carpeta Drive que el
proceso 2.

**Incremental hacia Drive:** no se re-encolan stems que ya existan como ``.tif`` en
``data/sentinel2`` **y**, si configurás ``FIC_DRIVE_LOCAL`` / ``--drive-local-root`` apuntando a la
carpeta donde sincronizás Drive, también los que ya estén bajo ``<esa_ruta>/<carpeta_raíz_colapsada>/``
(espejo de la carpeta de destino en la nube). La API de Earth Engine no lista Drive en remoto.

**Copia local:** tras las exportaciones a Drive de esta corrida, por defecto se **espera** a que las
tareas GEE terminen (``COMPLETED``). Luego, salvo ``--no-verify-drive-remote``, se consulta la **API
de Google Drive** hasta que aparezcan en la nube todos los ``.tif`` encolados en esta corrida
(tamaño ≥ umbral; carpetas homónimas: se elige la que mejor cubra los nombres esperados). Requiere
credenciales con alcance de lectura de Drive (p. ej. Application Default Credentials con ese scope,
o ``FIC_DRIVE_TOKEN_JSON``). Tras eso, un margen corto para que el cliente de Drive de escritorio
sincronice al disco y se copian desde ``FIC_DRIVE_LOCAL`` / ``--drive-local-root`` a
``data/sentinel2``. Desactivar la espera de tareas GEE: ``--no-wait-drive-tasks``. Desactivar la
verificación remota: ``--no-verify-drive-remote``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ee

DEFAULT_AOI_ASSET = "projects/teleambagr/assets/vectores/Area_Agricola_Reg_Valpo_2025"
DEFAULT_EXPORT_PREFIX = "projects/teleambagr/assets/S2_weekly_valpo"

# Proyecto Google Cloud para ``ee.Initialize(project=...)`` (API / facturación EE).
DEFAULT_CLOUD_PROJECT = "teleambagr"

DEFAULT_START_YEAR = 2017
DEFAULT_END_YEAR = None  # None ⇒ año civil actual (ver resolve_year_range)

# Raíz del repositorio (…/fic_agro): scripts/gee/export_s2.py → parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]

# Carpeta por defecto en Google Drive para GeoTIFF por predio (un solo nombre; GEE no anida con /).
# Mismo criterio que ``paths.DRIVE_S2_EXPORT_FOLDER`` (sync local). Sobrescribir: FIC_DRIVE_S2_EXPORT_FOLDER.
DEFAULT_DRIVE_PREDIOS_ROOT = (
    os.environ.get("FIC_DRIVE_S2_EXPORT_FOLDER", "").strip()
    or "FIC_RASTER_S2_semanales_por_predio"
)

# Subdirectorio **solo local** (repo) legado para stems S2_*_Y*_W*; no afecta el nombre de carpeta en Drive.
DRIVE_WEEKLY_PREDIO_SUBDIR = "semanales_por_predio"

# Escala (m) de exportación de mosaicos semanales a Drive (misma que toAsset en ``export_year``).
WEEKLY_IC_DRIVE_SCALE_M = 10.0

# Destino local (relativo al repo): copia desde la sincronización local de Drive.
DEFAULT_DATA_SENTINEL2 = REPO_ROOT / "data" / "sentinel2"

# Polling de tareas Export.image.toDrive hasta COMPLETED.
DEFAULT_DRIVE_TASK_POLL_SECONDS = 30.0
DEFAULT_DRIVE_TASK_LOG_INTERVAL = 120.0
# Tras COMPLETED en GEE, el cliente Drive puede tardar en escribir el .tif en disco.
DEFAULT_DRIVE_SYNC_GRACE_SECONDS = 20.0

# Verificación remota (Drive API v3): polling y tiempo máximo de espera.
DEFAULT_VERIFY_DRIVE_POLL_SECONDS = 30.0
DEFAULT_VERIFY_DRIVE_TIMEOUT_SECONDS = 7200.0
DEFAULT_VERIFY_DRIVE_MIN_FILE_BYTES = 1024
DEFAULT_VERIFY_DRIVE_LOG_INTERVAL = 120.0

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def sanitize_drive_folder_name(name: str) -> str:
    """
    Un solo nombre de carpeta para ``Export.*.toDrive(..., folder=...)``.

    Earth Engine interpreta ``a/b`` como **texto literal**, no como jerarquía en Drive; además
    si no existe la carpeta la crea en la raíz de «Mi unidad». Se colapsan segmentos con ``_``.
    """
    t = str(name).strip().replace("\\", "/")
    parts = [p for p in t.split("/") if p and p not in (".", "..")]
    out = "_".join(p.replace("..", "_") for p in parts)
    return out if out else "EE_GeoTIFF_export"


def resolve_repo_path(path_str: str | Path) -> Path:
    """Si la ruta es relativa, resuelve contra la raíz del repo."""
    p = Path(path_str)
    if p.is_absolute():
        return p.resolve()
    return (REPO_ROOT / p).resolve()


def utc_monday_00_current_week_ms() -> int:
    """Lunes 00:00 UTC de la semana ISO que contiene el instante actual."""
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp() * 1000)


def iso_week_specs_thursday_in_calendar_year(
    calendar_year: int,
    end_exclusive_ms: int,
) -> list[dict]:
    """
    Semanas ISO (lun–dom) cuyo **jueves** cae en el año civil ``calendar_year``.
    Solo incluye semanas que terminan en o antes de ``end_exclusive_ms``
    (lunes 00:00 UTC de la semana en curso, exclusivo en ``filterDate``).
    """
    ms_week = 7 * 24 * 60 * 60 * 1000
    out: dict[tuple[int, int], dict] = {}
    cur = datetime(calendar_year, 1, 1, tzinfo=timezone.utc)
    end_day = datetime(calendar_year, 12, 31, tzinfo=timezone.utc)
    while cur <= end_day:
        _iy, _iw, wd = cur.isocalendar()
        if wd == 4:
            monday = cur - timedelta(days=3)
            mms = int(monday.timestamp() * 1000)
            if mms + ms_week > end_exclusive_ms:
                cur += timedelta(days=1)
                continue
            key = (_iy, _iw)
            if key not in out:
                out[key] = {
                    "iso_year": _iy,
                    "iso_week": _iw,
                    "monday_ms": mms,
                    "calendar_year": calendar_year,
                }
        cur += timedelta(days=1)
    return [out[k] for k in sorted(out.keys())]


def slice_iso_week_specs_for_export_half(specs: list[dict], export_half: str) -> list[dict]:
    """
    Mitades por **orden ISO** en ``specs`` (ya ordenado por ``iso_week_specs_...``): la primera
    mitad recibe la semana extra si el número de semanas es impar.
    """
    if export_half in ("all", "") or not specs:
        return specs
    n = len(specs)
    mid = (n + 1) // 2
    if export_half == "first":
        return specs[:mid]
    if export_half == "second":
        return specs[mid:]
    return specs


def processed_scene_date_bounds_from_week_specs(
    specs: list[dict], end_exclusive_ms: int
) -> tuple[int, int]:
    """
    Rango mínimo ``[start, end)`` (ms UTC) para ``filterDate`` sobre S2+CS+, cubriendo las
    ventanas [lunes, lunes+7d) de ``specs``. Sin acotar así, el grafo supera con facilidad
    el límite de memoria de usuario de Earth Engine.
    """
    floor_ms = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    ms_week = 7 * 24 * 60 * 60 * 1000
    if not specs:
        return floor_ms, end_exclusive_ms
    starts = [int(s["monday_ms"]) for s in specs]
    ends = [m + ms_week for m in starts]
    return max(floor_ms, min(starts)), min(max(ends), end_exclusive_ms)


def export_asset_basename(iso_year: int, iso_week: int) -> str:
    return f"Y{iso_year}_W{iso_week:02d}"


def resolve_cloud_project(cli_project: str | None) -> str:
    """Orden: argumento ``--project``, env ``EE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_PROJECT``, valor por defecto."""
    if cli_project is not None and cli_project.strip() != "":
        return cli_project.strip()
    for key in ("EE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return DEFAULT_CLOUD_PROJECT


def ee_initialize(project: str) -> None:
    ee.Initialize(project=project)


def _asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException:
        return False


def list_child_asset_ids(parent_id: str) -> set[str]:
    """
    IDs completos de assets hijos bajo ``parent_id`` (ImageCollection o carpeta).
    Si no hay permiso o el padre no existe, devuelve conjunto vacío.
    """
    parent_id = parent_id.strip().rstrip("/")
    seen: set[str] = set()
    page_token: str | None = None
    while True:
        req: dict = {"parent": parent_id}
        if page_token:
            req["pageToken"] = page_token
        try:
            resp = ee.data.listAssets(req)
        except ee.EEException:
            return seen
        for a in resp.get("assets") or []:
            aid = a.get("name") or a.get("id")
            if isinstance(aid, str) and aid:
                seen.add(aid.rstrip("/"))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return seen


def load_existing_export_keys(export_prefix: str) -> tuple[set[str], int]:
    """Conjunto de claves para comprobar duplicados, y número de hijos devueltos por listAssets."""
    full = list_child_asset_ids(export_prefix.strip().rstrip("/"))
    keys: set[str] = set(full)
    for f in full:
        keys.add(f.rstrip("/").split("/")[-1])
    return keys, len(full)


def export_key_exists(asset_id: str, keys: set[str]) -> bool:
    if asset_id in keys:
        return True
    return asset_id.rstrip("/").split("/")[-1] in keys


def empty_collection(export_prefix: str, *, dry_run: bool = False) -> int:
    """
    Borra **todas las imágenes** hijas de la ImageCollection ``export_prefix`` (la colección se
    conserva). Operación destructiva e irreversible; devuelve el número de imágenes eliminadas.
    """
    export_prefix = export_prefix.strip().rstrip("/")
    children = sorted(list_child_asset_ids(export_prefix))
    images = [c for c in children if _asset_exists(c) and str(
        ee.data.getAsset(c).get("type", "")).upper() == "IMAGE"]
    print(f"Colección {export_prefix}: {len(images)} imagen(es) a eliminar.")
    if dry_run:
        for aid in images:
            print(f"  [dry-run] deleteAsset {aid}")
        return 0
    deleted = 0
    for aid in images:
        try:
            ee.data.deleteAsset(aid)
            deleted += 1
            if deleted % 25 == 0:
                print(f"  ...{deleted}/{len(images)} eliminadas")
        except ee.EEException as exc:
            print(f"  [WARN] no se pudo eliminar {aid}: {exc}")
    print(f"Eliminadas {deleted}/{len(images)} imágenes de {export_prefix}.")
    return deleted


def ensure_export_destination(export_prefix: str, *, dry_run: bool) -> None:
    """
    Crea bajo ``projects/<cloud>/assets/`` las carpetas (FOLDER) que falten y, al final del
    prefijo, un asset vacío IMAGE_COLLECTION si no existe. Es idempotente.
    Los mosaicos se exportan como imágenes hijas: ``<export_prefix>/Yxxxx_Www``.
    """
    export_prefix = export_prefix.strip().rstrip("/")
    parts = export_prefix.split("/")
    if len(parts) < 4 or parts[0] != "projects":
        raise ValueError(
            "export_prefix debe ser projects/<CLOUD_PROJECT>/assets/... "
            f"(recibido: {export_prefix!r})"
        )
    try:
        assets_idx = parts.index("assets")
    except ValueError as exc:
        raise ValueError("export_prefix debe incluir el segmento .../assets/...") from exc

    tail = parts[assets_idx + 1 :]
    if not tail:
        raise ValueError("Debe haber al menos un nombre bajo projects/.../assets/")

    cumulative = "/".join(parts[: assets_idx + 1])

    for i, segment in enumerate(tail):
        cumulative = f"{cumulative}/{segment}"
        last = i == len(tail) - 1

        if _asset_exists(cumulative):
            info = ee.data.getAsset(cumulative)
            kind = str(info.get("type", "")).upper()
            if last:
                if kind == "IMAGE_COLLECTION":
                    print(f"Colección destino ya existe: {cumulative}")
                elif kind == "FOLDER":
                    print(
                        f"Destino existe como carpeta (OK para exportar imágenes): {cumulative}"
                    )
                else:
                    raise ValueError(
                        f"El asset {cumulative} ya existe como {kind}; "
                        "usa otro export_prefix o eliminalo en Earth Engine."
                    )
            elif kind not in ("FOLDER", "IMAGE_COLLECTION"):
                raise ValueError(
                    f"No se puede usar {cumulative} como carpeta padre (tipo {kind})."
                )
            continue

        if dry_run:
            typ = "IMAGE_COLLECTION" if last else "FOLDER"
            print(f"[dry-run] crearía {typ}: {cumulative}")
            continue

        if last:
            ee.data.createAsset({"type": "IMAGE_COLLECTION"}, cumulative)
            print(f"Creada ImageCollection vacía: {cumulative}")
        else:
            ee.data.createAsset({"type": "FOLDER"}, cumulative)
            print(f"Creada carpeta: {cumulative}")


def mask_and_scale(image: ee.Image) -> ee.Image:
    mask = image.select("cs").gte(0.6)
    clear = mask.rename("clear_pixel_count")
    return (
        image.updateMask(mask)
        .divide(10000)
        .addBands(clear)
        .copyProperties(image, ["system:time_start", "system:index"])
    )


def reduce_radiometric_resolution(img: ee.Image) -> ee.Image:
    """
    Cuantiza cada índice a Int16 con su escala (``index_int16_scale``) para reducir tamaño.

    Se aplica **solo al mosaico semanal** listo para exportar (índices ya en reflectancia
    0–1 o rango físico del índice), no por escena: así ``median()`` y el clip no
    reintroducen float sobre enteros ya cuantizados. ``clear_pixel_count`` no se altera.
    """
    clear = img.select("clear_pixel_count")
    scaled = [
        img.select(band).multiply(index_int16_scale(band)).round().toInt16()
        for band in COMPOSED_INDEX_BANDS
    ]
    idx = ee.Image.cat(scaled)
    return idx.addBands(clear).copyProperties(img, ["system:time_start", "system:index"])


def add_indices(img: ee.Image) -> ee.Image:
    """Añade los 9 índices operativos (ver ``COMPOSED_INDEX_BANDS``)."""
    ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndmi = img.normalizedDifference(["B8", "B11"]).rename("NDMI")
    mndwi = img.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    gndvi = img.normalizedDifference(["B8", "B3"]).rename("GNDVI")

    evi = img.expression(
        "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
        {"NIR": img.select("B8"), "RED": img.select("B4"), "BLUE": img.select("B2")},
    ).rename("EVI")

    msavi = img.expression(
        "(2 * NIR + 1 - sqrt(pow((2 * NIR + 1), 2) - 8 * (NIR - RED))) / 2",
        {"NIR": img.select("B8"), "RED": img.select("B4")},
    ).rename("MSAVI")

    eps = ee.Image.constant(1e-6)
    mcari = img.expression(
        "((RE1 - R) - 0.2 * (RE1 - G)) * (RE1 / (R + eps))",
        {"RE1": img.select("B5"), "R": img.select("B4"), "G": img.select("B3"), "eps": eps},
    ).rename("MCARI")

    # REDEDGE_POSITION: posición del "red edge" en nm (~700–740). Se exporta con escala
    # propia (ver INDEX_INT16_SCALE_BY_BAND) para no desbordar Int16.
    b4, b5, b6, b7 = img.select("B4"), img.select("B5"), img.select("B6"), img.select("B7")
    rep_num = b4.add(b7).multiply(0.5).subtract(b5)
    rep_den = b6.subtract(b5).max(1e-6)
    rededge_position = rep_num.multiply(40.0).divide(rep_den).add(700.0).rename("REDEDGE_POSITION")

    b6_safe = img.select("B6").max(1e-6)
    psri = img.select("B4").subtract(img.select("B2")).divide(b6_safe).rename("PSRI")

    extra = [
        ndvi,
        ndmi,
        mndwi,
        rededge_position,
        mcari,
        gndvi,
        msavi,
        evi,
        psri,
    ]
    return img.addBands(extra)


# Salida de exportación: solo estos índices, sin B1…B12 ni otras bandas del sensor.
COMPOSED_INDEX_BANDS = [
    "NDVI",
    "NDMI",
    "MNDWI",
    "REDEDGE_POSITION",
    "MCARI",
    "GNDVI",
    "MSAVI",
    "EVI",
    "PSRI",
]

# Escala entera al exportar índices (Int16): valor_físico ≈ pixel / escala.
# La mayoría usa ×1000; REDEDGE_POSITION (~700–740 nm) usa ×10 para no desbordar Int16.
INDEX_INT16_SCALE = 1000
INDEX_INT16_SCALE_BY_BAND = {
    "REDEDGE_POSITION": 10,
}


def index_int16_scale(band: str) -> int:
    """Factor de escala Int16 para una banda de índice (default ``INDEX_INT16_SCALE``)."""
    return int(INDEX_INT16_SCALE_BY_BAND.get(band, INDEX_INT16_SCALE))


def ee_date_min(a: ee.Date, b: ee.Date) -> ee.Date:
    """Menor de dos ``ee.Date``. En JavaScript existe ``ee.Date.min``; en la API Python no."""
    a, b = ee.Date(a), ee.Date(b)
    return ee.Algorithms.If(a.millis().lt(b.millis()), a, b)


def ensure_system_time_start_monday(img: ee.Image, monday_ms: int) -> ee.Image:
    """``system:time_start`` = lunes 00:00 UTC de esa semana ISO (ms)."""
    t0 = ee.Date(monday_ms).millis()
    return ee.Image(img).set("system:time_start", t0)


def get_week_audit_for_year(
    aoi: ee.Geometry,
    calendar_year: int,
    end_exclusive: ee.Date,
) -> list[dict]:
    """
    Conteos por semana ISO del año civil (jueves en ``calendar_year``), misma ventana [lun, lun+7d)
    que el mosaico. Requiere una evaluación al servidor por año.
    """
    end_ms = int(end_exclusive.millis().getInfo())
    specs = iso_week_specs_thursday_in_calendar_year(calendar_year, end_ms)
    if not specs:
        return []

    ys = ee.Date.fromYMD(calendar_year, 1, 1)
    ye = ys.advance(1, "year")
    cap = ee_date_min(ye, end_exclusive)
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi).filterDate(ys, cap)
    linked = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .linkCollection(ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"), ["cs"])
        .filterDate(ys, cap)
    )
    processed = linked.map(mask_and_scale).map(add_indices)

    ms_ee = ee.List([int(s["monday_ms"]) for s in specs])
    iy_ee = ee.List([int(s["iso_year"]) for s in specs])
    iw_ee = ee.List([int(s["iso_week"]) for s in specs])
    n = len(specs)
    idxs = ee.List.sequence(0, n - 1)

    def per_i(i: ee.ComputedObject) -> ee.Dictionary:
        i = ee.Number(i)
        start = ee.Date(ms_ee.get(i))
        end_w = start.advance(7, "day")
        col_raw = s2.filterDate(start, end_w).filterBounds(aoi)
        col_pipeline = processed.filterDate(start, end_w).filterBounds(aoi)
        return ee.Dictionary(
            {
                "iso_year": ee.Number(iy_ee.get(i)),
                "iso_week": ee.Number(iw_ee.get(i)),
                "n_s2": col_raw.size(),
                "n_pipeline": col_pipeline.size(),
            }
        )

    raw = ee.List(idxs.map(per_i)).getInfo()
    for row in raw:
        row["calendar_year"] = calendar_year
        row["week"] = int(row["iso_week"])
    return raw


def year_has_pending_weeks(
    export_prefix: str,
    existing_ids: set[str],
    audit_rows: list[dict],
) -> bool:
    """Hay al menos una semana con datos de pipeline (n_pipeline > 0) y sin asset hijo."""
    for row in audit_rows:
        if int(row.get("n_pipeline", 0)) <= 0:
            continue
        iy = int(row["iso_year"])
        iw = int(row["iso_week"])
        aid = f"{export_prefix}/{export_asset_basename(iy, iw)}"
        if not export_key_exists(aid, existing_ids):
            return True
    return False


def compute_incremental_year_plan(
    export_prefix: str,
    existing_ids: set[str],
    aoi: ee.Geometry,
    end_exclusive: ee.Date,
    start_y: int,
    end_y: int,
) -> tuple[list[int], dict[int, list[dict]]]:
    """
    Un ``get_week_audit_for_year`` por año civil en el rango. Devuelve años con al menos
    una semana exportable aún faltante, y la auditoría cacheada para no repetir la llamada.
    """
    pending: list[int] = []
    audit_by_year: dict[int, list[dict]] = {}
    for y in range(start_y, end_y + 1):
        rows = get_week_audit_for_year(aoi, y, end_exclusive)
        audit_by_year[y] = rows
        if year_has_pending_weeks(export_prefix, existing_ids, rows):
            pending.append(y)
    return pending, audit_by_year


def build_weekly_collection(
    aoi: ee.Geometry,
    end_exclusive: ee.Date,
    calendar_years: list[int],
    *,
    export_half: str = "all",
) -> ee.ImageCollection:
    if not calendar_years:
        return ee.ImageCollection([])

    end_ms = int(end_exclusive.millis().getInfo())
    imgs: list[ee.Image] = []
    for cy in calendar_years:
        specs_full = iso_week_specs_thursday_in_calendar_year(cy, end_ms)
        specs = slice_iso_week_specs_for_export_half(specs_full, export_half)
        if not specs:
            continue
        scene_start_ms, scene_end_ms = processed_scene_date_bounds_from_week_specs(specs, end_ms)
        s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi)
        cs_plus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
        s2_with_clouds = s2.linkCollection(cs_plus, ["cs"])
        processed = s2_with_clouds.filterDate(
            ee.Date(scene_start_ms), ee.Date(scene_end_ms)
        ).map(mask_and_scale).map(add_indices)
        for spec in specs:
            monday_ms = int(spec["monday_ms"])
            start = ee.Date(monday_ms)
            end_w = start.advance(7, "day")
            week_col = processed.filterDate(start, end_w).filterBounds(aoi)
            idx_median = week_col.select(COMPOSED_INDEX_BANDS).median()
            clear_sum = week_col.select("clear_pixel_count").sum().rename("clear_pixel_count")
            iso_w = int(spec["iso_week"])
            mosaic = reduce_radiometric_resolution(idx_median.addBands(clear_sum)).set(
                {
                    "year": cy,
                    "week": iso_w,
                    "system:time_start": start.millis(),
                }
            )
            weekly_for_export = ee.Image(
                ee.Algorithms.If(
                    week_col.size().gt(0),
                    mosaic,
                    mosaic.set("year", -1),
                )
            )
            imgs.append(weekly_for_export)

    if not imgs:
        return ee.ImageCollection([])

    def _clip_keep_meta(im: ee.ComputedObject) -> ee.Image:
        im = ee.Image(im)
        # No usar .float(): anularía el int16 aplicado en reduce_radiometric_resolution (mosaico semanal).
        return im.clip(aoi).copyProperties(
            im,
            ["system:time_start", "week", "year"],
        )

    return (
        ee.ImageCollection(imgs)
        .filter(ee.Filter.neq("year", -1))
        .map(_clip_keep_meta)
    )


def export_year(
    final_collection: ee.ImageCollection,
    aoi: ee.Geometry,
    export_prefix: str,
    calendar_year: int,
    *,
    dry_run: bool,
    existing_ids: set[str],
    skip_existing: bool,
    end_exclusive: ee.Date,
    audit: bool,
    audit_rows_precalc: list[dict] | None = None,
    export_half: str = "all",
) -> tuple[int, int]:
    """Devuelve (encoladas_o_simuladas, omitidas_por_existir)."""
    col = final_collection.filter(ee.Filter.eq("year", calendar_year)).sort("week")
    size = col.size().getInfo()
    img_list = col.toList(size)
    week_to_img: dict[tuple[int, int], ee.Image] = {}
    for i in range(size):
        im = ee.Image(img_list.get(i))
        cy_img = int(im.get("year").getInfo())
        w_img = int(im.get("week").getInfo())
        week_to_img[(cy_img, w_img)] = im

    end_ms = int(end_exclusive.millis().getInfo())
    iso_specs = slice_iso_week_specs_for_export_half(
        iso_week_specs_thursday_in_calendar_year(calendar_year, end_ms),
        export_half,
    )

    by_key: dict[tuple[int, int], dict] = {}
    if audit:
        rows = (
            audit_rows_precalc
            if audit_rows_precalc is not None
            else get_week_audit_for_year(aoi, calendar_year, end_exclusive)
        )
        by_key = {(int(r["calendar_year"]), int(r["iso_week"])): r for r in rows}
        print(
            "  (Auditoría: semanas ISO lun–dom; n_s2 / n_pipe; fin de datos = última semana ISO "
            "completa antes del lunes UTC actual. cs≥0.6 solo enmascara píxeles.)"
        )

    n_enq = 0
    n_skip = 0
    n_no_s2 = 0
    n_gap_cs = 0

    for spec in iso_specs:
        iy = int(spec["iso_year"])
        iw = int(spec["iso_week"])
        key = (int(spec["calendar_year"]), iw)
        row = by_key.get(key, {}) if audit else {}
        n_s2 = int(row.get("n_s2", -1)) if audit else -1
        n_pipe = int(row.get("n_pipeline", -1)) if audit else -1
        img = week_to_img.get(key)
        asset_id = f"{export_prefix}/{export_asset_basename(iy, iw)}"

        if audit and n_s2 == 0:
            n_no_s2 += 1
            print(f"  ISO {iy}-W{iw:02d}: sin escenas S2 en AOI → no exportación")
            continue

        if audit and n_s2 > 0 and n_pipe == 0:
            n_gap_cs += 1
            print(
                f"  ISO {iy}-W{iw:02d}: {n_s2} escenas S2 pero 0 en pipeline "
                "(enlace CS+ / ventana) → no exportación"
            )
            continue

        if img is None:
            if audit:
                print(
                    f"  ISO {iy}-W{iw:02d}: inconsistencia (n_pipe={n_pipe}, sin imagen en colección)"
                )
            continue

        desc = f"S2_{iy}W{iw:02d}"
        if skip_existing and export_key_exists(asset_id, existing_ids):
            if audit:
                print(
                    f"  ISO {iy}-W{iw:02d}: ya existe, omitir | n_s2={n_s2} n_pipe={n_pipe} | {asset_id}"
                )
            else:
                print(f"  [ya existe, omitir] {asset_id}")
            n_skip += 1
            continue
        if dry_run:
            if audit:
                print(
                    f"  ISO {iy}-W{iw:02d}: [dry-run] encolaría | n_s2={n_s2} n_pipe={n_pipe} | {asset_id}"
                )
            else:
                print(f"  [dry-run] {desc} -> {asset_id}")
            n_enq += 1
            continue
        if audit:
            print(f"  ISO {iy}-W{iw:02d}: encolado export | n_s2={n_s2} n_pipe={n_pipe} | {asset_id}")
        img = ensure_system_time_start_monday(img, int(spec["monday_ms"]))
        task = ee.batch.Export.image.toAsset(
            image=img,
            description=desc,
            assetId=asset_id,
            region=aoi,
            scale=10,
            crs="EPSG:4326",
            maxPixels=1e13,
        )
        task.start()
        n_enq += 1

    if audit:
        n_exportable = len(week_to_img)
        print(
            f"  Resumen {calendar_year}: {n_exportable} semanas con mosaico; "
            f"{n_no_s2} sin S2; {n_gap_cs} con S2 pero pipeline vacío; "
            f"encoladas/simuladas {n_enq}; omitidas (existentes) {n_skip}"
        )
    return n_enq, n_skip


# ---------------------------------------------------------------------------
# Per-predio Drive export: composites for side-by-side visualization
# ---------------------------------------------------------------------------

DEFAULT_PREDIOS_VIZ_BANDS = ["NDVI", "NDMI", "MNDWI", "EVI", "MSAVI"]
DEFAULT_PREDIOS_VIZ_SCALE = 10.0

MONTH_NAMES_ES = [
    "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
]


def collect_existing_predio_tif_stems(local_dir: Path) -> set[str]:
    """Nombres base (sin extensión) de GeoTIFF ya presentes bajo ``data/sentinel2`` (recursivo)."""
    if not local_dir.is_dir():
        return set()
    out: set[str] = set()
    for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
        for p in local_dir.rglob(pat):
            if p.is_file():
                out.add(p.stem)
    return out


# Basename de imagen hija en la ImageCollection semanal (mismo patrón que ``export_asset_basename``).
_WEEKLY_IC_BASENAME_RE = re.compile(r"^Y(\d{4})_W(\d{2})$")


_WEEKLY_PREDIO_TIF_STEM_RE = re.compile(r"^S2_.+_Y\d{4}_W\d{2}$")


def collect_existing_weekly_predio_stems(local_dir: Path, subdir: str) -> set[str]:
    """
    Stems de GeoTIFF semanales por predio (``S2_<PREDIO>_Yxxxx_Www``) ya presentes en disco.

    Incluye el layout antiguo ``local_dir/<subdir>/…`` y cualquier ``.tif`` bajo ``local_dir``
    que coincida con el patrón (p. ej. carpeta Drive plana tras sincronizar).
    """
    out: set[str] = set()
    legacy = local_dir / subdir
    if legacy.is_dir():
        for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
            for p in legacy.rglob(pat):
                if p.is_file():
                    out.add(p.stem)
    for s in collect_tif_stems_under_directory(local_dir):
        if _WEEKLY_PREDIO_TIF_STEM_RE.match(s):
            out.add(s)
    return out


def collect_tif_stems_under_directory(root: Path) -> set[str]:
    """Nombres base de todos los GeoTIFF bajo ``root`` (recursivo)."""
    if not root.is_dir():
        return set()
    out: set[str] = set()
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".tif", ".tiff"):
            out.add(p.stem)
    return out


def collect_tif_stems_from_drive_local_mirror(
    drive_local_parent: Path | None,
    drive_root_folder_name: str,
) -> set[str]:
    """
    Stems de ``.tif`` bajo ``<drive_local_parent>/<drive_root_folder_name>/``.

    GEE no lista el contenido de Google Drive; si tenés la carpeta sincronizada en disco, usamos
    ese árbol para **no re-encolar** archivos que ya están en la carpeta de destino en la nube.
    """
    if drive_local_parent is None:
        return set()
    base = drive_local_parent.expanduser().resolve() / drive_root_folder_name.strip().strip("/")
    return collect_tif_stems_under_directory(base)


def list_weekly_image_asset_ids(export_prefix: str) -> list[str]:
    """
    Lista todos los assetIds de imágenes ``Y<iso_año>_W<ww>`` bajo la ImageCollection ``export_prefix``.
    Independiente de ``--start-year`` / ``--end-year`` (usa solo lo que ya existe en GEE).
    """
    keys, _ = load_existing_export_keys(export_prefix)
    pref = export_prefix.strip().rstrip("/")
    basenames: set[str] = set()
    for k in keys:
        base = k.rstrip("/").split("/")[-1]
        if _WEEKLY_IC_BASENAME_RE.match(base):
            basenames.add(base)

    def _sort_key(b: str) -> tuple[int, int]:
        m = _WEEKLY_IC_BASENAME_RE.match(b)
        if not m:
            return 0, 0
        return int(m.group(1)), int(m.group(2))

    return [f"{pref}/{b}" for b in sorted(basenames, key=_sort_key)]


def _image_for_geotiff_drive_export(img: ee.Image) -> ee.Image:
    """
    ``Export.image.toDrive`` (GeoTIFF) no admite píxeles ``Long`` (entero 64 bits).
    Convierte todo el stack a float64 (recomendación de EE / error code 3).
    """
    return ee.Image(img).toDouble()


def export_weekly_rasters_per_predio_to_drive(
    export_prefix: str,
    predios: list[dict],
    drive_root_folder: str,
    *,
    stem_prefix: str = "S2",
    scale_m: float = WEEKLY_IC_DRIVE_SCALE_M,
    dry_run: bool = False,
    skip_existing_stems: set[str] | None = None,
) -> tuple[list[ee.batch.Task], set[str]]:
    """
    Por cada imagen ``Y*_W*`` en la ImageCollection y cada predio, encola ``toDrive`` del mosaico
    recortado a la geometría del predio (mismo stack de bandas que el asset).

    Misma carpeta Drive que los compuestos: ``sanitize_drive_folder_name(drive_root_folder)``
    (p. ej. ``FIC_RASTER_S2_semanales_por_predio``). Archivos ``S2_<PREDIO>_Yxxxx_Www.tif`` dentro.

    Retorna ``(tareas, stems_encolados)`` — stems sin ``.tif``, solo los que realmente se encolaron.
    """
    tasks: list[ee.batch.Task] = []
    enqueued_stems: set[str] = set()
    skip = skip_existing_stems or set()
    folder_leaf = sanitize_drive_folder_name(drive_root_folder)

    asset_ids = list_weekly_image_asset_ids(export_prefix)
    n_pairs = len(asset_ids) * len(predios)
    print(
        f"Drive — mosaicos semanales por predio: {len(asset_ids)} semana(s) × {len(predios)} predio(s) "
        f"= {n_pairs} tarea(s) → Drive/{folder_leaf}/ (predio en el nombre del archivo)"
    )
    if not asset_ids:
        print("  (No hay assets Y*_W* en la colección.)", file=sys.stderr)
        return tasks, enqueued_stems
    if not predios:
        print("  (No hay predios en el GeoJSON.)", file=sys.stderr)
        return tasks, enqueued_stems

    for asset_id in asset_ids:
        basename = asset_id.rstrip("/").split("/")[-1]
        img = ee.Image(asset_id)
        for predio in predios:
            pid = predio["predio_id"]
            pid_up = pid.upper()
            geom = predio["geometry"]
            stem = f"{stem_prefix}_{pid_up}_{basename}"
            if stem in skip:
                print(f"  [omitir] {stem}.tif (ya en destino: repo o carpeta Drive sincronizada)")
                continue
            if dry_run:
                print(f"  [dry-run] {stem}.tif → Drive/{folder_leaf}")
                continue
            desc = f"S2wp_{stem}"[:100].replace(" ", "_")
            t = ee.batch.Export.image.toDrive(
                image=_image_for_geotiff_drive_export(img.clip(geom)),
                description=desc,
                folder=folder_leaf,
                fileNamePrefix=stem,
                region=geom,
                scale=scale_m,
                crs="EPSG:4326",
                maxPixels=1e13,
                fileFormat="GeoTIFF",
            )
            t.start()
            tasks.append(t)
            enqueued_stems.add(stem)
            print(f"  Encolado: {stem}.tif → Drive/{folder_leaf}")

    return tasks, enqueued_stems


def copy_drive_sync_to_data_sentinel2(
    drive_local_parent: Path,
    drive_folder_name: str,
    dest: Path,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Copia ``drive_local_parent / drive_folder_name/**.tif`` → ``dest`` preservando subcarpetas.

    Retorna ``(n_copiados, n_omitidos_sin_cambios)``.
    """
    src = (drive_local_parent.expanduser().resolve() / drive_folder_name).resolve()
    if not src.is_dir():
        return 0, 0
    dest.mkdir(parents=True, exist_ok=True)
    n_copy = 0
    n_skip = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".tif", ".tiff"):
            continue
        rel = f.relative_to(src)
        dst = dest / rel
        if dst.is_file():
            try:
                if dst.stat().st_mtime >= f.stat().st_mtime and dst.stat().st_size == f.stat().st_size:
                    n_skip += 1
                    continue
            except OSError:
                pass
        if dry_run:
            try:
                disp = str(dst.relative_to(REPO_ROOT))
            except ValueError:
                disp = str(dst)
            print(f"  [dry-run] copiaría → {disp}")
            n_copy += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        try:
            rel_disp = str(dst.relative_to(REPO_ROOT))
        except ValueError:
            rel_disp = str(dst)
        print(f"  Copiado local: {rel_disp}")
        n_copy += 1
    return n_copy, n_skip


def wait_for_export_tasks(
    tasks: list[ee.batch.Task],
    *,
    poll_seconds: float,
    log_interval: float,
) -> None:
    """
    Bloquea hasta que ninguna tarea esté en estado activo (READY/RUNNING/CANCEL_REQUESTED).
    Comprueba que todas queden en ``COMPLETED``; si hay ``FAILED``/``CANCELLED``, lanza ``RuntimeError``.
    """
    if not tasks:
        return
    n = len(tasks)
    print(
        f"\nEsperando {n} tarea(s) de exportación a Drive "
        f"(consulta cada {poll_seconds:g}s; log cada {log_interval:g}s)…",
        flush=True,
    )
    start = time.monotonic()
    last_log = start
    poll_seconds = max(5.0, float(poll_seconds))
    log_interval = max(poll_seconds, float(log_interval))

    while True:
        active_idx = [i for i, t in enumerate(tasks) if t.active()]
        if not active_idx:
            break
        now = time.monotonic()
        if now - last_log >= log_interval:
            done = n - len(active_idx)
            elapsed = int(now - start)
            print(f"  Drive: {done}/{n} inactivas; {len(active_idx)} aún activas ({elapsed}s)…", flush=True)
            last_log = now
        time.sleep(poll_seconds)

    failed: list[tuple[int, str, str]] = []
    for i, t in enumerate(tasks):
        try:
            info = t.status()
            st = info["state"]
        except ee.EEException as exc:
            failed.append((i, "STATUS_ERROR", str(exc)))
            continue
        st_s = st.value if isinstance(st, ee.batch.Task.State) else str(st)
        if st_s == ee.batch.Task.State.COMPLETED.value:
            continue
        err = str(info.get("error_message") or "")
        failed.append((i, st_s, err))

    if failed:
        lines = [f"  tarea[{i}]: estado={st}" + (f" | {msg}" if msg else "") for i, st, msg in failed]
        raise RuntimeError(
            "Fallo en exportación a Drive:\n" + "\n".join(lines[:25])
            + ("\n  …" if len(lines) > 25 else "")
        )

    elapsed = int(time.monotonic() - start)
    print(f"  Todas las tareas Drive de esta corrida: COMPLETED ({elapsed}s).", flush=True)


def _drive_query_escape_literal(value: str) -> str:
    """Escapa comillas y barras invertidas para literales en el parámetro ``q`` de Drive v3."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _import_google_drive_client():
    try:
        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise RuntimeError(
            "Verificación remota en Google Drive requiere paquetes adicionales. Instalá:\n"
            "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc
    return (
        google.auth,
        GoogleAuthRequest,
        Credentials,
        build,
        HttpError,
    )


def build_google_drive_v3_service():
    """
    Credenciales con lectura de Drive: primero ``FIC_DRIVE_TOKEN_JSON`` (OAuth authorized_user),
    si no ``google.auth.default`` con ``drive.readonly``.
    """
    google_auth, GoogleAuthRequest, Credentials, build, _HttpError = _import_google_drive_client()
    scopes = [DRIVE_READONLY_SCOPE]
    creds = None
    token_path = os.environ.get("FIC_DRIVE_TOKEN_JSON", "").strip()
    if token_path:
        p = Path(token_path).expanduser()
        if p.is_file():
            creds = Credentials.from_authorized_user_file(str(p), scopes)
    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
        except Exception:
            creds = None
    if creds is None or not creds.valid:
        try:
            adc, _ = google_auth.default(scopes=scopes)
        except google_auth.exceptions.DefaultCredentialsError:
            adc = None
        if adc is not None:
            creds = adc
    if creds is None:
        raise RuntimeError(
            "No hay credenciales de Google Drive (solo lectura).\n"
            "  • ``gcloud auth application-default login`` incluyendo el scope de Drive, p. ej.:\n"
            "    gcloud auth application-default login "
            "--scopes=https://www.googleapis.com/auth/drive.readonly,"
            "https://www.googleapis.com/auth/cloud-platform\n"
            "  • O definí ``FIC_DRIVE_TOKEN_JSON`` con un JSON de usuario autorizado (OAuth) "
            "que incluya ``https://www.googleapis.com/auth/drive.readonly``.\n"
            "  • En Google Cloud Console, habilitá la API **Google Drive** para el proyecto que uses."
        )
    if not creds.valid:
        creds.refresh(GoogleAuthRequest())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_drive_folder_ids_by_exact_name(service, folder_title: str) -> list[str]:
    esc = _drive_query_escape_literal(folder_title)
    q = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{esc}' "
        "and trashed=false"
    )
    out: list[str] = []
    page_token: str | None = None
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in resp.get("files") or []:
            fid = f.get("id")
            if isinstance(fid, str) and fid:
                out.append(fid)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _list_geotiff_sizes_in_drive_folder(service, folder_id: str) -> dict[str, int]:
    """Nombre de archivo en Drive → tamaño en bytes (solo ``.tif`` / ``.tiff``, no carpetas)."""
    q = f"'{folder_id}' in parents and trashed=false"
    out: dict[str, int] = {}
    page_token: str | None = None
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(name, size, mimeType)",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in resp.get("files") or []:
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                continue
            name = f.get("name") or ""
            low = name.lower()
            if not low.endswith((".tif", ".tiff")):
                continue
            try:
                sz = int(f.get("size") or 0)
            except (TypeError, ValueError):
                sz = 0
            out[name] = max(out.get(name, 0), sz)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _lower_geotiff_size_index(children: dict[str, int]) -> dict[str, int]:
    """Índice ``nombre.minúscula`` → tamaño máximo (por si hay duplicados de distinto case)."""
    lo: dict[str, int] = {}
    for name, sz in children.items():
        k = name.lower()
        lo[k] = max(lo.get(k, 0), sz)
    return lo


def _stem_satisfies_remote_tif(
    lower_sizes: dict[str, int], stem: str, min_bytes: int
) -> bool:
    for suf in (f"{stem.lower()}.tif", f"{stem.lower()}.tiff"):
        if lower_sizes.get(suf, 0) >= min_bytes:
            return True
    return False


def _pick_drive_folder_covering_expected(
    service,
    folder_ids: list[str],
    expected_stems: set[str],
    min_bytes: int,
) -> tuple[str | None, set[str]]:
    """
    Elige la carpeta cuyo contenido deja menos stems faltantes; devuelve ``(folder_id, missing)``.
    Si ``missing`` está vacío, ``folder_id`` es la carpeta que cubre todo.
    """
    best_id: str | None = None
    best_missing: set[str] | None = None
    for fid in folder_ids:
        children = _list_geotiff_sizes_in_drive_folder(service, fid)
        lo = _lower_geotiff_size_index(children)
        missing = {s for s in expected_stems if not _stem_satisfies_remote_tif(lo, s, min_bytes)}
        if best_missing is None or len(missing) < len(best_missing):
            best_id = fid
            best_missing = missing
    return best_id, best_missing if best_missing is not None else set(expected_stems)


def wait_for_remote_drive_tifs(
    *,
    folder_leaf: str,
    expected_stems: set[str],
    poll_seconds: float,
    timeout_seconds: float,
    min_file_bytes: int,
    log_interval: float,
    service=None,
) -> None:
    """
    Hasta que la API de Drive liste todos los ``expected_stems`` como ``.tif``/``.tiff`` con tamaño
    ≥ ``min_file_bytes`` dentro de una carpeta cuyo nombre coincide exactamente con ``folder_leaf``.

    Si ``service`` es ``None``, se construye un cliente Drive v3 (una sola vez por llamada).
    """
    if not expected_stems:
        return
    _, _, _, _, HttpError = _import_google_drive_client()
    if service is None:
        service = build_google_drive_v3_service()
    n = len(expected_stems)
    print(
        f"\nVerificación remota (Drive API): carpeta «{folder_leaf}» — "
        f"esperando {n} GeoTIFF(s) encolado(s) en esta corrida…",
        flush=True,
    )
    print(
        "  (Si falla con 403/insufficientPermissions, ampliá scopes de tus credenciales o usá "
        "FIC_DRIVE_TOKEN_JSON.)",
        flush=True,
    )
    start = time.monotonic()
    last_log = start
    poll_seconds = max(5.0, float(poll_seconds))
    log_interval = max(poll_seconds, float(log_interval))
    deadline = start + max(30.0, float(timeout_seconds))

    while time.monotonic() < deadline:
        try:
            folder_ids = _list_drive_folder_ids_by_exact_name(service, folder_leaf)
        except HttpError as exc:
            raise RuntimeError(
                f"Drive API al listar carpetas «{folder_leaf}»: {exc!r}\n"
                "Comprobá que la API Google Drive esté habilitada y que las credenciales tengan "
                "alcance drive.readonly."
            ) from exc
        if not folder_ids:
            if time.monotonic() - last_log >= log_interval:
                print(
                    f"  Aún no existe carpeta «{folder_leaf}» en Drive (o no es visible con estas "
                    f"credenciales). Reintentando… ({int(time.monotonic() - start)}s)",
                    flush=True,
                )
                last_log = time.monotonic()
            time.sleep(poll_seconds)
            continue

        try:
            _fid, missing = _pick_drive_folder_covering_expected(
                service, folder_ids, expected_stems, min_file_bytes
            )
        except HttpError as exc:
            raise RuntimeError(
                f"Drive API al listar archivos en «{folder_leaf}»: {exc!r}"
            ) from exc

        if not missing:
            elapsed = int(time.monotonic() - start)
            print(
                f"  Drive API: los {n} archivo(s) esperado(s) están en «{folder_leaf}» "
                f"(tamaño ≥ {min_file_bytes} B). ({elapsed}s)",
                flush=True,
            )
            return

        now = time.monotonic()
        if now - last_log >= log_interval:
            sample = sorted(missing)[:12]
            more = "" if len(missing) <= 12 else f" … (+{len(missing) - 12} más)"
            print(
                f"  Faltan {len(missing)}/{n} en Drive (ej.: {', '.join(sample)}{more}). "
                f"Reintento en {poll_seconds:g}s… ({int(now - start)}s)",
                flush=True,
            )
            last_log = now
        time.sleep(poll_seconds)

    raise RuntimeError(
        f"Timeout esperando GeoTIFF en Drive (carpeta «{folder_leaf}»): "
        f"tras {int(timeout_seconds)}s no constan todos los {n} archivo(s) vía API. "
        "Revisá tareas en Earth Engine, cuota de Drive y credenciales."
    )


def load_predios_from_geojson(geojson_path: str | Path) -> list[dict]:
    """
    Lee predios desde GeoJSON local. Retorna lista de ``{predio_id, name, geometry}``
    donde ``geometry`` es un ``ee.Geometry`` ya inicializado.
    Excluye ``lote_demo`` y cualquier feature sin id.
    """
    with open(geojson_path, encoding="utf-8") as f:
        fc = json.load(f)
    out: list[dict] = []
    for feat in fc.get("features", []):
        props = feat.get("properties") or {}
        wid = (
            props.get("wetland_id") or props.get("predio_id") or ""
        ).strip().lower()
        if not wid or wid == "lote_demo":
            continue
        name = (props.get("nombre") or wid.upper()).strip()
        geom = ee.Geometry(feat["geometry"])
        out.append({"predio_id": wid, "name": name, "geometry": geom})
    return out


def _ic_filter_month_hist(
    ic: ee.ImageCollection, month: int, current_year: int
) -> ee.ImageCollection:
    """Imágenes cuyo ``system:time_start`` cae en el mes ``month`` (1-12), excluyendo ``current_year``."""
    return ic.filter(ee.Filter.calendarRange(month, month, "month")).filter(
        ee.Filter.neq("year", current_year)
    )


def _ic_filter_year_month(
    ic: ee.ImageCollection, year: int, month: int
) -> ee.ImageCollection:
    return ic.filter(ee.Filter.eq("year", year)).filter(
        ee.Filter.calendarRange(month, month, "month")
    )


def _safe_median(ic: ee.ImageCollection, bands: list[str]) -> ee.Image:
    """``median()`` de la colección; si está vacía GEE devolverá imagen de ceros enmascarados."""
    return ic.select(bands).median()


def _enqueue_predios_drive_task(
    img: ee.Image,
    geom: ee.Geometry,
    stem: str,
    drive_root_folder: str,
    usage_subdir: str,
    scale: float,
    tasks: list,
    *,
    dry_run: bool,
) -> None:
    """
    ``folder`` en Drive = un solo nombre bajo «Mi unidad» (sin ``/`` literal); el tipo de compuesto
    va en ``stem`` (p. ej. ``…_annual_alltime``). ``usage_subdir`` alimenta el prefijo de la descripción
    de la tarea.
    """
    folder = sanitize_drive_folder_name(drive_root_folder)
    if dry_run:
        print(f"  [dry-run] encolaría: {stem}.tif → Drive/{folder}")
        return
    sub_tag = usage_subdir.strip().strip("/").replace("..", "_").replace("/", "_")
    desc = f"S2p_{sub_tag}_{stem}"[:100].replace(" ", "_").replace("/", "_")
    t = ee.batch.Export.image.toDrive(
        image=_image_for_geotiff_drive_export(img.clip(geom)),
        description=desc,
        folder=folder,
        fileNamePrefix=stem,
        region=geom,
        scale=scale,
        crs="EPSG:4326",
        maxPixels=1e11,
        fileFormat="GeoTIFF",
    )
    t.start()
    tasks.append(t)
    print(f"  Encolado: {stem}.tif → Drive/{folder}")


def export_predios_composites_to_drive(
    collection_id: str,
    predios: list[dict],
    *,
    bands: list[str],
    current_year: int,
    current_month: int,
    drive_root_folder: str,
    stem_prefix: str = "S2",
    scale: float = DEFAULT_PREDIOS_VIZ_SCALE,
    dry_run: bool = False,
    skip_existing_stems: set[str] | None = None,
) -> tuple[list[ee.batch.Task], set[str]]:
    """
    Por cada predio encola exports Drive de GeoTIFF multi-banda (``bands`` como capas).
    Los valores son int16 ×INDEX_INT16_SCALE (misma convención que el asset de exportación).

    Los archivos van bajo **una** carpeta Drive (nombre colapsado de ``drive_root_folder``), por
    ejemplo ``FIC_RASTER_S2_semanales_por_predio`` con ``S2_G1_annual_alltime.tif`` (subcarpetas por uso no son posibles vía
    ``folder`` con ``/`` en la API de GEE).

    Tipos de compuesto (sufijo del nombre de archivo):
      * ``annual_alltime``         – mediana de todas las semanas disponibles (todos los años).
      * ``annual_{year}``          – mediana del año ``current_year``.
      * ``monthly_hist_{MM:02d}``  – mediana histórica del mes (excluye ``current_year``), para MM 1-12.
      * ``monthly_{year}_{MM:02d}``– mediana del mes ``current_month`` del año ``current_year``.
      * ``weekly_last``            – imagen más reciente de la colección (última semana completa).

    Si ``skip_existing_stems`` contiene el ``stem`` del archivo (sin ``.tif``), no se encola.

    Retorna ``(tareas, stems_encolados)``.
    """
    ic = ee.ImageCollection(collection_id.strip().rstrip("/"))
    tasks: list[ee.batch.Task] = []
    enqueued_stems: set[str] = set()
    skip = skip_existing_stems or set()

    print(f"Colección: {collection_id}  |  bandas: {bands}  |  año actual: {current_year}, mes: {current_month}")
    ic_size = ic.size().getInfo()
    print(f"Imágenes en colección: {ic_size}")
    if ic_size == 0:
        print("Colección vacía: no hay nada que exportar a Drive por predio.", file=sys.stderr)
        return tasks, enqueued_stems

    comp_alltime = _safe_median(ic, bands)
    comp_annual_curr = _safe_median(ic.filter(ee.Filter.eq("year", current_year)), bands)
    comp_monthly_curr = _safe_median(_ic_filter_year_month(ic, current_year, current_month), bands)
    comp_monthly_hist: dict[int, ee.Image] = {
        m: _safe_median(_ic_filter_month_hist(ic, m, current_year), bands)
        for m in range(1, 13)
    }
    img_weekly_last = ic.select(bands).sort("system:time_start", False).first()

    for predio in predios:
        pid = predio["predio_id"]
        geom = predio["geometry"]
        pid_up = pid.upper()
        print(f"Predio {pid_up}...")

        def _enq(img: ee.Image, suffix: str, usage_subdir: str) -> None:
            stem = f"{stem_prefix}_{pid_up}_{suffix}"
            if stem in skip:
                print(f"  [omitir] {stem}.tif (ya en destino: repo o carpeta Drive sincronizada)")
                return
            if dry_run:
                print(f"  [dry-run] encolaría: {stem}.tif → Drive/{sanitize_drive_folder_name(drive_root_folder)}")
                return
            _enqueue_predios_drive_task(
                img, geom, stem, drive_root_folder, usage_subdir, scale, tasks, dry_run=dry_run
            )
            enqueued_stems.add(stem)

        _enq(comp_alltime, "annual_alltime", "annual_alltime")
        _enq(comp_annual_curr, f"annual_{current_year}", f"annual_{current_year}")
        _enq(
            comp_monthly_curr,
            f"monthly_{current_year}_{current_month:02d}",
            f"monthly_{current_year}_{current_month:02d}",
        )
        for m in range(1, 13):
            suf = f"monthly_hist_{m:02d}"
            _enq(comp_monthly_hist[m], suf, suf)
        _enq(ee.Image(img_weekly_last), "weekly_last", "weekly_last")

    return tasks, enqueued_stems


def resolve_year_range(args: argparse.Namespace, now_year: int) -> tuple[int, int]:
    if args.year is not None and (args.start_year is not None or args.end_year is not None):
        raise ValueError("Usa solo --year, o bien --start-year/--end-year, no ambos.")
    if args.year is not None:
        return args.year, args.year
    env_start = os.environ.get("GEE_START_YEAR", "").strip()
    if env_start.isdigit():
        default_start = int(env_start)
    else:
        default_start = DEFAULT_START_YEAR
    env_end = os.environ.get("GEE_END_YEAR", "").strip()
    if env_end.isdigit():
        default_end = int(env_end)
    elif DEFAULT_END_YEAR is not None:
        default_end = DEFAULT_END_YEAR
    else:
        default_end = now_year
    start_y = args.start_year if args.start_year is not None else default_start
    end_y = args.end_year if args.end_year is not None else default_end
    if start_y > end_y:
        raise ValueError(f"--start-year ({start_y}) no puede ser mayor que el último año ({end_y}).")
    return start_y, min(end_y, now_year)


def run_image_collection_exports(
    args: argparse.Namespace,
    init_project: str,
    export_prefix: str,
    aoi_asset: str,
) -> None:
    """Exporta mosaicos semanales a la ImageCollection bajo ``export_prefix`` (incremental o --force)."""
    if not args.skip_ensure_destination:
        ensure_export_destination(export_prefix, dry_run=args.dry_run)

    aoi = ee.FeatureCollection(aoi_asset).geometry()
    end_exclusive_ms = utc_monday_00_current_week_ms()
    end_exclusive = ee.Date(end_exclusive_ms)
    now_year = datetime.now(timezone.utc).year

    start_y, end_y = resolve_year_range(args, now_year)

    skip_existing = not args.force
    existing_ids: set[str] = set()
    n_listed = 0
    if skip_existing:
        existing_ids, n_listed = load_existing_export_keys(export_prefix)
        print(f"Assets hijos ya en destino (listAssets): {n_listed}")

    audit_by_year: dict[int, list[dict]] = {}
    if args.force:
        years_to_process = list(range(start_y, end_y + 1))
        print(
            "Modo --force: se procesan todos los años del rango y se re-encolan exportaciones "
            "(aunque el asset ya exista)."
        )
    else:
        print(
            "Modo incremental: comprobando semanas pendientes por año (auditoría, sin construir el grafo completo)..."
        )
        years_to_process, audit_by_year = compute_incremental_year_plan(
            export_prefix,
            existing_ids,
            aoi,
            end_exclusive,
            start_y,
            end_y,
        )
        skipped_years = [y for y in range(start_y, end_y + 1) if y not in years_to_process]
        if skipped_years:
            print(
                "  Años sin trabajo pendiente en assets (todas las semanas con datos ya exportadas o sin datos): "
                f"{skipped_years}"
            )
        if not years_to_process:
            print(
                "Nada que exportar a ImageCollection: no quedan semanas con datos sin asset bajo el prefijo. "
                "(Se continúa con exportación Drive por predio si está activada.)"
            )
        else:
            print(f"  Años a procesar en assets (al menos una semana pendiente): {years_to_process}")

    build_years = sorted(years_to_process)
    build_start = min(build_years) if build_years else None
    build_end = max(build_years) if build_years else None
    n_years_requested = end_y - start_y + 1
    n_years_build = len(build_years)

    print(
        "Fin datos (UTC): escenas con tiempo < lunes 00:00 de la semana ISO actual "
        f"({datetime.fromtimestamp(end_exclusive_ms/1000, tz=timezone.utc).isoformat()}); "
        "última semana exportada es la anterior completa (lun–dom)."
    )
    print("Assets: nombre `Y<iso_año>_W<ww>` con semana ISO 01–53 (p. ej. `Y2026_W09`).")
    print(f"Proyecto Cloud (initialize): {init_project}")
    print(f"AOI asset     : {aoi_asset}")
    print(f"Export prefix : {export_prefix}")
    print(f"Omitir dup.   : {'sí' if skip_existing else 'no ( --force )'}")
    if build_years:
        print(
            f"Rango solicitado: {start_y} → {end_y} ({n_years_requested} año(s)); "
            f"exportación con grafo por año civil ({build_start} → {build_end}, {n_years_build} año(s))."
        )
    else:
        print(
            f"Rango solicitado: {start_y} → {end_y} ({n_years_requested} año(s)); "
            "sin años con grafo de assets en esta corrida."
        )
    if n_years_requested > 1 and build_years and n_years_build < n_years_requested:
        print(
            "  (Solo años pendientes entran al grafo; el resto ya estaba completo bajo el prefijo.)"
        )
    print(
        "  Cada año acota S2+Cloud Score+ al intervalo ISO mínimo necesario (menos memoria de usuario en GEE)."
    )
    if args.export_half != "all":
        print(
            f"  --export-half={args.export_half}: solo esa mitad de semanas ISO por año "
            "(ej. tras first, ejecutar con second para completar el año)."
        )

    if args.dry_run:
        print("Modo dry-run: no se envían tareas a GEE.")

    total_enq = 0
    total_skip = 0
    total_mosaics = 0
    for y in years_to_process:
        print(f"Año {y}...")
        if args.export_half != "all":
            sh = slice_iso_week_specs_for_export_half(
                iso_week_specs_thursday_in_calendar_year(y, end_exclusive_ms),
                args.export_half,
            )
            if sh:
                iy0, iw0 = int(sh[0]["iso_year"]), int(sh[0]["iso_week"])
                iy1, iw1 = int(sh[-1]["iso_year"]), int(sh[-1]["iso_week"])
                print(
                    f"  Rango ISO en esta tanda: {iy0}-W{iw0:02d} … {iy1}-W{iw1:02d} "
                    f"({len(sh)} semanas de año civil {y})"
                )
        final_collection = build_weekly_collection(
            aoi, end_exclusive, [y], export_half=args.export_half
        )
        n_m = final_collection.size().getInfo()
        total_mosaics += n_m
        print(f"  Mosaicos semanales con escenas: {n_m}")
        precalc = audit_by_year.get(y) if audit_by_year else None
        enq, skipped = export_year(
            final_collection,
            aoi,
            export_prefix,
            y,
            dry_run=args.dry_run,
            existing_ids=existing_ids,
            skip_existing=skip_existing,
            end_exclusive=end_exclusive,
            audit=not args.no_audit,
            audit_rows_precalc=precalc,
            export_half=args.export_half,
        )
        total_enq += enq
        total_skip += skipped

    print(f"Mosaicos con datos (suma por año procesado en esta corrida): {total_mosaics}")
    print(
        f"Tareas ImageCollection encoladas (o simuladas en dry-run): {total_enq}; "
        f"omitidas (ya existían): {total_skip}"
    )
    print("Revisa el progreso en: https://code.earthengine.google.com/tasks")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Por defecto: primero encola a la ImageCollection en GEE los mosaicos semanales pendientes, "
            "luego a Drive cada semana ``Y*_W*`` ya en la colección, **recortada por predio** "
            "(misma carpeta Drive que los compuestos: nombre colapsado de ``--drive-folder-root``), "
            "espera tareas GEE, comprueba por **API de Google Drive** que los .tif encolados existan en la nube, "
            "copia local si hay FIC_DRIVE_LOCAL. "
            "Solo Drive sin actualizar la IC: ``--no-export-assets`` o ``--predios-drive-only``. "
            "Sin comprobar Drive remoto: ``--no-verify-drive-remote``. "
            "Compuestos annual/monthly/… solo con ``--export-predios-composites``."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No encola tareas; solo lista descripciones y assetIds.",
    )
    parser.add_argument(
        "--project",
        default=None,
        metavar="GCP_PROJECT_ID",
        help=(
            f"Proyecto Google Cloud para ee.Initialize() (por defecto: {DEFAULT_CLOUD_PROJECT}, "
            "salvo EE_CLOUD_PROJECT o GOOGLE_CLOUD_PROJECT)."
        ),
    )
    parser.add_argument(
        "--aoi-asset",
        default=os.environ.get("GEE_AOI_ASSET", DEFAULT_AOI_ASSET),
        help="Earth Engine AssetId del AOI (vectores).",
    )
    parser.add_argument(
        "--export-prefix",
        default=os.environ.get("GEE_EXPORT_PREFIX", DEFAULT_EXPORT_PREFIX),
        help="Prefijo de assetIds de salida sin barra final.",
    )
    parser.add_argument(
        "--skip-ensure-destination",
        action="store_true",
        help="No crear carpeta/colección vacía antes de exportar.",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="No imprimir auditoría semana a semana (solo mensajes mínimos).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Encolar todas las semanas aunque el asset hijo ya exista.",
    )
    parser.add_argument(
        "--empty-collection",
        action="store_true",
        help=(
            "DESTRUCTIVO: elimina todas las imágenes de la ImageCollection destino antes de "
            "exportar (la colección se conserva). Combinar con --start-year para re-exportar."
        ),
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        metavar="AAAA",
        help="Solo ese año civil; no usar junto con --start-year/--end-year.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        metavar="AAAA",
        help="Primer año (default: GEE_START_YEAR o DEFAULT_START_YEAR en el script).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        metavar="AAAA",
        help=(
            "Último año (por defecto: GEE_END_YEAR o DEFAULT_END_YEAR; "
            "nunca por encima del año actual en tiempo de ejecución)."
        ),
    )
    parser.add_argument(
        "--export-half",
        choices=("all", "first", "second"),
        default="all",
        metavar="HALF",
        help=(
            "Mitad de semanas ISO del año civil (orden de la lista del script): "
            "'first' ≈ primera mitad, 'second' el resto. Ejecutar dos veces (first, luego second) "
            "reduce el grafo GEE frente a exportar el año entero. Por defecto all."
        ),
    )
    parser.add_argument(
        "--export-assets",
        action="store_true",
        help=(
            "[Compat.] Sin efecto: la IC ya se actualiza por defecto antes de Drive. "
            "Usá --no-export-assets para omitir ese paso."
        ),
    )
    parser.add_argument(
        "--no-export-assets",
        action="store_true",
        help=(
            "No encolar mosaicos a la ImageCollection en GEE; solo Drive (semanales por predio y/o "
            "compuestos) y copia local. Equivale a saltar el proceso 1."
        ),
    )
    parser.add_argument(
        "--export-predios-composites",
        action="store_true",
        help=(
            "Encolar además compuestos por predio (annual_alltime, monthly_*, weekly_last, …). "
            "Por defecto no (solo semanales por predio)."
        ),
    )

    # -----------------------------------------------------------------------
    # Google Drive por predio (GEE: un solo nombre de carpeta, sin / anidado)
    # -----------------------------------------------------------------------
    parser.add_argument(
        "--no-drive-predios",
        action="store_true",
        help="No encolar compuestos por predio (annual, monthly, …); solo aplica con --export-predios-composites.",
    )
    parser.add_argument(
        "--no-drive-weekly-ic",
        action="store_true",
        help=(
            "No encolar GeoTIFF semanales **por predio** desde la ImageCollection "
            "(misma carpeta Drive que los compuestos; predio en el nombre del archivo)."
        ),
    )
    parser.add_argument(
        "--drive-weekly-subdir",
        default=DRIVE_WEEKLY_PREDIO_SUBDIR,
        metavar="NAME",
        help=(
            "Solo para **omitir duplicados** en el repo: subcarpeta legado bajo data/sentinel2 donde "
            "buscar stems ``S2_*_Y*_W*`` (default: "
            f"{DRIVE_WEEKLY_PREDIO_SUBDIR!r}). No cambia el nombre de la carpeta en Google Drive."
        ),
    )
    parser.add_argument(
        "--drive-weekly-scale",
        type=float,
        default=WEEKLY_IC_DRIVE_SCALE_M,
        metavar="METROS",
        help=f"Resolución en m de esos GeoTIFF semanales (default: {WEEKLY_IC_DRIVE_SCALE_M}).",
    )
    parser.add_argument(
        "--predios-drive-only",
        action="store_true",
        help="Solo Drive: semanales por predio y/o compuestos; no encola export a la ImageCollection en GEE.",
    )
    parser.add_argument(
        "--drive-folder-root",
        default=os.environ.get("FIC_DRIVE_FOLDER_ROOT", DEFAULT_DRIVE_PREDIOS_ROOT).strip(),
        metavar="NAME",
        help=(
            f"Carpeta raíz en Google Drive para GeoTIFF por predio (default: {DEFAULT_DRIVE_PREDIOS_ROOT})."
        ),
    )
    parser.add_argument(
        "--drive-local-root",
        default=os.environ.get("FIC_DRIVE_LOCAL", "").strip() or None,
        metavar="PATH",
        help=(
            "Directorio local de sincronización de Google Drive que contiene la carpeta raíz "
            f"(p. ej. …/Mi unidad). También: FIC_DRIVE_LOCAL. "
            "Sirve para omitir re-export a Drive lo que ya esté en ese espejo (EE no lista Drive)."
        ),
    )
    parser.add_argument(
        "--data-sentinel2-dir",
        default=str(DEFAULT_DATA_SENTINEL2),
        metavar="PATH",
        help=f"Destino local (default: repo/{DEFAULT_DATA_SENTINEL2.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--no-drive-local-copy",
        action="store_true",
        help="No copiar desde Drive local a data/sentinel2.",
    )
    parser.add_argument(
        "--no-wait-drive-tasks",
        action="store_true",
        help=(
            "No esperar a que terminen las tareas Export toDrive de esta corrida antes de la "
            "verificación remota (si está activa) y la copia local; las tareas pueden seguir en GEE."
        ),
    )
    parser.add_argument(
        "--no-verify-drive-remote",
        action="store_true",
        help=(
            "No usar la API de Google Drive para comprobar que todos los GeoTIFF encolados en esta "
            "corrida existan ya en la nube antes de la copia local."
        ),
    )
    parser.add_argument(
        "--verify-drive-poll-seconds",
        type=float,
        default=DEFAULT_VERIFY_DRIVE_POLL_SECONDS,
        metavar="SEC",
        help=(
            "Intervalo entre consultas a Drive al comprobar archivos remotos "
            f"(mín. 5; default {DEFAULT_VERIFY_DRIVE_POLL_SECONDS:g})."
        ),
    )
    parser.add_argument(
        "--verify-drive-timeout-seconds",
        type=float,
        default=DEFAULT_VERIFY_DRIVE_TIMEOUT_SECONDS,
        metavar="SEC",
        help=f"Tiempo máximo esperando que aparezcan todos los .tif en Drive (default {DEFAULT_VERIFY_DRIVE_TIMEOUT_SECONDS:g}).",
    )
    parser.add_argument(
        "--verify-drive-min-file-bytes",
        type=int,
        default=DEFAULT_VERIFY_DRIVE_MIN_FILE_BYTES,
        metavar="N",
        help=(
            "Tamaño mínimo en bytes para considerar que un .tif remoto está completo "
            f"(default {DEFAULT_VERIFY_DRIVE_MIN_FILE_BYTES})."
        ),
    )
    parser.add_argument(
        "--verify-drive-log-interval",
        type=float,
        default=DEFAULT_VERIFY_DRIVE_LOG_INTERVAL,
        metavar="SEC",
        help=f"Cada cuántos segundos loguear progreso de la verificación remota (default {DEFAULT_VERIFY_DRIVE_LOG_INTERVAL:g}).",
    )
    parser.add_argument(
        "--drive-task-poll-seconds",
        type=float,
        default=DEFAULT_DRIVE_TASK_POLL_SECONDS,
        metavar="SEC",
        help=(
            f"Intervalo entre consultas de estado de las tareas Drive (mín. 5; default {DEFAULT_DRIVE_TASK_POLL_SECONDS:g})."
        ),
    )
    parser.add_argument(
        "--drive-task-log-interval",
        type=float,
        default=DEFAULT_DRIVE_TASK_LOG_INTERVAL,
        metavar="SEC",
        help=f"Cada cuántos segundos imprimir progreso (default {DEFAULT_DRIVE_TASK_LOG_INTERVAL:g}).",
    )
    parser.add_argument(
        "--drive-sync-grace-seconds",
        type=float,
        default=DEFAULT_DRIVE_SYNC_GRACE_SECONDS,
        metavar="SEC",
        help=(
            "Tras COMPLETED en GEE, espera antes de copiar desde el espejo local (default "
            f"{DEFAULT_DRIVE_SYNC_GRACE_SECONDS:g}s; 0 para omitir). Solo si hay copia local y ruta de sync."
        ),
    )
    parser.add_argument(
        "--drive-force",
        action="store_true",
        help=(
            "Re-encolar Drive aunque el stem ya exista en data/sentinel2 o en el espejo bajo "
            "--drive-local-root (si está definido)."
        ),
    )
    parser.add_argument(
        "--export-predios-drive",
        default=None,
        metavar="NAME",
        help="[Compat.] Sobrescribe --drive-folder-root (nombre de la carpeta raíz en Drive).",
    )
    parser.add_argument(
        "--export-predios-collection",
        default=None,
        metavar="ASSET_ID",
        help="ImageCollection GEE para compuestos por predio (default: --export-prefix).",
    )
    parser.add_argument(
        "--export-predios-aoi-json",
        default=os.environ.get("FIC_AOI_GEOJSON", "data/vectors/cuarteles/cuarteles.geojson"),
        metavar="PATH",
        help="GeoJSON de predios (relativo al repo si no es absoluta).",
    )
    parser.add_argument(
        "--export-predios-bands",
        default=",".join(DEFAULT_PREDIOS_VIZ_BANDS),
        metavar="BANDS",
        help=(
            f"Bandas separadas por coma en GeoTIFF por predio. "
            f"Por defecto: {','.join(DEFAULT_PREDIOS_VIZ_BANDS)}."
        ),
    )
    parser.add_argument(
        "--export-predios-scale",
        type=float,
        default=DEFAULT_PREDIOS_VIZ_SCALE,
        metavar="METROS",
        help=f"Resolución en m de exports por predio (default: {DEFAULT_PREDIOS_VIZ_SCALE}).",
    )
    parser.add_argument(
        "--export-predios-stem-prefix",
        default="S2",
        metavar="PREFIX",
        help="Prefijo del nombre de archivo (default: S2).",
    )
    parser.add_argument(
        "--export-predios-current-year",
        type=int,
        default=None,
        metavar="AAAA",
        help="Año de referencia para compuestos 'actuales' (default: año UTC).",
    )
    parser.add_argument(
        "--export-predios-current-month",
        type=int,
        default=None,
        metavar="MM",
        help="Mes de referencia para último mes completo (default: mes anterior UTC).",
    )

    args = parser.parse_args()

    init_project = resolve_cloud_project(args.project)
    ee_initialize(init_project)

    export_prefix = args.export_prefix.strip().rstrip("/")
    aoi_asset = args.aoi_asset.strip().rstrip("/")
    drive_folder_name = (
        args.export_predios_drive.strip()
        if args.export_predios_drive
        else args.drive_folder_root.strip()
    ) or DEFAULT_DRIVE_PREDIOS_ROOT
    data_sentinel2_dir = resolve_repo_path(args.data_sentinel2_dir)
    drive_export_leaf = sanitize_drive_folder_name(drive_folder_name)

    drive_sync_parent: Path | None = None
    if args.drive_local_root:
        _dl = resolve_repo_path(args.drive_local_root)
        if _dl.is_dir():
            drive_sync_parent = _dl
        else:
            print(
                f"Aviso: --drive-local-root no es un directorio existente: {_dl}",
                file=sys.stderr,
            )

    tasks_w: list[ee.batch.Task] = []
    tasks_p: list[ee.batch.Task] = []
    weekly_enqueued_stems: set[str] = set()
    composite_enqueued_stems: set[str] = set()

    if args.empty_collection:
        print(f"== Vaciando ImageCollection destino: {export_prefix} ==")
        empty_collection(export_prefix, dry_run=args.dry_run)

    ran_assets = not args.predios_drive_only and not args.no_export_assets
    if ran_assets:
        run_image_collection_exports(args, init_project, export_prefix, aoi_asset)
    elif not args.predios_drive_only:
        print("")
        print(
            "(ImageCollection: omitido — --no-export-assets; no se encolan mosaicos nuevos a GEE en esta corrida.)"
        )

    ran_weekly_predio = not args.no_drive_weekly_ic
    ran_composites = bool(args.export_predios_composites) and not args.no_drive_predios

    if args.predios_drive_only and not ran_weekly_predio and not ran_composites:
        print(
            "Error: --predios-drive-only sin trabajo (ni semanales por predio ni --export-predios-composites).",
            file=sys.stderr,
        )
        sys.exit(1)

    predios: list[dict] = []
    if ran_weekly_predio or ran_composites:
        aoi_path = resolve_repo_path(args.export_predios_aoi_json)
        if not aoi_path.exists():
            print(f"Error: GeoJSON de predios no encontrado: {aoi_path}", file=sys.stderr)
            sys.exit(1)
        predios = load_predios_from_geojson(aoi_path)
        if not predios:
            print("Error: no se encontraron predios en el GeoJSON.", file=sys.stderr)
            sys.exit(1)

    if ran_weekly_predio:
        skip_weekly: set[str] | None = None
        if not args.drive_force:
            skip_weekly = collect_existing_weekly_predio_stems(
                data_sentinel2_dir, args.drive_weekly_subdir
            ) | collect_tif_stems_from_drive_local_mirror(
                drive_sync_parent, drive_export_leaf
            )
        print("")
        print("=== Drive: mosaicos semanales por predio (toda la ImageCollection) ===")
        print(
            f"Colección: {export_prefix!r}  |  predios: {[p['predio_id'] for p in predios]}  |  "
            f"Drive (carpeta única): {drive_export_leaf}/"
        )
        if skip_weekly:
            src = []
            src.append(f"{data_sentinel2_dir.relative_to(REPO_ROOT)}/ (recursivo, stems S2_*_Y*_W*)")
            if drive_sync_parent:
                src.append(f"{drive_sync_parent}/{drive_export_leaf}/ (sync Drive)")
            print(
                f"Incremental: omitiendo {len(skip_weekly)} stem(s) ya presentes en "
                + " o ".join(src)
                + " (--drive-force para re-encolar todo)."
            )
        elif not args.drive_force and not drive_sync_parent:
            print(
                "Aviso: sin --drive-local-root / FIC_DRIVE_LOCAL no se puede comprobar qué .tif "
                "ya están **solo** en Google Drive; se omiten duplicados respecto de data/sentinel2."
            )
        tasks_w, weekly_enqueued_stems = export_weekly_rasters_per_predio_to_drive(
            export_prefix,
            predios,
            drive_folder_name,
            stem_prefix=args.export_predios_stem_prefix,
            scale_m=args.drive_weekly_scale,
            dry_run=args.dry_run,
            skip_existing_stems=skip_weekly,
        )
        if not args.dry_run:
            print(f"Tareas Drive (semanales por predio) encoladas: {len(tasks_w)}")
    elif args.no_drive_weekly_ic:
        print("")
        print("Semanales por predio a Drive desactivados (--no-drive-weekly-ic).")

    if ran_composites:
        now = datetime.now(tz=timezone.utc)
        curr_year = args.export_predios_current_year or now.year
        if args.export_predios_current_month is not None:
            curr_month = args.export_predios_current_month
        else:
            curr_month = (now.month - 1) or 12

        collection_id = args.export_predios_collection or export_prefix
        bands = [b.strip() for b in args.export_predios_bands.split(",") if b.strip()]
        skip_predio: set[str] | None = None
        if not args.drive_force:
            skip_predio = collect_existing_predio_tif_stems(
                data_sentinel2_dir
            ) | collect_tif_stems_from_drive_local_mirror(
                drive_sync_parent, drive_export_leaf
            )

        print("")
        print("=== Drive: compuestos por predio (--export-predios-composites) ===")
        print(
            f"Carpeta Drive: {drive_export_leaf}/  |  "
            f"Colección: {collection_id}"
        )
        print(f"Bandas: {bands}  |  Año/mes ref.: {curr_year}/{curr_month:02d}")
        if skip_predio:
            loc = (
                f"repo + «{drive_sync_parent}/{drive_export_leaf}» (sync Drive)"
                if drive_sync_parent
                else "repo (sin sync Drive: no se ve la carpeta en la nube)"
            )
            print(
                f"Incremental: omitiendo {len(skip_predio)} stem(s) ya en {loc}. "
                "--drive-force para re-encolar."
            )
        elif not args.drive_force and not drive_sync_parent:
            print(
                "Aviso: sin --drive-local-root no se listan .tif ya subidos solo a Google Drive "
                "(solo se evitan duplicados respecto de data/sentinel2)."
            )

        tasks_p, composite_enqueued_stems = export_predios_composites_to_drive(
            collection_id,
            predios,
            bands=bands,
            current_year=curr_year,
            current_month=curr_month,
            drive_root_folder=drive_folder_name,
            stem_prefix=args.export_predios_stem_prefix,
            scale=args.export_predios_scale,
            dry_run=args.dry_run,
            skip_existing_stems=skip_predio,
        )
        if not args.dry_run:
            print(f"Tareas Drive (compuestos) encoladas: {len(tasks_p)}")
    elif args.export_predios_composites and args.no_drive_predios:
        print("")
        print("Compuestos por predio no encolados (--no-drive-predios).")

    drive_tasks_this_run = tasks_w + tasks_p
    did_remote_verify = False
    if drive_tasks_this_run and not args.dry_run:
        if not args.no_wait_drive_tasks:
            wait_for_export_tasks(
                drive_tasks_this_run,
                poll_seconds=max(5.0, float(args.drive_task_poll_seconds)),
                log_interval=max(
                    float(args.drive_task_poll_seconds), float(args.drive_task_log_interval)
                ),
            )
        else:
            print(
                "\n(Aviso: --no-wait-drive-tasks: no se espera el estado COMPLETED de las tareas GEE; "
                "la verificación remota en Drive —si está activa— puede tardar hasta que EE termine.)",
                flush=True,
            )

        if not args.no_verify_drive_remote:
            stems_by_folder: dict[str, set[str]] = {}
            if weekly_enqueued_stems:
                stems_by_folder.setdefault(drive_export_leaf, set()).update(weekly_enqueued_stems)
            if composite_enqueued_stems:
                stems_by_folder.setdefault(drive_export_leaf, set()).update(composite_enqueued_stems)
            has_stems_to_check = any(bool(s) for s in stems_by_folder.values())
            if has_stems_to_check:
                drive_svc = build_google_drive_v3_service()
                for leaf in sorted(stems_by_folder.keys()):
                    stems = stems_by_folder[leaf]
                    if not stems:
                        continue
                    wait_for_remote_drive_tifs(
                        folder_leaf=leaf,
                        expected_stems=stems,
                        poll_seconds=max(5.0, float(args.verify_drive_poll_seconds)),
                        timeout_seconds=float(args.verify_drive_timeout_seconds),
                        min_file_bytes=max(1, int(args.verify_drive_min_file_bytes)),
                        log_interval=max(
                            float(args.verify_drive_poll_seconds), float(args.verify_drive_log_interval)
                        ),
                        service=drive_svc,
                    )
                did_remote_verify = True

        grace = max(0.0, float(args.drive_sync_grace_seconds))
        need_grace = (not args.no_wait_drive_tasks and bool(drive_tasks_this_run)) or did_remote_verify
        if (
            grace > 0
            and need_grace
            and not args.no_drive_local_copy
            and drive_sync_parent is not None
            and drive_sync_parent.is_dir()
            and (ran_weekly_predio or ran_composites)
        ):
            print(
                f"\nPausa {grace:g}s para que el cliente de Google Drive escriba los .tif en el disco…",
                flush=True,
            )
            time.sleep(grace)

    if not args.no_drive_local_copy and (ran_weekly_predio or ran_composites):
        dlr = args.drive_local_root
        drive_parent = resolve_repo_path(dlr) if dlr else None
        if drive_parent is None or not drive_parent.is_dir():
            print("")
            print(
                "Copia local omitida: definí --drive-local-root o FIC_DRIVE_LOCAL (padre de "
                f"«{drive_export_leaf}»). "
                f"Destino: {data_sentinel2_dir.relative_to(REPO_ROOT)}."
            )
        else:
            copy_leaves: list[str] = [drive_export_leaf]
            total_nc = total_ns = 0
            for i, leaf in enumerate(copy_leaves):
                print("")
                print(
                    f"=== Copia local ({i + 1}/{len(copy_leaves)}): {drive_parent}/{leaf} → "
                    f"{data_sentinel2_dir.relative_to(REPO_ROOT)} ==="
                )
                nc, ns = copy_drive_sync_to_data_sentinel2(
                    drive_parent,
                    leaf,
                    data_sentinel2_dir,
                    dry_run=args.dry_run,
                )
                total_nc += nc
                total_ns += ns
                if nc == 0 and ns == 0:
                    print(
                        f"  No hay .tif en {drive_parent / leaf} (tareas en curso o carpeta vacía)."
                    )
                else:
                    print(f"  Copiados: {nc}; sin cambios: {ns}")
            print("")
            print(f"Copia local total: {total_nc} archivo(s); sin cambios: {total_ns}")
    elif args.no_drive_local_copy and (ran_weekly_predio or ran_composites):
        print("")
        print("(Copia local desactivada: --no-drive-local-copy.)")

    if (ran_weekly_predio or ran_composites or ran_assets) and not args.dry_run:
        print("")
        print("Tareas GEE: https://code.earthengine.google.com/tasks")


if __name__ == "__main__":
    try:
        main()
    except (ee.EEException, ValueError, RuntimeError) as exc:
        if isinstance(exc, ee.EEException):
            kind = "Earth Engine"
        elif isinstance(exc, RuntimeError):
            emsg = str(exc).lower()
            if (
                "drive api" in emsg
                or "timeout esperando geotiff" in emsg
                or "no hay credenciales de google drive" in emsg
                or "verificación remota en google drive requiere" in emsg
            ):
                kind = "Google Drive (API / credenciales)"
            else:
                kind = "Tareas Drive (Earth Engine)"
        else:
            kind = "Validación"
        print(f"Error ({kind}): {exc}", file=sys.stderr)
        if isinstance(exc, ee.EEException):
            emsg = str(exc).lower()
            if "memory limit" in emsg:
                print(
                    "Sugerencia: límite de memoria de usuario en GEE (tamaño del grafo). "
                    "Prueba el mismo año en dos pasos: `--export-half first` y luego "
                    "`--export-half second` (p. ej. con `--year 2025`). "
                    "También: AOI más pequeño, o cuota High-Volume Earth Engine en tu proyecto Cloud.",
                    file=sys.stderr,
                )
            else:
                print(
                    "Ejecuta `earthengine authenticate` y configura "
                    "EE_CLOUD_PROJECT (o --project=...) con el proyecto Cloud que Earth Engine muestra en "
                    "https://code.earthengine.google.com/ en Configuration "
                    "(necesitas rol Service Usage Consumer en ese proyecto).",
                    file=sys.stderr,
                )
        sys.exit(1)
