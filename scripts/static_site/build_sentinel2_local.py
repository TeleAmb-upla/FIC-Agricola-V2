#!/usr/bin/env python3
"""
Pipeline LOCAL Sentinel-2: GeoTIFF semanales en ``data/sentinel2/`` → agregados
mensual / semanal + chart de series temporales + WebPs por banda.

Convención de entrada (un archivo por predio × año × semana ISO)::

    data/sentinel2/S2_<PREDIO>_Y<YYYY>_W<WW>.tif   # int16/float64, 9 índices + clear_pixel_count, EPSG:4326

Salidas en ``data_static/sentinel2/``::

    metadata.json
    timeseries.json
    rasters/S2_<PREDIO>_<COMPOSITE_KEY>_<BAND>.webp
    csv/<predio>_timeseries.csv
    csv/<predio>_timeseries_monthly.csv

Composite keys generados:

  monthly_hist_<MM>         Mediana sobre todas las semanas (todos los a\u00f1os hist\u00f3ricos)
                            cuyo jueves cae en el mes ``MM``.
  monthly_current_<MM>      Mediana sobre semanas del a\u00f1o actual en el mes ``MM``
                            (solo se publica si hay suficiente cobertura).
  weekly_hist_W<WW>         Mediana sobre instancias de la semana ISO ``WW`` en a\u00f1os
                            hist\u00f3ricos. Solo se exporta la mediana como raster; los
                            percentiles 25/75 quedan en el ``timeseries.json``/CSV.
  weekly_current_W<WW>      Raster del a\u00f1o actual para la semana ISO ``WW``.

Ejemplo de uso::

    python scripts/static_site/build_sentinel2_local.py --force
    python scripts/static_site/build_sentinel2_local.py   # incremental (default)

Opción ``--no-incremental`` fuerza a releer todos los TIFs aunque no hayan cambiado.

Requisitos: rasterio, numpy, Pillow, matplotlib (ver requirements.txt).
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import re
import sys
import warnings
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import reproject, Resampling, transform_bounds, transform_geom
except ImportError as exc:  # pragma: no cover
    print(f"Falta rasterio: {exc}. pip install rasterio", file=sys.stderr)
    sys.exit(1)

try:
    from PIL import Image as PILImage
except ImportError as exc:  # pragma: no cover
    print(f"Falta Pillow: {exc}. pip install Pillow", file=sys.stderr)
    sys.exit(1)

try:
    from matplotlib import colormaps as _MPL_COLORMAPS
except ImportError as exc:  # pragma: no cover
    print(f"Falta matplotlib: {exc}. pip install matplotlib", file=sys.stderr)
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_SITE_DIR = REPO_ROOT / "scripts" / "static_site"
if str(STATIC_SITE_DIR) not in sys.path:
    sys.path.insert(0, str(STATIC_SITE_DIR))

from pipeline_utils import build_s2_file_to_predio_map, load_config, load_predio_clip_geometries

DEFAULT_TIF_DIR = REPO_ROOT / "data" / "sentinel2"
DEFAULT_STATIC_DIR = REPO_ROOT / "data_static" / "sentinel2"
DEFAULT_AOI_GEOJSON = REPO_ROOT / "data_static" / "predios_aoi.geojson"
DEFAULT_DB_CSV = REPO_ROOT / "data" / "fic_database.csv"

_STEM_RE = re.compile(r"^S2_(?P<predio>[A-Za-z0-9_]+)_Y(?P<year>\d{4})_W(?P<week>\d{2})$")

MONTH_NAMES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                  "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

# 9 índices operativos Sentinel-2 (único conjunto publicado en UI y series).
OPERATIVE_INDEX_BANDS: tuple[str, ...] = (
    "NDVI",
    "NDMI",
    "MNDWI",
    "REDEDGE_POSITION",
    "MCARI",
    "GNDVI",
    "MSAVI",
    "EVI",
    "PSRI",
)
OPERATIVE_INDEX_SET: frozenset[str] = frozenset(OPERATIVE_INDEX_BANDS)

# Visualización por banda (vmin, vmax, colormap matplotlib).
# Si una banda no está acá, se usa DEFAULT_VIZ.
BAND_VIZ: dict[str, dict] = {
    "NDVI":              {"vmin": -1.0, "vmax": 1.0,   "colormap": "RdYlGn",   "label": "NDVI"},
    "NDMI":              {"vmin": -1.0, "vmax": 1.0,   "colormap": "RdYlBu_r", "label": "NDMI"},
    "MNDWI":             {"vmin": -1.0, "vmax": 1.0,   "colormap": "Blues",    "label": "MNDWI"},
    "REDEDGE_POSITION":  {"vmin": 700.0,"vmax": 750.0, "colormap": "viridis",  "label": "Posición red edge (nm)"},
    "MCARI":             {"vmin":  0.0, "vmax": 3.0,   "colormap": "YlGn",     "label": "MCARI"},
    "GNDVI":             {"vmin": -1.0, "vmax": 1.0,   "colormap": "YlGn",     "label": "GNDVI"},
    "MSAVI":             {"vmin": -1.0, "vmax": 1.0,   "colormap": "YlGn",     "label": "MSAVI"},
    "EVI":               {"vmin": -1.0, "vmax": 1.0,   "colormap": "YlGn",     "label": "EVI"},
    "PSRI":              {"vmin": -0.5, "vmax": 0.5,   "colormap": "RdYlGn_r", "label": "PSRI"},
    "CLEAR_PIXEL_COUNT": {"vmin":  0.0, "vmax": 30.0,  "colormap": "viridis",  "label": "Clear pixel count"},
}
DEFAULT_VIZ = {"vmin": -1.0, "vmax": 1.0, "colormap": "RdYlGn", "label": ""}

# Bandas que normalmente se grafican en la series temporal (en este orden).
DEFAULT_CHART_BAND_ORDER = ["NDVI", "NDMI", "MNDWI", "EVI", "MSAVI", "GNDVI", "MCARI", "PSRI", "REDEDGE_POSITION"]

WEBP_QUALITY = 86
WEBP_METHOD = 4  # PIL WebP: 0=rápido, 6=más compresión. 4 balance velocidad/tamaño.
WEBP_UPSCALE_MIN_SIDE = 384  # Cada predio es chico (~13x17 px). Subimos hasta lado mín. ≥ N px.

# Divisor por banda (los TIF guardan los índices como int16-en-float64 escalados ×1000;
# ``clear_pixel_count`` es entero crudo).
DIVISOR_DEFAULT = 1000.0
# Escala Int16 por banda (coherente con export_s2.index_int16_scale): REDEDGE ×10, el resto ×1000.
BAND_DIVISOR_OVERRIDE: dict[str, float] = {
    "CLEAR_PIXEL_COUNT": 1.0,
    "REDEDGE_POSITION": 10.0,
}
# Bandas auxiliares / legado: no se publican en mapa ni series.
SKIP_BANDS: set[str] = {"CLEAR_PIXEL_COUNT"}


def is_published_band(name: str) -> bool:
    return str(name or "").strip().upper() in OPERATIVE_INDEX_SET
# Valores sentinel adicionales (vienen como int16): ±32767 / ±32768.
SENTINEL_VALUES = (32767.0, -32767.0, 32768.0, -32768.0)


# ---------------------------------------------------------------------------
# Date utilities (ISO weeks)
# ---------------------------------------------------------------------------

def iso_week_thursday(iso_year: int, iso_week: int) -> date:
    """
    Devuelve la fecha del jueves de la semana ISO (semana de calendario ISO 8601).
    """
    return date.fromisocalendar(iso_year, iso_week, 4)


def current_iso_today() -> tuple[int, int]:
    """ ``(iso_year, iso_week)`` del lunes en curso. """
    today = datetime.now(timezone.utc).date()
    iy, iw, _ = today.isocalendar()
    return iy, iw


def last_complete_iso_week(now_year: int, now_week: int) -> tuple[int, int]:
    """
    Última semana ISO **completa** anterior al momento actual.

    Si la semana actual está en curso (no terminó el domingo), la última completa es la previa.
    """
    if now_week == 1:
        prev = date(now_year - 1, 12, 28)
        py, pw, _ = prev.isocalendar()
        return py, pw
    return now_year, now_week - 1


def last_complete_month(now_year: int, now_month: int) -> tuple[int, int]:
    if now_month == 1:
        return now_year - 1, 12
    return now_year, now_month - 1


def clamp_last_complete_week_to_data(
    lc_y: int,
    lc_w: int,
    grouped: dict[str, list[dict]],
    current_year: int,
) -> tuple[int, int]:
    """No publicar semana/mes «actual» más allá del último GeoTIFF semanal disponible."""
    max_w = 0
    for recs in grouped.values():
        for r in recs:
            if r["year"] == current_year:
                max_w = max(max_w, int(r["week"]))
    if max_w <= 0:
        return lc_y, lc_w
    if lc_y > current_year or (lc_y == current_year and lc_w > max_w):
        return current_year, max_w
    return lc_y, lc_w


def clamp_last_complete_month_to_data(
    lm_y: int,
    lm_m: int,
    grouped: dict[str, list[dict]],
    current_year: int,
) -> tuple[int, int]:
    max_m = 0
    for recs in grouped.values():
        for r in recs:
            if r["year"] == current_year:
                max_m = max(max_m, int(r["thursday"].month))
    if max_m <= 0:
        return lm_y, lm_m
    if lm_y > current_year or (lm_y == current_year and lm_m > max_m):
        return current_year, max_m
    return lm_y, lm_m


# ---------------------------------------------------------------------------
# Discovery & stack loading
# ---------------------------------------------------------------------------

def discover_tifs(tif_dir: Path, s2_to_wetland: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """
    Recorre ``tif_dir`` y agrupa por wetland_id (clave en config.yaml).

    Los archivos ``S2_G5_…`` se renombran lógicamente a ``l_martinez`` si ``s2_to_wetland`` lo indica.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(tif_dir.glob("S2_*.tif")):
        m = _STEM_RE.match(path.stem)
        if not m:
            continue
        file_predio = m.group("predio").upper()
        wetland_key = file_predio.lower()
        if s2_to_wetland and file_predio in s2_to_wetland:
            wetland_key = s2_to_wetland[file_predio]
        try:
            y = int(m.group("year"))
            w = int(m.group("week"))
            th = iso_week_thursday(y, w)
        except ValueError:
            continue
        grouped[wetland_key].append({"path": path, "year": y, "week": w, "thursday": th})
    for predio in grouped:
        grouped[predio].sort(key=lambda r: r["thursday"])
    return dict(grouped)


