#!/usr/bin/env python3
"""
Pipeline local: GeoTIFF Sentinel-2 descargado de Drive → WebP + metadata.json

Uso típico
----------
    python scripts/gee/build_s2_satellite_previews.py \
        --tif-dir data/sentinel2 \
        --webp-dir data_static/satellite2/rasters \
        --meta-out data_static/satellite2/metadata.json \
        --aoi-geojson data/shapefiles/aoi.geojson \
        --db-csv data/fic_database.csv

Los GeoTIFF deben seguir la convención de nombre del script export_s2.py:
    {PREFIX}_{PREDIO_UPPER}_{COMPOSITE_KEY}.tif

Ejemplos de COMPOSITE_KEY:
  monthly_hist_04         → mediana histórica de Abril
  monthly_2026_04         → mediana de Abril 2026
  weekly_last             → última semana completa

Cada banda del GeoTIFF se convierte en un WebP con colormap; los valores int16
están escalados ×1000 (el divisor es 1000).

Colormaps por banda (extensibles con --colormaps JSON):
  NDVI   → RdYlGn
  NDWI   → RdYlBu
  NDMI   → RdYlBu
  EVI    → YlGn
  SAVI   → YlGn
  default → RdYlGn
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    import rasterio
    from rasterio.warp import transform_bounds
except ImportError:
    print("Error: falta 'rasterio'. Instala con: pip install rasterio", file=sys.stderr)
    sys.exit(1)

try:
    from PIL import Image as PILImage
except ImportError:
    print("Error: falta 'Pillow'. Instala con: pip install Pillow", file=sys.stderr)
    sys.exit(1)

try:
    import matplotlib.cm as mcm
    import matplotlib.colors as mcolors
except ImportError:
    print("Error: falta 'matplotlib'. Instala con: pip install matplotlib", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIVISOR = 1000.0  # int16 × 1000 en el asset GEE → dividir para obtener valor real

BAND_VIZ: dict[str, dict] = {
    "NDVI":  {"label": "NDVI",  "vmin": -1.0, "vmax": 1.0,  "colormap": "RdYlGn"},
    "NDWI":  {"label": "NDWI",  "vmin": -1.0, "vmax": 1.0,  "colormap": "RdYlBu"},
    "NDMI":  {"label": "NDMI",  "vmin": -1.0, "vmax": 1.0,  "colormap": "RdYlBu_r"},
    "MNDWI": {"label": "MNDWI", "vmin": -1.0, "vmax": 1.0,  "colormap": "Blues"},
    "GNDVI": {"label": "GNDVI", "vmin": -1.0, "vmax": 1.0,  "colormap": "YlGn"},
    "EVI":   {"label": "EVI",   "vmin": -1.0, "vmax": 1.0,  "colormap": "YlGn"},
    "SAVI":  {"label": "SAVI",  "vmin": -1.0, "vmax": 1.0,  "colormap": "YlGn"},
    "MSAVI": {"label": "MSAVI", "vmin": -1.0, "vmax": 1.0,  "colormap": "YlGn"},
    "ARI":   {"label": "ARI",   "vmin":  0.0, "vmax": 2.0,  "colormap": "RdPu"},
    "MARI":  {"label": "MARI",  "vmin":  0.0, "vmax": 4.0,  "colormap": "RdPu"},
    "LAI":   {"label": "LAI",   "vmin":  0.0, "vmax": 8.0,  "colormap": "YlGn"},
    "FAPAR": {"label": "FAPAR", "vmin":  0.0, "vmax": 1.0,  "colormap": "YlGn"},
    "FCOVER":{"label": "FCOVER","vmin":  0.0, "vmax": 1.0,  "colormap": "YlGn"},
    "MSI":   {"label": "MSI",   "vmin":  0.0, "vmax": 3.0,  "colormap": "RdYlBu_r"},
}
DEFAULT_VIZ = {"vmin": -1.0, "vmax": 1.0, "colormap": "RdYlGn"}

WEBP_QUALITY = 85
MAX_SIZE = 2048  # px máximo lado

MONTH_NAMES_ES = [
    "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
]

# ---------------------------------------------------------------------------
# Parsing filename convention
# ---------------------------------------------------------------------------

_COMPOSITE_RE = re.compile(
    r"^(?P<prefix>[A-Za-z0-9]+)_(?P<predio>[A-Za-z0-9]+)_(?P<composite_key>.+)$"
)


def parse_tif_stem(stem: str) -> dict | None:
    """
    Extrae {prefix, predio_id, composite_key} de la convención de nombre.
    Retorna None si no coincide.
    """
    m = _COMPOSITE_RE.match(stem)
    if not m:
        return None
    return {
        "prefix": m.group("prefix"),
        "predio_id": m.group("predio").lower(),
        "composite_key": m.group("composite_key"),
    }


def composite_meta(composite_key: str, current_year: int | None = None) -> dict:
    """
    Interpreta composite_key y retorna metadatos de visualización:
    {view_mode, role, label, year, month, is_weekly_last}
    """
    ck = composite_key.lower()
    m = re.match(r"monthly_hist_(\d{2})$", ck)
    if m:
        mo = int(m.group(1))
        return {
            "view_mode": "monthly",
            "role": "left",
            "label": f"Mediana histórica {MONTH_NAMES_ES[mo-1]}",
            "year": None,
            "month": mo,
            "is_weekly_last": False,
        }
    m = re.match(r"monthly_(\d{4})_(\d{2})$", ck)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return {
            "view_mode": "monthly",
            "role": "right",
            "label": f"Mediana {MONTH_NAMES_ES[mo-1]} {y}",
            "year": y,
            "month": mo,
            "is_weekly_last": False,
        }
    if ck == "weekly_last":
        return {
            "view_mode": "weekly",
            "role": "right",
            "label": "Última semana completa",
            "year": None,
            "month": None,
            "is_weekly_last": True,
        }
    m = re.match(r"weekly_(\d{8})$", ck)
    if m:
        d_str = m.group(1)
        try:
            d = datetime.strptime(d_str, "%Y%m%d")
            label = f"Semana {d.day} {MONTH_NAMES_ES[d.month-1]} {d.year}"
        except ValueError:
            label = f"Semana {d_str}"
        return {
            "view_mode": "weekly",
            "role": "right",
            "label": label,
            "year": None,
            "month": None,
            "is_weekly_last": False,
        }
    return {
        "view_mode": "weekly",
        "role": "right",
        "label": composite_key,
        "year": None,
        "month": None,
        "is_weekly_last": False,
    }


# ---------------------------------------------------------------------------
# TIF → WebP
# ---------------------------------------------------------------------------

def _bounds_wgs84(ds: rasterio.DatasetReader) -> tuple[float, float, float, float]:
    """Retorna (lon_west, lat_south, lon_east, lat_north) en WGS84."""
    if ds.crs and ds.crs.to_epsg() != 4326:
        bounds = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
    else:
        b = ds.bounds
        bounds = (b.left, b.bottom, b.right, b.top)
    return bounds  # (west, south, east, north)


def _apply_colormap(
    data: np.ndarray,
    mask: np.ndarray,
    vmin: float,
    vmax: float,
    cmap_name: str,
) -> np.ndarray:
    """
    Aplica colormap a ``data`` (float32); retorna RGBA uint8 con alpha=0 donde ``mask`` es True.
    """
    norm = np.clip((data - vmin) / (vmax - vmin + 1e-9), 0.0, 1.0)
    try:
        cmap = mcm.get_cmap(cmap_name)
    except ValueError:
        cmap = mcm.get_cmap("RdYlGn")
    rgba = (cmap(norm) * 255).astype(np.uint8)
    rgba[mask, 3] = 0
    return rgba


def tif_band_to_webp(
    tif_path: Path,
    band_name: str,
    band_idx: int,
    out_path: Path,
    *,
    vmin: float,
    vmax: float,
    cmap_name: str,
    max_size: int = MAX_SIZE,
    quality: int = WEBP_QUALITY,
) -> tuple[int, int]:
    """
    Lee la banda ``band_idx`` (1-based) del GeoTIFF, aplica colormap y guarda WebP.
    Retorna (ancho, alto) del WebP guardado.
    """
    with rasterio.open(tif_path) as ds:
        raw = ds.read(band_idx).astype(np.float32)
        nodata = ds.nodata if ds.nodata is not None else -9999

    mask = (raw == nodata) | np.isnan(raw) | np.isinf(raw)
    data_f = raw / DIVISOR
    rgba = _apply_colormap(data_f, mask, vmin, vmax, cmap_name)

    h, w = rgba.shape[:2]
    if max(h, w) > max_size:
        scale = max_size / max(h, w)
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    else:
        new_w, new_h = w, h

    img = PILImage.fromarray(rgba, mode="RGBA")
    if (new_w, new_h) != (w, h):
        img = img.resize((new_w, new_h), PILImage.LANCZOS)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="WEBP", quality=quality, method=6)
    return new_w, new_h


# ---------------------------------------------------------------------------
# Load auxiliary data
# ---------------------------------------------------------------------------

def load_predios_info(aoi_geojson: Path, db_csv: Path | None) -> dict[str, dict]:
    """
    Retorna dict[predio_id] = {name, area_ha, center[lat,lon], codigo_predio, ...}
    Combina aoi.geojson y fic_database.csv.
    """
    info: dict[str, dict] = {}

    with open(aoi_geojson, encoding="utf-8") as f:
        fc = json.load(f)
    for feat in fc.get("features", []):
        props = feat.get("properties") or {}
        wid = (props.get("wetland_id") or props.get("predio_id") or "").strip().lower()
        if not wid:
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates", [[]])
        # Compute rough centroid from exterior ring
        ring = coords[0] if geom.get("type") == "Polygon" else (coords[0][0] if coords else [])
        if ring:
            lons = [c[0] for c in ring]
            lats = [c[1] for c in ring]
            center = [sum(lats) / len(lats), sum(lons) / len(lons)]
        else:
            center = [0.0, 0.0]
        info[wid] = {
            "name": props.get("nombre") or wid.upper(),
            "area_ha": None,
            "center": center,
            "codigo_predio": wid.upper(),
        }

    if db_csv and db_csv.exists():
        import csv
        with open(db_csv, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                codigo = (row.get("codigo_predio") or "").strip()
                if not codigo:
                    continue
                wid_key = codigo.lower()
                if wid_key in info:
                    info[wid_key].update({k: v for k, v in row.items() if v})
                    info[wid_key]["codigo_predio"] = codigo
    return info


# ---------------------------------------------------------------------------
# Main build pipeline
# ---------------------------------------------------------------------------

def build_satellite_metadata(
    tif_dir: Path,
    webp_dir: Path,
    meta_out: Path,
    aoi_geojson: Path,
    db_csv: Path | None,
    *,
    band_viz_override: dict | None = None,
    webp_quality: int = WEBP_QUALITY,
    max_size: int = MAX_SIZE,
    force: bool = False,
    stem_prefix: str | None = None,
    webp_rel_prefix: str = "satellite2/rasters",
) -> None:
    """
    Recorre ``tif_dir`` buscando GeoTIFF, genera WebP en ``webp_dir`` y escribe
    ``meta_out`` (``data_static/satellite2/metadata.json``).
    """
    viz_map = {**BAND_VIZ, **(band_viz_override or {})}
    predios_info = load_predios_info(aoi_geojson, db_csv)

    tif_paths = sorted(tif_dir.glob("*.tif")) + sorted(tif_dir.glob("*.tiff"))
    if not tif_paths:
        print(f"No se encontraron GeoTIFF en {tif_dir}", file=sys.stderr)
        return

    rasters_meta: dict[str, dict] = {}
    wetlands_seen: dict[str, dict] = {}  # predio_id → stats

    for tif_path in tif_paths:
        parsed = parse_tif_stem(tif_path.stem)
        if not parsed:
            print(f"  [skip] nombre no reconocido: {tif_path.name}")
            continue
        if stem_prefix and parsed["prefix"].upper() != stem_prefix.upper():
            print(f"  [skip] prefijo no coincide ({parsed['prefix']} ≠ {stem_prefix}): {tif_path.name}")
            continue

        pid = parsed["predio_id"]
        comp_key = parsed["composite_key"]
        cmeta = composite_meta(comp_key)
        print(f"Procesando {tif_path.name}  →  predio={pid} key={comp_key}")

        with rasterio.open(tif_path) as ds:
            band_names = list(ds.descriptions) if ds.descriptions else []
            n_bands = ds.count
            bounds_ws = _bounds_wgs84(ds)  # (west, south, east, north)

        leaflet_bounds = [
            [bounds_ws[1], bounds_ws[0]],  # SW [lat, lon]
            [bounds_ws[3], bounds_ws[2]],  # NE [lat, lon]
        ]

        # Fill band names if not set in raster metadata
        if not band_names or not any(band_names):
            # Try to infer from stem_prefix_BANDS naming convention or defaults
            band_names = [f"band_{i+1}" for i in range(n_bands)]

        for b_idx in range(1, n_bands + 1):
            band_name_raw = (band_names[b_idx - 1] or f"band_{b_idx}").strip().upper()
            band_name = band_name_raw  # normalized key
            viz = viz_map.get(band_name, DEFAULT_VIZ)
            vmin, vmax = viz["vmin"], viz["vmax"]
            cmap = viz.get("colormap", DEFAULT_VIZ["colormap"])
            band_label = viz.get("label", band_name)

            webp_stem = f"{parsed['prefix']}_{pid.upper()}_{comp_key}_{band_name}.webp"
            webp_path = webp_dir / webp_stem
            webp_rel = f"{webp_rel_prefix}/{webp_stem}"

            if webp_path.exists() and not force:
                # Read existing size
                with PILImage.open(webp_path) as im:
                    w_px, h_px = im.size
                print(f"  [cache] {webp_stem}")
            else:
                try:
                    w_px, h_px = tif_band_to_webp(
                        tif_path,
                        band_name,
                        b_idx,
                        webp_path,
                        vmin=vmin,
                        vmax=vmax,
                        cmap_name=cmap,
                        max_size=max_size,
                        quality=webp_quality,
                    )
                    print(f"  → {webp_stem} ({w_px}×{h_px}px)")
                except Exception as exc:
                    print(f"  [error] {webp_stem}: {exc}", file=sys.stderr)
                    continue

            raster_key = f"{pid}_{comp_key}_{band_name.lower()}"
            rasters_meta[raster_key] = {
                "wetland_id": pid,
                "view_mode": cmeta["view_mode"],
                "role": cmeta["role"],
                "composite_key": comp_key,
                "label": cmeta["label"],
                "band": band_name,
                "band_label": band_label,
                "year": cmeta["year"],
                "month": cmeta["month"],
                "is_weekly_last": cmeta["is_weekly_last"],
                "divisor": DIVISOR,
                "vmin": vmin,
                "vmax": vmax,
                "colormap": cmap,
                "visual": {
                    "path": webp_rel,
                    "bounds": leaflet_bounds,
                    "format": "WEBP",
                    "display_size": [w_px, h_px],
                    "render_mode": "smooth",
                    "opacity": 0.85,
                },
            }

        # Update wetlands_seen per predio
        if pid not in wetlands_seen:
            wetlands_seen[pid] = {
                "years": set(),
                "has_weekly_last": False,
                "last_month": None,
                "last_year": None,
            }
        if cmeta["year"] is not None:
            wetlands_seen[pid]["years"].add(cmeta["year"])
        if cmeta["is_weekly_last"]:
            wetlands_seen[pid]["has_weekly_last"] = True
        if cmeta["view_mode"] == "monthly" and cmeta["role"] == "right":
            wetlands_seen[pid]["last_month"] = cmeta["month"]
            wetlands_seen[pid]["last_year"] = cmeta["year"]

    # Build wetlands section
    wetlands_out: dict[str, dict] = {}
    for pid, stats in wetlands_seen.items():
        pinfo = predios_info.get(pid, {})
        avail_years = sorted(stats["years"])
        if avail_years:
            last_year = avail_years[-1]
            last_period = str(last_year)
            status = "ready"
        else:
            last_year = None
            last_period = None
            status = "empty"
        wetlands_out[pid] = {
            "name": pinfo.get("name") or pid.upper(),
            "area_ha": pinfo.get("area_ha"),
            "center": pinfo.get("center", [0.0, 0.0]),
            "codigo_predio": pinfo.get("codigo_predio") or pid.upper(),
            "nombre_agricultor": pinfo.get("nombre_agricultor", ""),
            "available_years": avail_years,
            "last_period": last_period,
            "has_weekly_last": stats["has_weekly_last"],
            "n_periods": len(avail_years),
            "status": status,
        }

    # Collect unique indices for the "indices" section
    indices_out: dict[str, dict] = {}
    for rk, rv in rasters_meta.items():
        band = rv["band"]
        if band not in indices_out:
            indices_out[band] = {
                "label": rv["band_label"],
                "vmin": rv["vmin"],
                "vmax": rv["vmax"],
                "colormap": rv["colormap"],
                "visual_only": False,
            }

    metadata = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "aoi_id_column": "wetland_id",
        "source": {
            "id": "sentinel2",
            "label": "Satélite Sentinel-2",
            "description": "Mosaicos semanales Sentinel-2 por predio.",
            "color": "#1d6b4a",
            "has_data": bool(wetlands_out),
        },
        "indices": indices_out,
        "view_modes": {
            "monthly": {
                "left_composite_prefix": "monthly_hist_",
                "right_composite_prefix": "monthly_",
                "left_label": "Mediana histórica por mes",
                "right_label": "Último mes completo",
                "pill_selector": True,
                "pill_by": "month",
                "months": list(range(1, 13)),
                "month_labels": MONTH_NAMES_ES,
            },
            "weekly": {
                "left_composite_prefix": "monthly_hist_",
                "right_composite_key": "weekly_last",
                "left_label": "Mediana histórica del mes",
                "right_label": "Última semana completa",
                "pill_selector": True,
                "pill_by": "month",
                "months": list(range(1, 13)),
                "month_labels": MONTH_NAMES_ES,
            },
        },
        "wetlands": wetlands_out,
        "rasters": rasters_meta,
    }

    meta_out.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_out, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"\nMetadata escrita: {meta_out}")
    print(f"  Predios: {len(wetlands_out)}  |  Rasters: {len(rasters_meta)}  |  Índices: {len(indices_out)}")

    # Update sources_manifest.json if present
    manifest_path = meta_out.parent.parent / "sources_manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            if "sources" not in manifest:
                manifest["sources"] = {}
            if "sentinel2" not in manifest["sources"]:
                manifest["sources"]["sentinel2"] = {}
            avail_years: list = []
            for w in wetlands_out.values():
                avail_years.extend(w.get("available_years") or [])
            avail_years = sorted(set(avail_years))
            manifest["sources"]["sentinel2"].update({
                "id": "sentinel2",
                "label": "Satélite Sentinel-2",
                "description": "Mosaicos semanales Sentinel-2 por predio.",
                "color": "#1d6b4a",
                "has_data": bool(wetlands_out),
                "timeseries_path": None,
                "metadata_path": "satellite2/metadata.json",
                "status": "ready" if wetlands_out else "pending",
                "summary": {
                    "n_wetlands": len(wetlands_out),
                    "raster_count": len(rasters_meta),
                    "available_years": avail_years,
                },
            })
            manifest["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            print(f"Manifest actualizado: {manifest_path}")
        except Exception as exc:
            print(f"  [aviso] No se pudo actualizar el manifest: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Copy from Drive helper
# ---------------------------------------------------------------------------

def sync_drive_to_local(drive_dir: Path, tif_dir: Path, *, dry_run: bool = False) -> int:
    """
    Copia archivos .tif desde ``drive_dir`` a ``tif_dir`` si no existen o son más nuevos.
    Retorna cantidad de archivos copiados.
    """
    import shutil

    tif_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sorted(drive_dir.glob("*.tif")) + sorted(drive_dir.glob("*.tiff")):
        dst = tif_dir / src.name
        if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
            if dry_run:
                print(f"  [dry-run] copiaría {src.name}")
            else:
                shutil.copy2(src, dst)
                print(f"  Copiado: {src.name}")
            copied += 1
        else:
            print(f"  [omitir] {src.name} (sin cambios)")
    return copied


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convierte GeoTIFF Sentinel-2 (de Drive) a WebP y genera metadata.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tif-dir",
        default="data/sentinel2",
        metavar="DIR",
        help="Directorio con GeoTIFF descargados de Drive (default: data/sentinel2).",
    )
    parser.add_argument(
        "--webp-dir",
        default="data_static/satellite2/rasters",
        metavar="DIR",
        help="Directorio de salida para los WebP (default: data_static/satellite2/rasters).",
    )
    parser.add_argument(
        "--meta-out",
        default="data_static/satellite2/metadata.json",
        metavar="FILE",
        help="Ruta de salida para metadata.json (default: data_static/satellite2/metadata.json).",
    )
    parser.add_argument(
        "--aoi-geojson",
        default="data/shapefiles/aoi.geojson",
        metavar="FILE",
        help="GeoJSON local con predios (default: data/shapefiles/aoi.geojson).",
    )
    parser.add_argument(
        "--db-csv",
        default="data/fic_database.csv",
        metavar="FILE",
        help="CSV con datos de agricultores (default: data/fic_database.csv).",
    )
    parser.add_argument(
        "--stem-prefix",
        default=None,
        metavar="PREFIX",
        help="Solo procesar TIF cuyo nombre empiece con este prefijo (ej. 'S2').",
    )
    parser.add_argument(
        "--webp-rel-prefix",
        default="satellite2/rasters",
        metavar="REL",
        help="Prefijo relativo para las rutas de WebP en metadata.json (default: satellite2/rasters).",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=WEBP_QUALITY,
        metavar="N",
        help=f"Calidad WebP 1-100 (default: {WEBP_QUALITY}).",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=MAX_SIZE,
        metavar="PX",
        help=f"Lado máximo del WebP en píxeles (default: {MAX_SIZE}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-generar WebP aunque ya existan.",
    )
    parser.add_argument(
        "--sync-from-drive",
        default=None,
        metavar="DRIVE_DIR",
        help="Copiar TIFs desde esta carpeta local de Drive antes de procesar.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo mostrar qué se haría, sin escribir archivos.",
    )
    args = parser.parse_args()

    tif_dir = Path(args.tif_dir)
    webp_dir = Path(args.webp_dir)
    meta_out = Path(args.meta_out)
    aoi_geojson = Path(args.aoi_geojson)
    db_csv = Path(args.db_csv) if args.db_csv else None

    if not aoi_geojson.exists():
        print(f"Error: GeoJSON no encontrado: {aoi_geojson}", file=sys.stderr)
        sys.exit(1)

    if args.sync_from_drive:
        drive_dir = Path(args.sync_from_drive)
        if not drive_dir.exists():
            print(f"Error: directorio Drive no encontrado: {drive_dir}", file=sys.stderr)
            sys.exit(1)
        n = sync_drive_to_local(drive_dir, tif_dir, dry_run=args.dry_run)
        print(f"Copiados desde Drive: {n} archivo(s)")

    if not tif_dir.exists():
        print(f"Error: directorio TIF no encontrado: {tif_dir}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("Modo dry-run: no se escribirán WebP ni metadata.json.")
        return

    build_satellite_metadata(
        tif_dir=tif_dir,
        webp_dir=webp_dir,
        meta_out=meta_out,
        aoi_geojson=aoi_geojson,
        db_csv=db_csv,
        webp_quality=args.quality,
        max_size=args.max_size,
        force=args.force,
        stem_prefix=args.stem_prefix,
        webp_rel_prefix=args.webp_rel_prefix,
    )


if __name__ == "__main__":
    main()
