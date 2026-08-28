#!/usr/bin/env python3
"""
GeoTIFF semanales Sentinel-1 (``Y{year}_W{week}.tif``) → índices SAR, series por
predio y WebP para el explorador FIC (mismas claves de comparador que S2).

Entrada: pila de ``update_s1_weekly_collection.py`` (VV/VH γ0, VV_DB/VH_DB).
Productos: RVI, VV_DB, VH_DB, SSM.

Uso (desde ``fic_agro/``)::

    python scripts/static_site/build_sentinel1_local.py
    python scripts/static_site/build_sentinel1_local.py --force
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

try:
    import rasterio
    from rasterio.mask import mask as rio_mask
    from rasterio.warp import Resampling, reproject
except ImportError as exc:
    print(f"Falta rasterio: {exc}", file=sys.stderr)
    sys.exit(1)

from shapely.geometry import mapping, shape

if __name__ == "__main__" and not __package__:
    _root = Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from scripts.static_site.build_sentinel2_local import (
    MONTH_NAMES_ES,
    WEBP_QUALITY,
    WEBP_UPSCALE_MIN_SIDE,
    _calendar_month_complete_for_current_year,
    _is_map_composite_key,
    _sanitize_numbers,
    clamp_last_complete_month_to_data,
    clamp_last_complete_week_to_data,
    compute_aggregates,
    current_iso_today,
    last_complete_iso_week,
    last_complete_month,
    load_predios_info,
    render_band_to_webp,
    write_predio_csv,
)
from scripts.static_site.pipeline_utils import (
    REPO_ROOT,
    load_config,
    load_predio_clip_geometries,
)

_STEM_RE = re.compile(r"^Y(?P<year>\d{4})_W(?P<week>\d{2})$", re.I)

SSM_URBAN_DB = -6.0
SSM_WATER_DB = -17.0

BAND_VIZ: dict[str, dict] = {
    "RVI": {"label": "RVI (vegetación radar)", "colormap": "RdYlGn", "vmin": 0.0, "vmax": 4.0},
    "VV_DB": {"label": "γ0 VV (dB)", "colormap": "viridis", "vmin": -20.0, "vmax": 0.0},
    "VH_DB": {"label": "γ0 VH (dB)", "colormap": "viridis", "vmin": -28.0, "vmax": -5.0},
    "SSM": {"label": "Humedad relativa (cambio VV)", "colormap": "Blues", "vmin": 0.0, "vmax": 1.0},
}
SCALAR_BANDS = tuple(BAND_VIZ)
DEFAULT_BAND = "RVI"

DEFAULT_TIF_DIR = REPO_ROOT / "data" / "sentinel1"
DEFAULT_STATIC_DIR = REPO_ROOT / "data_static" / "sentinel1"
DEFAULT_AOI_GEOJSON = REPO_ROOT / "data_static" / "predios_aoi.geojson"
DEFAULT_DB_CSV = REPO_ROOT / "data" / "fic_database.csv"


def discover_weekly_tifs(tif_dir: Path) -> list[dict]:
    rows: list[dict] = []
    if not tif_dir.is_dir():
        return rows
    for path in sorted(tif_dir.glob("Y*_W*.tif")):
        m = _STEM_RE.match(path.stem)
        if not m:
            continue
        y, w = int(m.group("year")), int(m.group("week"))
        try:
            thu = date.fromisocalendar(y, w, 4)
        except ValueError:
            continue
        rows.append(
            {
                "path": path,
                "year": y,
                "week": w,
                "thursday": thu,
            }
        )
    rows.sort(key=lambda r: (r["year"], r["week"]))
    return rows


def _safe_ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(den > 0, num / den, np.nan)
    return out.astype(np.float32)


def compute_soil_moisture(vv_lin: np.ndarray, vv_db: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        lo = np.nanpercentile(vv_lin, 5.0, axis=0)
        hi = np.nanpercentile(vv_lin, 95.0, axis=0)
        mean_db = np.nanmean(vv_db, axis=0)
    sensitivity = np.where(hi > lo, hi - lo, np.nan)
    ssm = np.clip((vv_lin - lo) / sensitivity, 0.0, 1.0)
    valid = (mean_db <= SSM_URBAN_DB) & (mean_db >= SSM_WATER_DB)
    return np.where(valid[None, :, :], ssm, np.nan).astype(np.float32)


def _band_index(names: list[str], wanted: str) -> int | None:
    u = wanted.upper()
    for i, n in enumerate(names):
        if str(n or "").upper() == u:
            return i
    return None


def clip_week(path: Path, geom) -> tuple[np.ndarray, list[str], object, object] | None:
    with rasterio.open(path) as ds:
        names = [
            (d or f"B{i + 1}").strip().upper() for i, d in enumerate(ds.descriptions or [])
        ]
        if not names:
            names = [f"B{i + 1}" for i in range(ds.count)]
        try:
            data, transform = rio_mask(
                ds,
                [mapping(geom)],
                crop=True,
                filled=True,
                nodata=np.nan,
            )
        except ValueError:
            return None
        data = data.astype(np.float32)
        if ds.nodata is not None:
            data = np.where(data == ds.nodata, np.nan, data)
        return data, names, transform, ds.crs


def align_to_grid(
    data: np.ndarray,
    src_transform,
    src_crs,
    dst_h: int,
    dst_w: int,
    dst_transform,
    dst_crs,
) -> np.ndarray:
    out = np.full((data.shape[0], dst_h, dst_w), np.nan, dtype=np.float32)
    reproject(
        source=data,
        destination=out,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return out


def leaflet_bounds_from_transform(transform, h: int, w: int, crs) -> list:
    from rasterio.warp import transform_bounds

    left, bottom, right, top = rasterio.transform.array_bounds(h, w, transform)
    west, south, east, north = transform_bounds(crs, "EPSG:4326", left, bottom, right, top)
    return [[south, west], [north, east]]


def compute_s1_timeseries(
    stack: np.ndarray,
    records: list[dict],
    band_names: list[str],
    current_year: int,
    lm_year: int,
    lm_month: int,
) -> dict[str, dict]:
    n_bands = stack.shape[1]
    historic_years = sorted({r["year"] for r in records if r["year"] < current_year})
    week_to_hist_idx: dict[int, list[int]] = defaultdict(list)
    week_to_curr_idx: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if r["year"] < current_year:
            week_to_hist_idx[r["week"]].append(i)
        elif r["year"] == current_year:
            week_to_curr_idx[r["week"]].append(i)

    by_band: dict[str, dict] = {}
    for b_idx, band in enumerate(band_names):
        if b_idx >= n_bands:
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
            "current_year": {"year": current_year, "values_by_week": _sanitize_numbers(cur_vals)},
            "years_used": historic_years,
        }

    month_to_hist_idx: dict[int, list[int]] = defaultdict(list)
    month_to_curr_idx: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        m = int(r["thursday"].month)
        if r["year"] < current_year:
            month_to_hist_idx[m].append(i)
        elif r["year"] == current_year:
            month_to_curr_idx[m].append(i)

    for b_idx, band in enumerate(band_names):
        band_entry = by_band.get(band)
        if not band_entry:
            continue
        hist_med = [math.nan] * 12
        hist_p25 = [math.nan] * 12
        hist_p75 = [math.nan] * 12
        cur_month = [math.nan] * 12
        for m in range(1, 13):
            hist_idx = month_to_hist_idx.get(m, [])
            if hist_idx:
                yearly_means = []
                for i in hist_idx:
                    with np.errstate(invalid="ignore"):
                        mm = float(np.nanmean(stack[i, b_idx]))
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
            if _calendar_month_complete_for_current_year(current_year, m, lm_year, lm_month):
                curr_idx = month_to_curr_idx.get(m, [])
                if curr_idx:
                    slab = stack[curr_idx, b_idx]
                    with np.errstate(invalid="ignore"):
                        if slab.shape[0] > 1:
                            spatial = np.nanmean(np.nanmedian(slab, axis=0))
                        else:
                            spatial = np.nanmean(slab[0])
                    cur_month[m - 1] = float(spatial) if np.isfinite(spatial) else math.nan
        band_entry["months"] = list(range(1, 13))
        band_entry["historical_median_by_month"] = _sanitize_numbers(hist_med)
        band_entry["historical_p25_by_month"] = _sanitize_numbers(hist_p25)
        band_entry["historical_p75_by_month"] = _sanitize_numbers(hist_p75)
        band_entry["current_year"]["values_by_month"] = _sanitize_numbers(cur_month)

    return by_band


def update_sources_manifest(data_static_dir: Path, sentinel_meta: dict) -> None:
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
    manifest["sources"]["sentinel1"] = {
        "id": "sentinel1",
        "label": sentinel_meta["source"]["label"],
        "description": sentinel_meta["source"]["description"],
        "color": sentinel_meta["source"]["color"],
        "has_data": sentinel_meta["source"]["has_data"],
        "timeseries_path": "sentinel1/timeseries.json",
        "metadata_path": "sentinel1/metadata.json",
        "csv_dir": "sentinel1/csv",
        "status": "ready" if sentinel_meta["source"]["has_data"] else "pending",
        "summary": sentinel_meta["summary"],
    }
    manifest["generated_at"] = sentinel_meta["generated_at"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Manifest actualizado: {manifest_path.relative_to(REPO_ROOT)}")


def build_predio_cube(weeks: list[dict], geom) -> tuple[np.ndarray, list[dict], dict] | None:
    used: list[dict] = []
    slices: list[np.ndarray] = []
    ref_t = ref_crs = None
    ref_h = ref_w = 0

    for rec in weeks:
        clipped = clip_week(rec["path"], geom)
        if clipped is None:
            continue
        data, names, transform, crs = clipped
        i_vv = _band_index(names, "VV")
        i_vh = _band_index(names, "VH")
        i_vvd = _band_index(names, "VV_DB")
        i_vhd = _band_index(names, "VH_DB")
        if None in (i_vv, i_vh, i_vvd, i_vhd):
            print(f"  [omitir] {rec['path'].name}: faltan bandas VV/VH")
            continue
        slab = data[[i_vv, i_vh, i_vvd, i_vhd], :, :]
        if ref_t is None:
            ref_t, ref_crs = transform, crs
            ref_h, ref_w = slab.shape[1], slab.shape[2]
        elif slab.shape[1:] != (ref_h, ref_w):
            slab = align_to_grid(slab, transform, crs, ref_h, ref_w, ref_t, ref_crs)
        slices.append(slab)
        used.append(rec)

    if not slices or ref_t is None:
        return None

    raw = np.stack(slices, axis=0)
    vv, vh, vv_db, vh_db = raw[:, 0], raw[:, 1], raw[:, 2], raw[:, 3]
    rvi = _safe_ratio(4.0 * vh, vv + vh)
    ssm = compute_soil_moisture(vv, vv_db)
    stack = np.stack([rvi, vv_db, vh_db, ssm], axis=1)
    geo = {
        "leaflet_bounds": leaflet_bounds_from_transform(ref_t, ref_h, ref_w, ref_crs),
    }
    return stack, used, geo


def build(
    tif_dir: Path,
    static_dir: Path,
    aoi_geojson: Path | None,
    db_csv: Path | None,
    *,
    current_year: int | None = None,
    force: bool = False,
    upscale_min_side: int = WEBP_UPSCALE_MIN_SIDE,
    webp_quality: int = WEBP_QUALITY,
) -> None:
    rasters_dir = static_dir / "rasters"
    csv_dir = static_dir / "csv"
    rasters_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    weeks = discover_weekly_tifs(tif_dir)
    if not weeks:
        print(f"No hay TIFs S1 en {tif_dir}. Se actualiza el manifiesto vacío.")
        empty_meta = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": {
                "id": "sentinel1",
                "label": "Satélite Sentinel-1",
                "description": "Radar C-band semanal (sin GeoTIFF locales todavía).",
                "color": "#0f766e",
                "has_data": False,
            },
            "summary": {"n_wetlands": 0, "raster_count": 0, "available_years": []},
        }
        update_sources_manifest(static_dir.parent, empty_meta)
        return

    all_years = {r["year"] for r in weeks}
    if current_year is None:
        cy_today, _ = current_iso_today()
        current_year = max(all_years) if all_years else cy_today
    historic_years = sorted({y for y in all_years if y < current_year})
    print(f"Semanas S1: {len(weeks)} | años={sorted(all_years)} | actual={current_year}")

    cfg = load_config()
    clips = load_predio_clip_geometries(cfg)
    predios_info = load_predios_info(aoi_geojson, db_csv)
    grouped = {pid: weeks for pid in clips}

    iy_meta, iw_meta = current_iso_today()
    lc_week_y, lc_week_w = last_complete_iso_week(iy_meta, iw_meta)
    lm_year, lm_month = last_complete_month(iy_meta, datetime.now(timezone.utc).month)
    lc_week_y, lc_week_w = clamp_last_complete_week_to_data(
        lc_week_y, lc_week_w, grouped, current_year
    )
    lm_year, lm_month = clamp_last_complete_month_to_data(
        lm_year, lm_month, grouped, current_year
    )

    rasters_meta: dict[str, dict] = {}
    wetlands_meta: dict[str, dict] = {}
    timeseries_all: dict[str, dict] = {}
    band_names = list(SCALAR_BANDS)

    for predio, geom_gj in clips.items():
        print(f"\n=== S1 {predio} ===")
        geom = shape(geom_gj)
        built = build_predio_cube(weeks, geom)
        if built is None:
            print("  sin solape con mosaicos S1")
            continue
        stack, records, geo = built
        aggregates = compute_aggregates(stack, records, current_year)
        timeseries = compute_s1_timeseries(
            stack, records, band_names, current_year, lm_year, lm_month
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

        n_written = 0
        for comp_key, agg in aggregates.items():
            if not _is_map_composite_key(comp_key):
                continue
            raster_3d = agg["raster"]
            for b_idx, band in enumerate(band_names):
                viz = BAND_VIZ[band]
                stem = f"S1_{predio.upper()}_{comp_key}_{band}"
                webp_path = rasters_dir / f"{stem}.webp"
                if force or not webp_path.exists():
                    try:
                        render_band_to_webp(
                            raster_3d[b_idx],
                            webp_path,
                            vmin=viz["vmin"],
                            vmax=viz["vmax"],
                            colormap=viz["colormap"],
                            upscale_min_side=upscale_min_side,
                            quality=webp_quality,
                        )
                        n_written += 1
                    except (OSError, ValueError) as exc:
                        print(f"  [error] {stem}: {exc}", file=sys.stderr)
                        continue
                raster_key = f"{predio.lower()}_{comp_key.lower()}_{band.lower()}"
                rasters_meta[raster_key] = {
                    "p": f"sentinel1/rasters/{stem}.webp",
                    "l": agg["label"],
                    "n": agg["n_inputs"],
                }
        print(f"  WebP escritos/regenerados: {n_written}")

    indices_out = {
        band: {
            "label": viz["label"],
            "vmin": viz["vmin"],
            "vmax": viz["vmax"],
            "colormap": viz["colormap"],
            "visual_only": False,
            "scale": 1.0,
        }
        for band, viz in BAND_VIZ.items()
    }

    metadata = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "aoi_id_column": "predio_id",
        "current_year": current_year,
        "historic_years": historic_years,
        "available_years": sorted(all_years),
        "last_complete_week": {"year": lc_week_y, "week": lc_week_w},
        "last_complete_month": {"year": lm_year, "month": lm_month},
        "source": {
            "id": "sentinel1",
            "label": "Satélite Sentinel-1",
            "description": "Mosaicos semanales Sentinel-1 (radar) por predio.",
            "color": "#0f766e",
            "has_data": bool(wetlands_meta),
        },
        "raster_defaults": {
            "format": "WEBP",
            "render_mode": "smooth",
            "opacity": 0.88,
        },
        "indices": indices_out,
        "default_chart_band": DEFAULT_BAND,
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

    (static_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    timeseries_payload = {
        "generated_at": metadata["generated_at"],
        "current_year": current_year,
        "default_band": DEFAULT_BAND,
        "predios": timeseries_all,
        "wetlands": timeseries_all,
    }
    (static_dir / "timeseries.json").write_text(
        json.dumps(timeseries_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    update_sources_manifest(static_dir.parent, metadata)
    print(
        f"Listo. Predios: {len(wetlands_meta)} | Rasters WebP: {len(rasters_meta)}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pipeline local Sentinel-1 FIC → WebP/JSON.")
    parser.add_argument("--tif-dir", type=Path, default=DEFAULT_TIF_DIR)
    parser.add_argument("--static-dir", type=Path, default=DEFAULT_STATIC_DIR)
    parser.add_argument("--aoi-geojson", type=Path, default=DEFAULT_AOI_GEOJSON)
    parser.add_argument("--db-csv", type=Path, default=DEFAULT_DB_CSV)
    parser.add_argument("--current-year", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    build(
        args.tif_dir,
        args.static_dir,
        args.aoi_geojson if args.aoi_geojson.is_file() else None,
        args.db_csv if args.db_csv.is_file() else None,
        current_year=args.current_year,
        force=args.force,
    )


if __name__ == "__main__":
    main()