def read_stack(predio_records: list[dict]) -> tuple[np.ndarray, list[str], dict]:
    """
    Lee todos los TIFs del predio. Devuelve:

    - ``stack`` float32 con shape ``(n_dates, n_bands, H, W)`` (NaN para nodata).
    - ``band_names`` con los nombres de banda (siempre uppercase).
    - ``geo``: ``{"crs", "bounds_4326": (w,s,e,n), "leaflet_bounds": [[s,w],[n,e]], "H", "W"}``.
    """
    if not predio_records:
        raise ValueError("Predio sin TIFs.")

    with rasterio.open(predio_records[0]["path"]) as ds0:
        ref_w, ref_h = ds0.width, ds0.height
        ref_crs = ds0.crs
        ref_transform = ds0.transform
        band_descs = list(ds0.descriptions)
        if not band_descs or not any(band_descs):
            band_descs = [f"band_{i+1}" for i in range(ds0.count)]
        band_names = [(b or f"band_{i+1}").strip().upper() for i, b in enumerate(band_descs)]
        n_bands = ds0.count
        if ref_crs and ref_crs.to_epsg() != 4326:
            wgs = transform_bounds(ref_crs, "EPSG:4326", *ds0.bounds)
        else:
            b = ds0.bounds
            wgs = (b.left, b.bottom, b.right, b.top)

    n_dates = len(predio_records)
    stack = np.full((n_dates, n_bands, ref_h, ref_w), np.nan, dtype=np.float32)

    # Divisor por banda (resuelto desde band_names para no recalcular en cada iteración).
    div_per_band = np.array(
        [BAND_DIVISOR_OVERRIDE.get(b, DIVISOR_DEFAULT) for b in band_names],
        dtype=np.float32,
    ).reshape(-1, 1, 1)

    for i, rec in enumerate(predio_records):
        with rasterio.open(rec["path"]) as ds:
            if ds.width != ref_w or ds.height != ref_h:
                print(
                    f"  [aviso] {rec['path'].name}: tamaño {ds.width}x{ds.height} ≠ "
                    f"referencia {ref_w}x{ref_h}; remuestreo a grilla de referencia.",
                    file=sys.stderr,
                )
                arr = np.full((n_bands, ref_h, ref_w), np.nan, dtype=np.float32)
                nb = min(n_bands, ds.count)
                for bi in range(nb):
                    src = ds.read(bi + 1).astype(np.float32, copy=False)
                    nd = ds.nodata
                    if nd is not None and np.isfinite(float(nd)):
                        src = np.where(np.isclose(src, float(nd)), np.nan, src)
                    for sentinel in SENTINEL_VALUES:
                        src = np.where(src == sentinel, np.nan, src)
                    reproject(
                        source=src,
                        destination=arr[bi],
                        src_transform=ds.transform,
                        src_crs=ds.crs,
                        dst_transform=ref_transform,
                        dst_crs=ref_crs,
                        resampling=Resampling.bilinear,
                        src_nodata=np.nan,
                        dst_nodata=np.nan,
                    )
            else:
                arr = ds.read().astype(np.float32, copy=False)
                nodata = ds.nodata
                if nodata is not None:
                    arr = np.where(arr == nodata, np.nan, arr)
                for sentinel in SENTINEL_VALUES:
                    arr = np.where(arr == sentinel, np.nan, arr)
                arr = np.where(np.isfinite(arr), arr, np.nan)
        if arr.shape[0] != n_bands:
            arr = arr[:n_bands]
        arr = arr / div_per_band[: arr.shape[0]]
        stack[i, : arr.shape[0]] = arr

    geo = {
        "crs": str(ref_crs) if ref_crs else None,
        "bounds_4326": wgs,
        "leaflet_bounds": [[wgs[1], wgs[0]], [wgs[3], wgs[2]]],
        "H": ref_h,
        "W": ref_w,
        "transform": ref_transform,
    }
    return stack, band_names, geo


def load_predio_geometries(aoi_geojson: Path | None) -> dict[str, dict]:
    """Geometrías AOI por ``wetland_id`` (GeoJSON geometry en EPSG:4326)."""
    out: dict[str, dict] = {}
    if not aoi_geojson or not aoi_geojson.is_file():
        return out
    try:
        fc = json.loads(aoi_geojson.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for feat in fc.get("features", []):
        props = feat.get("properties") or {}
        wid = (props.get("wetland_id") or props.get("predio_id") or "").strip().lower()
        geom = feat.get("geometry")
        if wid and isinstance(geom, dict) and geom.get("type"):
            out[wid] = geom
    return out


def mask_stack_to_geometry(
    stack: np.ndarray,
    geometry_wgs84: dict | None,
    transform,
    crs,
) -> np.ndarray:
    """Pone NaN fuera del polígono del predio (recorte real, no solo bbox)."""
    if geometry_wgs84 is None or transform is None:
        return stack
    h, w = stack.shape[2], stack.shape[3]
    geom = geometry_wgs84
    crs_str = str(crs) if crs else "EPSG:4326"
    if crs_str.upper() not in ("EPSG:4326", "OGC:CRS84", "CRS84"):
        try:
            geom = transform_geom("EPSG:4326", crs, geometry_wgs84)
        except Exception:
            return stack
    try:
        outside = geometry_mask(
            [geom],
            out_shape=(h, w),
            transform=transform,
            invert=False,
        )
    except (ValueError, TypeError):
        return stack
    if not np.any(outside):
        return stack
    if outside.all():
        # El polígono no cubre ningún píxel del ráster (deriva de georreferencia entre el
        # recorte exportado y el predio actual). Como cada TIFF Sentinel-2 ya es un recorte
        # por predio, se usa el ráster completo en lugar de descartar todos los datos.
        return stack
    for i in range(stack.shape[0]):
        for b in range(stack.shape[1]):
            stack[i, b, outside] = np.nan
    return stack


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _nan_median(stack_slice: np.ndarray) -> np.ndarray:
    """Mediana por píxel (n_dates) ignorando NaN; vacío → NaN."""
    if stack_slice.shape[0] == 0:
        # (n_bands, H, W)
        return np.full(stack_slice.shape[1:], np.nan, dtype=np.float32)
    with np.errstate(invalid="ignore"):
        return np.nanmedian(stack_slice, axis=0).astype(np.float32, copy=False)


def _nan_percentile(stack_slice: np.ndarray, q: float) -> np.ndarray:
    if stack_slice.shape[0] == 0:
        return np.full(stack_slice.shape[1:], np.nan, dtype=np.float32)
    with np.errstate(invalid="ignore"):
        return np.nanpercentile(stack_slice, q, axis=0).astype(np.float32, copy=False)


def _spatial_mean(raster_3d: np.ndarray) -> np.ndarray:
    """Para un cubo ``(n_bands, H, W)``, retorna ``(n_bands,)`` con la media espacial NaN-aware."""
    flat = raster_3d.reshape(raster_3d.shape[0], -1)
    with np.errstate(invalid="ignore"):
        return np.nanmean(flat, axis=1).astype(np.float32, copy=False)


def _is_map_composite_key(comp_key: str) -> bool:
    """Composites usados en el mapa comparativo (semanal/mensual)."""
    return comp_key.startswith("weekly_") or comp_key.startswith("monthly_")


# Bandas sin escala conjunta semanal/mensual (metadatos o sin comparación visual).
BAND_STATS_SKIP: set[str] = set(SKIP_BANDS) | {"CLEAR_PIXEL_COUNT"}


def _accumulate_map_composite_stats(
    band_stats: dict[str, dict[str, float]],
    aggregates: dict[str, dict],
    band_names: list[str],
    band_idx_filter: list[int],
) -> None:
    """Acumula min/max por banda sobre todos los rasters semanales y mensuales."""
    for comp_key, agg in aggregates.items():
        if not _is_map_composite_key(comp_key):
            continue
        raster_3d = agg["raster"]
        for b_idx in band_idx_filter:
            if b_idx >= raster_3d.shape[0]:
                continue
            band = band_names[b_idx]
            if band in BAND_STATS_SKIP:
                continue
            arr = raster_3d[b_idx]
            valid = arr[np.isfinite(arr)]
            if valid.size == 0:
                continue
            lo = float(np.nanmin(valid))
            hi = float(np.nanmax(valid))
            slot = band_stats.setdefault(band, {"min": float("inf"), "max": float("-inf")})
            slot["min"] = min(slot["min"], lo)
            slot["max"] = max(slot["max"], hi)


def _round_viz_limit(value: float) -> float:
    av = abs(value)
    if av >= 100:
        return round(value, 1)
    if av >= 10:
        return round(value, 2)
    if av >= 1:
        return round(value, 3)
    return round(value, 4)


def _resolve_band_viz_ranges(
    band_stats: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """``vmin``/``vmax`` compartidos por índice (histórico semanal + mensual)."""
    out: dict[str, dict[str, float]] = {}
    for band, stats in band_stats.items():
        lo, hi = stats.get("min"), stats.get("max")
        base = BAND_VIZ.get(band, DEFAULT_VIZ)
        if lo is None or hi is None or not np.isfinite(lo) or not np.isfinite(hi) or lo >= hi:
            out[band] = {"vmin": float(base["vmin"]), "vmax": float(base["vmax"])}
        else:
            lo_adj, hi_adj = float(lo), float(hi)
            if band == "REDEDGE_POSITION":
                # Valores físicos ~700–740 nm; ignorar basura de exportaciones antiguas (sentinelas sin filtrar).
                if lo_adj < 680.0 or hi_adj > 760.0 or lo_adj >= hi_adj:
                    out[band] = {"vmin": float(base["vmin"]), "vmax": float(base["vmax"])}
                else:
                    out[band] = {
                        "vmin": _round_viz_limit(lo_adj),
                        "vmax": _round_viz_limit(hi_adj),
                    }
            else:
                out[band] = {
                    "vmin": _round_viz_limit(lo_adj),
                    "vmax": _round_viz_limit(hi_adj),
                }
    return out


def _band_viz_ranges_changed(
    new_ranges: dict[str, dict[str, float]],
    existing_meta: dict,
) -> bool:
    prev = (existing_meta or {}).get("indices") or {}
    for band, lim in new_ranges.items():
        old = prev.get(band) or {}
        if old.get("vmin") is None or old.get("vmax") is None:
            return True
        if abs(float(old["vmin"]) - lim["vmin"]) > 1e-6:
            return True
        if abs(float(old["vmax"]) - lim["vmax"]) > 1e-6:
            return True
    return False


def compute_aggregates(
    stack: np.ndarray,
    records: list[dict],
    current_year: int,
) -> dict[str, dict]:
    """
    Calcula todos los rasters agregados pedidos. Retorna::

        {
          composite_key: {
            "raster": np.ndarray shape (n_bands, H, W),
            "view_mode": "monthly"|"weekly",
            "role": "left"|"right",
            "year": int|None,
            "month": int|None,
            "week": int|None,
            "label": str,
            "n_inputs": int,
          },
          ...
        }

    Notas:
      - Historial NO incluye al año actual (separación limpia para comparar).
      - ``weekly_hist_W<WW>``: solo mediana como raster (los percentiles van al chart).
    """
    # Indexar registros por mes y semana.
    idx_month_hist: dict[int, np.ndarray] = {
        m: np.array(
            [(r["year"] < current_year) and (r["thursday"].month == m) for r in records],
            dtype=bool,
        )
        for m in range(1, 13)
    }
    idx_month_current: dict[int, np.ndarray] = {
        m: np.array(
            [(r["year"] == current_year) and (r["thursday"].month == m) for r in records],
            dtype=bool,
        )
        for m in range(1, 13)
    }
    idx_week_hist: dict[int, np.ndarray] = {
        w: np.array(
            [(r["year"] < current_year) and (r["week"] == w) for r in records],
            dtype=bool,
        )
        for w in range(1, 54)
    }
    idx_week_current: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if r["year"] == current_year:
            idx_week_current[r["week"]].append(i)

    out: dict[str, dict] = {}

    # ---- Monthly ----
    for m in range(1, 13):
        sel = stack[idx_month_hist[m]]
        if sel.shape[0] > 0:
            out[f"monthly_hist_{m:02d}"] = {
                "raster": _nan_median(sel),
                "view_mode": "monthly",
                "role": "left",
                "year": None,
                "month": m,
                "week": None,
                "label": f"Hist. {MONTH_NAMES_ES[m-1]}",
                "n_inputs": int(sel.shape[0]),
            }
        sel_cur = stack[idx_month_current[m]]
        if sel_cur.shape[0] > 0:
            out[f"monthly_current_{m:02d}"] = {
                "raster": _nan_median(sel_cur),
                "view_mode": "monthly",
                "role": "right",
                "year": current_year,
                "month": m,
                "week": None,
                "label": f"{MONTH_NAMES_ES[m-1]} {current_year}",
                "n_inputs": int(sel_cur.shape[0]),
            }

    # ---- Weekly ----
    for w in range(1, 54):
        sel = stack[idx_week_hist[w]]
        if sel.shape[0] > 0:
            out[f"weekly_hist_W{w:02d}"] = {
                "raster": _nan_median(sel),
                "view_mode": "weekly",
                "role": "left",
                "year": None,
                "month": None,
                "week": w,
                "label": f"Hist. semana {w:02d}",
                "n_inputs": int(sel.shape[0]),
            }
        cur_indices = idx_week_current.get(w, [])
        if cur_indices:
            cur_stack = stack[cur_indices]
            out[f"weekly_current_W{w:02d}"] = {
                "raster": _nan_median(cur_stack) if len(cur_indices) > 1 else cur_stack[0],
                "view_mode": "weekly",
                "role": "right",
                "year": current_year,
                "month": None,
                "week": w,
                "label": f"Semana {w:02d} · {current_year}",
                "n_inputs": int(cur_stack.shape[0]),
            }

    return out


def compute_weekly_timeseries(
    stack: np.ndarray,
    records: list[dict],
    band_names: list[str],
    current_year: int,
) -> dict[str, dict]:
    """
    Para cada banda devuelve la serie semanal (1..52).

    **Hist\u00f3rico** (por semana ISO ``w``):

    1. Para cada a\u00f1o hist\u00f3rico con imagen en ``w``, se calcula la **media espacial**
       de la banda (un escalar por a\u00f1o).
    2. Sobre ese vector de medias anuales (t\u00edpicamente 2–3 valores) se calculan
       mediana, percentil 25 y 75. As\u00ed el rango P25–P75 refleja **variabilidad
       interanual** al nivel del predio; con pocos a\u00f1os sigue siendo interpretable
       (antes, percentiles temporales *por p\u00edxel* y luego media espacial hac\u00eda
       que P25=P75 en muchas semanas si en cada p\u00edxel solo hab\u00eda 1 a\u00f1o v\u00e1lido).

    **A\u00f1o actual**: media espacial del raster de esa semana (``null`` si la composici\u00f3n
    sali\u00f3 toda sin datos v\u00e1lidos para esa banda, p. ej. nubes / sin cobertura).

    Estructura::

        {
          "<BAND>": {
            "weeks": [1..52],
            "historical_median": [...],
            "historical_p25": [...],
            "historical_p75": [...],
            "current_year": {"year": YYYY, "values_by_week": [...]},
            "years_used": [YYYY, ...],
          },
          ...
        }
    """
    n_bands = stack.shape[1]
    by_band: dict[str, dict] = {}

    historic_years = sorted({r["year"] for r in records if r["year"] < current_year})

    # Pre-armar índices.
    week_to_hist_idx: dict[int, list[int]] = defaultdict(list)
    week_to_curr_idx: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if r["year"] < current_year:
            week_to_hist_idx[r["week"]].append(i)
        elif r["year"] == current_year:
            week_to_curr_idx[r["week"]].append(i)

    for b_idx, band in enumerate(band_names):
        if band in SKIP_BANDS or not is_published_band(band):
            continue
        weeks = list(range(1, 53))
        hist_med = [math.nan] * 52
        hist_p25 = [math.nan] * 52
        hist_p75 = [math.nan] * 52
        cur_vals = [math.nan] * 52

        for w in weeks:
            hist_idx = week_to_hist_idx.get(w, [])
            if hist_idx:
                yearly_means: list[float] = []
                for i in hist_idx:
                    arr = stack[i, b_idx]
                    with np.errstate(invalid="ignore"):
                        m = float(np.nanmean(arr))
                    if math.isfinite(m):
                        yearly_means.append(m)
                if yearly_means:
                    v = np.asarray(yearly_means, dtype=np.float64)
                    hist_med[w - 1] = float(np.median(v))
                    if v.size >= 2:
                        hist_p25[w - 1] = float(np.percentile(v, 25))
                        hist_p75[w - 1] = float(np.percentile(v, 75))
                    else:
                        # Un solo a\u00f1o hist\u00f3rico: no hay dispersi\u00f3n interanual
                        hist_p25[w - 1] = hist_p75[w - 1] = hist_med[w - 1]

            curr_idx = week_to_curr_idx.get(w, [])
            if curr_idx:
                slab = stack[curr_idx, b_idx]
                with np.errstate(invalid="ignore"):
                    if slab.shape[0] > 1:
                        spatial = np.nanmean(np.nanmedian(slab, axis=0))
                    else:
                        spatial = np.nanmean(slab[0])
                cur_vals[w - 1] = float(spatial) if np.isfinite(spatial) else math.nan

        by_band[band] = {
            "weeks": weeks,
            "historical_median": _sanitize_numbers(hist_med),
            "historical_p25": _sanitize_numbers(hist_p25),
            "historical_p75": _sanitize_numbers(hist_p75),
            "current_year": {
                "year": current_year,
                "values_by_week": _sanitize_numbers(cur_vals),
            },
            "years_used": historic_years,
        }

    return by_band


def _sanitize_numbers(seq: Iterable[float]) -> list[float | None]:
    """JSON-safe: NaN/Inf → None; floats → ``round(value, 4)``."""
    out: list[float | None] = []
    for v in seq:
        if v is None:
            out.append(None)
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            out.append(None)
            continue
        if not math.isfinite(f):
            out.append(None)
        else:
            out.append(round(f, 4))
    return out


def _calendar_month_complete_for_current_year(
    current_year: int, month: int, lc_y: int, lc_m: int
) -> bool:
    """Mes calendario del año actual ya cerrado respecto al último mes completo global."""
    if current_year < lc_y:
        return True
    if current_year > lc_y:
        return False
    return month <= lc_m


def augment_timeseries_with_monthly(
    ts_by_band: dict[str, dict],
    stack: np.ndarray,
    records: list[dict],
    band_names: list[str],
    current_year: int,
    lc_y: int,
    lc_m: int,
) -> None:
    month_to_hist_idx: dict[int, list[int]] = defaultdict(list)
    month_to_curr_idx: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        m = int(r["thursday"].month)
        if r["year"] < current_year:
            month_to_hist_idx[m].append(i)
        elif r["year"] == current_year:
            month_to_curr_idx[m].append(i)

    for b_idx, band in enumerate(band_names):
        if band in SKIP_BANDS or not is_published_band(band):
            continue
        band_entry = ts_by_band.get(band)
        if not band_entry:
            continue
        months = list(range(1, 13))
        hist_med = [math.nan] * 12
        hist_p25 = [math.nan] * 12
        hist_p75 = [math.nan] * 12
        cur_month = [math.nan] * 12

        for m in months:
            hist_idx = month_to_hist_idx.get(m, [])
            if hist_idx:
                yearly_means: list[float] = []
                for i in hist_idx:
                    arr = stack[i, b_idx]
                    with np.errstate(invalid="ignore"):
                        mm = float(np.nanmean(arr))
                    if math.isfinite(mm):
                        yearly_means.append(mm)
                if yearly_means:
                    v = np.asarray(yearly_means, dtype=np.float64)
                    hist_med[m - 1] = float(np.median(v))
                    if v.size >= 2:
                        hist_p25[m - 1] = float(np.percentile(v, 25))
                        hist_p75[m - 1] = float(np.percentile(v, 75))
                    else:
                        hist_p25[m - 1] = hist_p75[m - 1] = hist_med[m - 1]

            if _calendar_month_complete_for_current_year(current_year, m, lc_y, lc_m):
                curr_idx = month_to_curr_idx.get(m, [])
                if curr_idx:
                    slab = stack[curr_idx, b_idx]
                    with np.errstate(invalid="ignore"):
                        if slab.shape[0] > 1:
                            spatial = np.nanmean(np.nanmedian(slab, axis=0))
                        else:
                            spatial = np.nanmean(slab[0])
                    cur_month[m - 1] = float(spatial) if np.isfinite(spatial) else math.nan

        band_entry["months"] = months
        band_entry["historical_median_by_month"] = _sanitize_numbers(hist_med)
        band_entry["historical_p25_by_month"] = _sanitize_numbers(hist_p25)
        band_entry["historical_p75_by_month"] = _sanitize_numbers(hist_p75)
        cy = band_entry["current_year"]
        cy["values_by_month"] = _sanitize_numbers(cur_month)


# ---------------------------------------------------------------------------
# WebP rendering
# ---------------------------------------------------------------------------

class ColormapCache:
    """Cache de LUTs (256 colores) por nombre de matplotlib colormap."""

    def __init__(self) -> None:
        self._luts: dict[str, np.ndarray] = {}

    def get(self, name: str) -> np.ndarray:
        if name not in self._luts:
            try:
                cmap = _MPL_COLORMAPS[name]
            except KeyError:
                cmap = _MPL_COLORMAPS["RdYlGn"]
            lut = (cmap(np.linspace(0.0, 1.0, 256)) * 255).astype(np.uint8)
            self._luts[name] = lut
        return self._luts[name]


CMAP_CACHE = ColormapCache()


def render_band_to_webp(
    data: np.ndarray,
    out_path: Path,
    *,
    vmin: float,
    vmax: float,
    colormap: str,
    upscale_min_side: int = WEBP_UPSCALE_MIN_SIDE,
    quality: int = WEBP_QUALITY,
    method: int = WEBP_METHOD,
) -> tuple[int, int]:
    """
    Toma un array (H, W) en valores físicos y guarda un WebP coloreado.
    NaN → transparente. Retorna (w, h) del WebP guardado.
    """
    arr = data.astype(np.float32, copy=False)
    mask = ~np.isfinite(arr)
    span = max(float(vmax) - float(vmin), 1e-9)
    norm = np.clip((arr - vmin) / span, 0.0, 1.0)
    norm = np.where(mask, 0.0, norm)
    idx = (norm * 255.0).astype(np.uint8)

    lut = CMAP_CACHE.get(colormap)
    rgba = lut[idx].copy()  # (H, W, 4)
    rgba[mask, 3] = 0

    h, w = rgba.shape[:2]
    if min(h, w) < upscale_min_side:
        scale = upscale_min_side / max(1, min(h, w))
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
    else:
        new_w, new_h = w, h

    img = PILImage.fromarray(rgba, mode="RGBA")
    if (new_w, new_h) != (w, h):
        img = img.resize((new_w, new_h), PILImage.NEAREST)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="WEBP", quality=quality, method=method)
    return new_w, new_h


# ---------------------------------------------------------------------------
# AOI / DB helpers
# ---------------------------------------------------------------------------

def load_predios_info(aoi_geojson: Path | None, db_csv: Path | None) -> dict[str, dict]:
    info: dict[str, dict] = {}
    if aoi_geojson and aoi_geojson.is_file():
        try:
            fc = json.loads(aoi_geojson.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fc = {"features": []}
        for feat in fc.get("features", []):
            props = feat.get("properties") or {}
            wid = (props.get("wetland_id") or props.get("predio_id") or "").strip().lower()
            if not wid:
                continue
            geom = feat.get("geometry") or {}
            ring: list[list[float]] = []
            coords = geom.get("coordinates", [])
            if geom.get("type") == "Polygon" and coords:
                ring = coords[0]
            elif geom.get("type") == "MultiPolygon" and coords and coords[0]:
                ring = coords[0][0]
            if ring:
                lons = [c[0] for c in ring]
                lats = [c[1] for c in ring]
                center = [sum(lats) / len(lats), sum(lons) / len(lons)]
            else:
                center = [0.0, 0.0]
            info[wid] = {
                "name": props.get("nombre") or wid.upper(),
                "area_ha": props.get("area_ha"),
                "center": center,
                "codigo_predio": wid.upper(),
            }
    if db_csv and db_csv.is_file():
        try:
            cfg = load_config()
            poligono_wetlands: dict[str, list[str]] = defaultdict(list)
            for wid, wcfg in cfg.get("wetlands", {}).items():
                pv = str(wcfg.get("aoi_filter_val") or "").strip().upper()
                if pv:
                    poligono_wetlands[pv].append(wid)
            with open(db_csv, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pv = str(row.get("poligono_vuelo") or "").strip().upper()
                    prop = str(row.get("propietario") or "").strip()
                    asesor = str(row.get("asesor") or "").strip()
                    nom_predio = str(row.get("nom_predio") or "").strip()
                    for wid in poligono_wetlands.get(pv, []):
                        wcfg = cfg["wetlands"].get(wid, {})
                        if wid not in info:
                            info[wid] = {
                                "name": wcfg.get("name") or nom_predio or wid,
                                "codigo_predio": str(wcfg.get("drone_code") or wid).upper(),
                            }
                        info[wid].update(
                            {
                                k: v
                                for k, v in row.items()
                                if v not in (None, "")
                            }
                        )
                        info[wid]["nombre_agricultor"] = prop
                        info[wid]["nombre_sat_asesor"] = asesor
            # Esquema legacy
            with open(db_csv, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames and "codigo_predio" in reader.fieldnames:
                    for row in reader:
                        codigo = (row.get("codigo_predio") or "").strip()
                        if not codigo:
                            continue
                        key = codigo.lower()
                        if key in info:
                            info[key].update({k: v for k, v in row.items() if v not in (None, "")})
                            info[key]["codigo_predio"] = codigo
        except OSError:
            pass
    return info


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def write_predio_csv(
    out_dir: Path,
    predio: str,
    timeseries: dict[str, dict],
    *,
    current_year: int,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{predio.lower()}_timeseries.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "predio", "band", "iso_week",
            "historical_median", "historical_p25", "historical_p75",
            f"value_{current_year}",
        ])
        for band, info in timeseries.items():
            weeks = info["weeks"]
            for i, w in enumerate(weeks):
                writer.writerow([
                    predio.upper(),
                    band,
                    w,
                    _csv_num(info["historical_median"][i]),
                    _csv_num(info["historical_p25"][i]),
                    _csv_num(info["historical_p75"][i]),
                    _csv_num(info["current_year"]["values_by_week"][i]),
                ])
    return path


def _csv_num(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v:.4f}"


def write_predio_monthly_csv(
    out_dir: Path,
    predio: str,
    timeseries: dict[str, dict],
    *,
    current_year: int,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{predio.lower()}_timeseries_monthly.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "predio", "band", "month",
            "historical_median", "historical_p25", "historical_p75",
            f"value_{current_year}",
        ])
        for band, info in timeseries.items():
            if band in SKIP_BANDS or not is_published_band(band):
                continue
            months = info.get("months") or list(range(1, 13))
            hm = info.get("historical_median_by_month") or [None] * 12
            hp25 = info.get("historical_p25_by_month") or [None] * 12
            hp75 = info.get("historical_p75_by_month") or [None] * 12
            curm = (info.get("current_year") or {}).get("values_by_month") or [None] * 12
            for i, m in enumerate(months):
                writer.writerow([
                    predio.upper(),
                    band,
                    m,
                    _csv_num(hm[i] if i < len(hm) else None),
                    _csv_num(hp25[i] if i < len(hp25) else None),
                    _csv_num(hp75[i] if i < len(hp75) else None),
                    _csv_num(curm[i] if i < len(curm) else None),
                ])
    return path


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _timeseries_predio_has_monthly(ts_predio: dict | None) -> bool:
    if not ts_predio:
        return False
    for info in ts_predio.values():
        if isinstance(info, dict) and info.get("historical_median_by_month"):
            return True
    return False


def _predio_build_fingerprint(
    records: list[dict],
    current_year: int,
    lm_year: int,
    lm_month: int,
    lc_week_y: int,
    lc_week_w: int,
    *,
    aoi_mtime_ns: int = 0,
) -> str:
    """Huella estable por predio: TIFs + año actual + último mes/semana completo (afecta series)."""
    max_mtime_ns = 0
    for r in records:
        try:
            max_mtime_ns = max(max_mtime_ns, int(r["path"].stat().st_mtime_ns))
        except OSError:
            pass
    first = records[0]["thursday"].isoformat() if records else ""
    last = records[-1]["thursday"].isoformat() if records else ""
    payload = {
        "current_year": current_year,
        "last_complete_month": [lm_year, lm_month],
        "last_complete_week": [lc_week_y, lc_week_w],
        "n_tifs": len(records),
        "max_tif_mtime_ns": max_mtime_ns,
        "thursday_first": first,
        "thursday_last": last,
        "aoi_mtime_ns": aoi_mtime_ns,
        "geom_mask_v": 1,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _predio_fp_path(csv_dir: Path, predio_lower: str) -> Path:
    return csv_dir / f".{predio_lower}_build_fp.txt"


# ---------------------------------------------------------------------------
# Build orchestrator
# ---------------------------------------------------------------------------

def build(
    tif_dir: Path,
    static_dir: Path,
    aoi_geojson: Path | None,
    db_csv: Path | None,
    *,
    current_year: int | None = None,
    bands_filter: list[str] | None = None,
    force: bool = False,
    incremental: bool = True,
    upscale_min_side: int = WEBP_UPSCALE_MIN_SIDE,
    webp_quality: int = WEBP_QUALITY,
) -> None:
    """
    Pipeline completo: lee TIFs → calcula agregados/series → escribe rasters y JSONs.

    Con ``incremental=True`` (default), omite un predio si los GeoTIFF no cambiaron
    (mtime / rango de fechas) y ya existen los CSV de series con bloque mensual en
    ``timeseries.json``; o solo escribe el CSV mensual si falta ese archivo pero el
    resto coincide.
    """
    rasters_dir = static_dir / "rasters"
    csv_dir = static_dir / "csv"
    rasters_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    s2_map = build_s2_file_to_predio_map(load_config())
    grouped = discover_tifs(tif_dir, s2_map)
    if not grouped:
        print(f"No se encontraron TIFs en {tif_dir}. Nada que hacer.", file=sys.stderr)
        return

    all_years: set[int] = set()
    for recs in grouped.values():
        all_years.update(r["year"] for r in recs)
    if current_year is None:
        cy_today, _ = current_iso_today()
        current_year = max(all_years) if all_years else cy_today
    historic_years = sorted({y for y in all_years if y < current_year})
    print(f"Predios: {sorted(grouped.keys())}")
    print(f"Años detectados: {sorted(all_years)} | actual={current_year} | hist={historic_years}")

    predios_info = load_predios_info(aoi_geojson, db_csv)
    predio_geoms = load_predio_clip_geometries(load_config())
    aoi_mtime_ns = 0
    if aoi_geojson and aoi_geojson.is_file():
        try:
            aoi_mtime_ns = int(aoi_geojson.stat().st_mtime_ns)
        except OSError:
            pass
    rasters_meta: dict[str, dict] = {}
    wetlands_meta: dict[str, dict] = {}
    timeseries_all: dict[str, dict] = {}
    band_set: set[str] = set()

    iy_meta, iw_meta = current_iso_today()
    lc_week_y, lc_week_w = last_complete_iso_week(iy_meta, iw_meta)
    lm_year, lm_month = last_complete_month(iy_meta, datetime.now(timezone.utc).month)
    lc_week_y, lc_week_w = clamp_last_complete_week_to_data(
        lc_week_y, lc_week_w, grouped, current_year
    )
    lm_year, lm_month = clamp_last_complete_month_to_data(
        lm_year, lm_month, grouped, current_year
    )

    existing_ts_doc: dict | None = _load_json(static_dir / "timeseries.json") if incremental else None
    existing_wetlands: dict = (existing_ts_doc or {}).get("wetlands") or {}
    existing_meta: dict = _load_json(static_dir / "metadata.json") or {} if incremental else {}

    band_stats: dict[str, dict[str, float]] = {}
    predio_render_jobs: list[dict] = []

    for predio, records in grouped.items():
        print(f"\n=== {predio} ({len(records)} TIFs) ===")
        pl = predio.lower()
        fp = _predio_build_fingerprint(
            records, current_year, lm_year, lm_month, lc_week_y, lc_week_w,
            aoi_mtime_ns=aoi_mtime_ns,
        )
        fp_path = _predio_fp_path(csv_dir, pl)
        mcsv = csv_dir / f"{pl}_timeseries_monthly.csv"
        wcsv = csv_dir / f"{pl}_timeseries.csv"
        skip_render = False

        try:
            stack, band_names, geo = read_stack(records)
        except (rasterio.RasterioIOError, ValueError) as exc:
            print(f"  [error] no se pudo leer stack: {exc}", file=sys.stderr)
            continue
        pl_geom = predio_geoms.get(pl)
        if pl_geom is not None:
            stack = mask_stack_to_geometry(
                stack, pl_geom, geo.get("transform"), geo.get("crs")
            )
            print(f"  Máscara AOI aplicada ({pl.upper()}).")
        band_set.update(b for b in band_names if is_published_band(b))

        if bands_filter:
            allowed_upper = {b.strip().upper() for b in bands_filter}
            band_idx_filter = [
                i for i, b in enumerate(band_names)
                if b in allowed_upper and is_published_band(b)
            ]
        else:
            band_idx_filter = [
                i for i, b in enumerate(band_names) if is_published_band(b)
            ]
        if not band_idx_filter:
            print(f"  [aviso] sin bandas válidas; salto {predio}.")
            continue

        aggregates = compute_aggregates(stack, records, current_year)
        _accumulate_map_composite_stats(band_stats, aggregates, band_names, band_idx_filter)

        if incremental and not force:
            prev_fp = fp_path.read_text(encoding="utf-8").strip() if fp_path.is_file() else None
            fp_ok = prev_fp == fp
            ts_existing = existing_wetlands.get(pl)
            has_monthly = _timeseries_predio_has_monthly(ts_existing)
            wl_meta = (existing_meta.get("wetlands") or {}).get(pl) if isinstance(existing_meta, dict) else None

            def _merge_skipped_predio() -> None:
                assert ts_existing is not None
                timeseries_all[pl] = {
                    b: copy.deepcopy(v)
                    for b, v in ts_existing.items()
                    if is_published_band(b)
                }
                if wl_meta:
                    wetlands_meta[pl] = copy.deepcopy(wl_meta)
                for rk, rv in (existing_meta.get("rasters") or {}).items():
                    if not rk.startswith(f"{pl}_"):
                        continue
                    if is_published_band(_raster_ref_band(rk)):
                        rasters_meta[rk] = copy.deepcopy(rv)
                for b in timeseries_all[pl]:
                    band_set.add(b)

            if fp_ok and ts_existing and wcsv.is_file() and has_monthly and mcsv.is_file() and wl_meta:
                _merge_skipped_predio()
                skip_render = True
                print(
                    f"  [incremental] Sin cambios en TIFs ni calendario; se reutilizan series y CSV "
                    f"({wcsv.name}, {mcsv.name})."
                )

            elif fp_ok and ts_existing and wcsv.is_file() and has_monthly and not mcsv.is_file() and wl_meta:
                _merge_skipped_predio()
                write_predio_monthly_csv(csv_dir, predio, timeseries_all[pl], current_year=current_year)
                skip_render = True
                print(
                    f"  [incremental] Solo faltaba {mcsv.name}; generado desde series en caché "
                    f"(sin releer GeoTIFFs)."
                )

        if not skip_render:
            timeseries = compute_weekly_timeseries(stack, records, band_names, current_year)
            augment_timeseries_with_monthly(
                timeseries, stack, records, band_names, current_year, lm_year, lm_month
            )
            timeseries_all[predio.lower()] = timeseries

            wetlands_meta[predio.lower()] = {
                "name": predios_info.get(predio.lower(), {}).get("name") or predio.upper(),
                "codigo_predio": predios_info.get(predio.lower(), {}).get("codigo_predio") or predio.upper(),
                "center": predios_info.get(predio.lower(), {}).get("center", [0.0, 0.0]),
                "area_ha": predios_info.get(predio.lower(), {}).get("area_ha"),
                "available_years": sorted({r["year"] for r in records}),
                "historic_years": historic_years,
                "current_year": current_year,
                "n_weeks_current": sum(1 for r in records if r["year"] == current_year),
                "n_weeks_total": len(records),
                "leaflet_bounds": geo["leaflet_bounds"],
            }

            write_predio_csv(csv_dir, predio, timeseries, current_year=current_year)
            write_predio_monthly_csv(csv_dir, predio, timeseries, current_year=current_year)
            try:
                fp_path.write_text(fp, encoding="utf-8")
            except OSError as exc:
                print(f"  [aviso] no se pudo escribir huella incremental {fp_path}: {exc}", file=sys.stderr)

        predio_render_jobs.append({
            "predio": predio,
            "aggregates": aggregates,
            "band_names": band_names,
            "band_idx_filter": band_idx_filter,
            "skip_render": skip_render,
        })

    band_viz_ranges = _resolve_band_viz_ranges(band_stats)
    ranges_changed = _band_viz_ranges_changed(band_viz_ranges, existing_meta)
    if ranges_changed and incremental and not force:
        print(
            "\n[aviso] Rangos de leyenda (vmin/vmax) actualizados; "
            "se regeneran WebPs con escala conjunta semanal/mensual."
        )
    if band_viz_ranges:
        sample = ", ".join(
            f"{b}=[{lim['vmin']},{lim['vmax']}]"
            for b, lim in sorted(band_viz_ranges.items())[:6]
        )
        print(f"\nEscala conjunta mapa (muestra): {sample}{'…' if len(band_viz_ranges) > 6 else ''}")

    for job in predio_render_jobs:
        if job["skip_render"] and not force and not ranges_changed:
            continue
        predio = job["predio"]
        aggregates = job["aggregates"]
        band_names = job["band_names"]
        band_idx_filter = job["band_idx_filter"]
        n_written = 0
        for comp_key, agg in aggregates.items():
            if not _is_map_composite_key(comp_key):
                continue
            raster_3d = agg["raster"]
            for b_idx in band_idx_filter:
                if b_idx >= raster_3d.shape[0]:
                    continue
                band = band_names[b_idx]
                base_viz = BAND_VIZ.get(band, DEFAULT_VIZ)
                lim = band_viz_ranges.get(
                    band,
                    {"vmin": base_viz["vmin"], "vmax": base_viz["vmax"]},
                )
                stem = f"S2_{predio.upper()}_{comp_key}_{band}"
                webp_path = rasters_dir / f"{stem}.webp"

                if not (webp_path.exists() and not force and not ranges_changed):
                    try:
                        render_band_to_webp(
                            raster_3d[b_idx],
                            webp_path,
                            vmin=lim["vmin"],
                            vmax=lim["vmax"],
                            colormap=base_viz["colormap"],
                            upscale_min_side=upscale_min_side,
                            quality=webp_quality,
                        )
                        n_written += 1
                    except (OSError, ValueError) as exc:
                        print(f"  [error] {stem}: {exc}", file=sys.stderr)
                        continue

                raster_key = f"{predio.lower()}_{comp_key.lower()}_{band.lower()}"
                rasters_meta[raster_key] = {
                    "p": f"sentinel2/rasters/{stem}.webp",
                    "l": agg["label"],
                    "n": agg["n_inputs"],
                }
        print(
            f"  Rasters {predio}: agregados mapa="
            f"{sum(1 for k in aggregates if _is_map_composite_key(k))} "
            f"webp_regenerados={n_written}"
        )

    # Indices catalog para el frontend (misma escala que los WebPs del mapa).
    indices_out: dict[str, dict] = {}
    band_order = [b for b in DEFAULT_CHART_BAND_ORDER if b in band_set and is_published_band(b)]
    band_order.extend(
        sorted(b for b in band_set if b not in band_order and is_published_band(b))
    )
    for band in band_order:
        viz = BAND_VIZ.get(band, DEFAULT_VIZ)
        lim = band_viz_ranges.get(
            band,
            {"vmin": viz["vmin"], "vmax": viz["vmax"]},
        )
        indices_out[band] = {
            "label": viz.get("label") or band,
            "vmin": lim["vmin"],
            "vmax": lim["vmax"],
            "colormap": viz["colormap"],
            "visual_only": False,
            "scale": float(BAND_DIVISOR_OVERRIDE.get(band, DIVISOR_DEFAULT)),
        }

    # Último completo (a partir de records globales).
    metadata = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "aoi_id_column": "predio_id",
        "current_year": current_year,
        "historic_years": historic_years,
        "available_years": sorted(all_years),
        "last_complete_week": {"year": lc_week_y, "week": lc_week_w},
        "last_complete_month": {"year": lm_year, "month": lm_month},
        "source": {
            "id": "sentinel2",
            "label": "Satélite Sentinel-2",
            "description": "Mosaicos semanales Sentinel-2 por predio (agregados locales).",
            "color": "#1d6b4a",
            "has_data": bool(wetlands_meta),
        },
        "raster_defaults": {
            "format": "WEBP",
            "render_mode": "smooth",
            "opacity": 0.88,
        },
        "indices": indices_out,
        "default_chart_band": next((b for b in DEFAULT_CHART_BAND_ORDER if b in band_set), None),
        "view_modes": {
            "monthly": {
                "pill_by": "month",
                "months": list(range(1, 13)),
                "month_labels": MONTH_NAMES_ES,
                "left_label": "Hist. del mes",
                "right_label": f"Mes en {current_year}",
                "left_composite_template": "monthly_hist_{month:02d}",
                "right_composite_template": "monthly_current_{month:02d}",
            },
            "weekly": {
                "pill_by": "week",
                "weeks": list(range(1, 53)),
                "left_label": "Hist. de la semana",
                "right_label": f"Semana en {current_year}",
                "left_composite_template": "weekly_hist_W{week:02d}",
                "right_composite_template": "weekly_current_W{week:02d}",
                "default_week": lc_week_w,
            },
        },
        "predios": wetlands_meta,
        "wetlands": wetlands_meta,
        "rasters": rasters_meta,
        "summary": {
            "n_wetlands": len(wetlands_meta),
            "raster_count": len(rasters_meta),
            "available_years": sorted(all_years),
        },
    }

    meta_path = static_dir / "metadata.json"
    meta_path.write_text(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"\nMetadata escrita: {meta_path.relative_to(REPO_ROOT)} "
          f"({meta_path.stat().st_size / 1024:.1f} KB)")

    timeseries_path = static_dir / "timeseries.json"
    timeseries_payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "current_year": current_year,
        "default_band": metadata["default_chart_band"],
        "predios": timeseries_all,
        "wetlands": timeseries_all,
    }
    timeseries_path.write_text(
        json.dumps(timeseries_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Serie temporal escrita: {timeseries_path.relative_to(REPO_ROOT)} "
          f"({timeseries_path.stat().st_size / 1024:.1f} KB)")

    update_sources_manifest(static_dir.parent, metadata)
    _prune_obsolete_rasters(rasters_dir)
    print(
        f"Listo. Predios: {len(wetlands_meta)} | Rasters WebP: {len(rasters_meta)} | "
        f"Bandas: {len(indices_out)}"
    )


def _raster_ref_band(ref_key: str) -> str:
    upper = ref_key.upper()
    for band in sorted(OPERATIVE_INDEX_BANDS, key=len, reverse=True):
        if upper.endswith("_" + band):
            return band
    return ref_key.rsplit("_", 1)[-1].upper()


def _raster_stem_band(stem: str) -> str:
    upper = stem.upper()
    for band in sorted(OPERATIVE_INDEX_BANDS, key=len, reverse=True):
        if upper.endswith("_" + band):
            return band
    return stem.rsplit("_", 1)[-1].upper()


def _prune_obsolete_rasters(rasters_dir: Path) -> None:
    """Elimina WebPs de bandas descartadas (p. ej. NDWI, SAVI) tras cambiar el catálogo."""
    if not rasters_dir.is_dir():
        return
    removed = 0
    for webp in rasters_dir.glob("*.webp"):
        band = _raster_stem_band(webp.stem)
        if not is_published_band(band):
            try:
                webp.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"  WebPs obsoletos eliminados: {removed}")


def update_sources_manifest(data_static_dir: Path, sentinel_meta: dict) -> None:
    """Actualiza ``data_static/sources_manifest.json`` con el estado de Sentinel-2."""
    manifest_path = data_static_dir / "sources_manifest.json"
    try:
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {}
        )
    except (OSError, json.JSONDecodeError):
        manifest = {}
    manifest.setdefault("sources", {})
    manifest["sources"]["sentinel2"] = {
        "id": "sentinel2",
        "label": sentinel_meta["source"]["label"],
        "description": sentinel_meta["source"]["description"],
        "color": sentinel_meta["source"]["color"],
        "has_data": sentinel_meta["source"]["has_data"],
        "timeseries_path": "sentinel2/timeseries.json",
        "metadata_path": "sentinel2/metadata.json",
        "csv_dir": "sentinel2/csv",
        "status": "ready" if sentinel_meta["source"]["has_data"] else "pending",
        "summary": sentinel_meta["summary"],
    }
    manifest["generated_at"] = sentinel_meta["generated_at"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Manifest actualizado: {manifest_path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline LOCAL: agrega Sentinel-2 semanal a mensual/semanal con WebPs y series.",
    )
    parser.add_argument(
        "--tif-dir",
        type=Path,
        default=DEFAULT_TIF_DIR,
        help=f"Carpeta con TIFs semanales (default: {DEFAULT_TIF_DIR.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=DEFAULT_STATIC_DIR,
        help=f"Salida para data_static/sentinel2 (default: {DEFAULT_STATIC_DIR.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--aoi-geojson",
        type=Path,
        default=DEFAULT_AOI_GEOJSON,
        help="GeoJSON con metadatos por predio (opcional).",
    )
    parser.add_argument(
        "--db-csv",
        type=Path,
        default=DEFAULT_DB_CSV,
        help="CSV con datos auxiliares por predio (opcional).",
    )
    parser.add_argument(
        "--current-year",
        type=int,
        default=None,
        help="Año considerado «actual». Por defecto, el último presente en los TIFs.",
    )
    parser.add_argument(
        "--bands",
        default=None,
        metavar="LIST",
        help="Lista separada por comas para limitar bandas (ej. NDVI,NDMI). "
        "Default: los 9 índices operativos.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerar WebPs aunque ya existan.",
    )
    parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="Reprocesar todos los predios (releer TIFs) aunque no hayan cambiado; "
        "por defecto el build es incremental.",
    )
    parser.add_argument(
        "--upscale-min-side",
        type=int,
        default=WEBP_UPSCALE_MIN_SIDE,
        help=f"Aumentar el WebP hasta que su lado menor sea ≥ N px (default: {WEBP_UPSCALE_MIN_SIDE}).",
    )
    parser.add_argument(
        "--webp-quality",
        type=int,
        default=WEBP_QUALITY,
        help=f"Calidad WebP 1-100 (default: {WEBP_QUALITY}).",
    )
    args = parser.parse_args(argv)

    bands_filter = (
        [s.strip() for s in args.bands.split(",") if s.strip()] if args.bands else None
    )

    build(
        tif_dir=Path(args.tif_dir),
        static_dir=Path(args.static_dir),
        aoi_geojson=Path(args.aoi_geojson) if args.aoi_geojson else None,
        db_csv=Path(args.db_csv) if args.db_csv else None,
        current_year=args.current_year,
        bands_filter=bands_filter,
        force=args.force,
        incremental=not args.no_incremental,
        upscale_min_side=args.upscale_min_side,
        webp_quality=args.webp_quality,
    )


if __name__ == "__main__":
    main()
