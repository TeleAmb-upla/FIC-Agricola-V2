#!/usr/bin/env python3
"""
Añade series temporales por cuartel (``id_cuartel``) a ``timeseries.json`` de dron y Sentinel-2.

Uso desde la raíz del repo::

    python scripts/static_site/augment_cuartel_timeseries.py
    python scripts/static_site/augment_cuartel_timeseries.py --source drone
    python scripts/static_site/augment_cuartel_timeseries.py --source sentinel2
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "static_site"
for p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipeline_utils import (  # noqa: E402
    bootstrap_proj_environment,
    build_cuarteles_index,
    load_config,
    load_cuartels_by_wetland,
)

bootstrap_proj_environment()


def _json_dump(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def augment_drone_timeseries(config: dict, cuartels_by_wetland: dict[str, list[dict]]) -> int:
    from export_data_ortho import compute_metrics, extract_zonal_mean

    static_dir = (REPO_ROOT / config["sources"]["drone"]["static_root"]).resolve()
    ts_path = static_dir / "timeseries.json"
    meta_path = static_dir / "metadata.json"
    if not ts_path.is_file() or not meta_path.is_file():
        print("  [skip] dron: falta timeseries.json o metadata.json")
        return 0

    ts_doc = json.loads(ts_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rasters = meta.get("rasters") or {}
    indices_cfg = config.get("indices") or {}
    n_cuartels = 0

    for wetland_id, cuartels in cuartels_by_wetland.items():
        wentry = ts_doc.get("wetlands", {}).get(wetland_id)
        if not wentry:
            continue
        # Si el predio tiene un único cuartel, este equivale al predio completo: se permite
        # el respaldo de media global cuando el polígono no coincide con la huella del vuelo.
        single_cuartel = len(cuartels) == 1
        cuartel_block: dict[str, dict] = {}
        for cu in cuartels:
            cid = cu["id_cuartel"]
            geom = cu["geometry"]
            cu_indices: dict[str, dict] = {}
            for rk, raster in rasters.items():
                if str(raster.get("wetland_id") or "").lower() != wetland_id:
                    continue
                index_key = str(raster.get("index") or "").lower()
                if not index_key or indices_cfg.get(index_key, {}).get("visual_only"):
                    continue
                analytic = raster.get("analytic_path")
                if not analytic:
                    continue
                tiff_path = Path(analytic)
                if not tiff_path.is_file():
                    tiff_path = REPO_ROOT / analytic
                if not tiff_path.is_file():
                    continue
                period_key = str(raster.get("period_key") or "")
                label = str(raster.get("season") or period_key)
                year = int(raster.get("year") or (period_key[:4] if len(period_key) >= 4 else 0) or 0)
                if len(period_key) == 8 and period_key.isdigit():
                    iso_date = f"{period_key[0:4]}-{period_key[4:6]}-{period_key[6:8]}"
                else:
                    iso_date = f"{year}-01-01"
                mean_value = extract_zonal_mean(
                    tiff_path, geom, allow_full_raster_fallback=single_cuartel
                )
                if mean_value is None:
                    continue
                pt = {
                    "date": iso_date,
                    "label": label,
                    "year": year,
                    "season_key": "vuelo",
                    "season_label": label,
                    "period_key": period_key,
                    "value": round(float(mean_value), 4),
                }
                slot = cu_indices.setdefault(index_key, {"points": [], "metrics": {}})
                slot["points"].append(pt)

            for index_key, slot in cu_indices.items():
                pts = sorted(slot["points"], key=lambda p: p["date"])
                slot["points"] = pts
                slot["metrics"] = compute_metrics(pts)

            if cu_indices:
                cuartel_block[cid] = {
                    "id_cuartel": cid,
                    "nom_cuartel": cu["nom_cuartel"],
                    "cultivo": cu["cultivo"],
                    "superficie": cu["superficie"],
                    "indices": cu_indices,
                }
                n_cuartels += 1

        if cuartel_block:
            wentry["cuarteles"] = cuartel_block

    _json_dump(ts_path, ts_doc)
    print(f"  dron: {n_cuartels} cuartel(es) en {ts_path.relative_to(REPO_ROOT)}")
    return n_cuartels


def augment_sentinel2_timeseries(config: dict, cuartels_by_wetland: dict[str, list[dict]]) -> int:
    from datetime import datetime, timezone

    from build_sentinel2_local import (
        augment_timeseries_with_monthly,
        build_s2_file_to_wetland_map,
        clamp_last_complete_month_to_data,
        clamp_last_complete_week_to_data,
        compute_weekly_timeseries,
        current_iso_today,
        discover_tifs,
        last_complete_iso_week,
        last_complete_month,
        mask_stack_to_geometry,
        read_stack,
    )

    s2_cfg = config["sources"]["sentinel2"]
    static_dir = (REPO_ROOT / s2_cfg["static_root"]).resolve()
    tif_dir = (REPO_ROOT / s2_cfg["input_root"]).resolve()
    ts_path = static_dir / "timeseries.json"
    if not ts_path.is_file():
        print("  [skip] sentinel2: falta timeseries.json")
        return 0
    ts_doc = json.loads(ts_path.read_text(encoding="utf-8"))
    s2_map = build_s2_file_to_wetland_map(config)
    grouped = discover_tifs(tif_dir, s2_map)
    all_years: set[int] = set()
    for recs in grouped.values():
        all_years.update(r["year"] for r in recs)
    cy_today, _ = current_iso_today()
    current_year = max(all_years) if all_years else cy_today
    iy_meta, iw_meta = current_iso_today()
    lc_y, lc_w = last_complete_iso_week(iy_meta, iw_meta)
    lc_y, lc_m = last_complete_month(iy_meta, datetime.now(timezone.utc).month)
    lc_y, lc_w = clamp_last_complete_week_to_data(lc_y, lc_w, grouped, current_year)
    lc_y, lc_m = clamp_last_complete_month_to_data(lc_y, lc_m, grouped, current_year)

    n_cuartels = 0
    for wetland_id, cuartels in cuartels_by_wetland.items():
        records = grouped.get(wetland_id)
        if not records:
            continue
        wentry = ts_doc.get("wetlands", {}).get(wetland_id)
        if not wentry:
            continue
        stack, band_names, geo = read_stack(records)
        cuartel_block: dict[str, dict] = {}
        for cu in cuartels:
            cid = cu["id_cuartel"]
            masked = mask_stack_to_geometry(
                stack.copy(), cu["geometry"], geo.get("transform"), geo.get("crs")
            )
            ts_cu = compute_weekly_timeseries(masked, records, band_names, current_year)
            augment_timeseries_with_monthly(
                ts_cu, masked, records, band_names, current_year, lc_y, lc_m
            )
            if ts_cu:
                cuartel_block[cid] = {
                    "id_cuartel": cid,
                    "nom_cuartel": cu["nom_cuartel"],
                    "cultivo": cu["cultivo"],
                    "superficie": cu["superficie"],
                    **ts_cu,
                }
                n_cuartels += 1
        if cuartel_block:
            wentry["cuarteles"] = cuartel_block

    _json_dump(ts_path, ts_doc)
    print(f"  sentinel2: {n_cuartels} cuartel(es) en {ts_path.relative_to(REPO_ROOT)}")
    return n_cuartels


def main() -> None:
    ap = argparse.ArgumentParser(description="Series temporales por cuartel (id_cuartel).")
    ap.add_argument(
        "--source",
        choices=("drone", "sentinel2", "all"),
        default="all",
        help="Fuente a enriquecer (default: all)",
    )
    args = ap.parse_args()
    config = load_config()
    cuartels_by_wetland = load_cuartels_by_wetland(config)
    index = build_cuarteles_index(config)
    index_path = REPO_ROOT / "data_static" / "cuarteles_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    _json_dump(index_path, {"cuarteles": index, "by_wetland": {
        wid: [c["id_cuartel"] for c in cu_list]
        for wid, cu_list in cuartels_by_wetland.items()
    }})
    print(f"Índice cuarteles: {index_path.relative_to(REPO_ROOT)} ({len(index)} filas)")

    total = 0
    if args.source in ("drone", "all"):
        total += augment_drone_timeseries(config, cuartels_by_wetland)
    if args.source in ("sentinel2", "all"):
        total += augment_sentinel2_timeseries(config, cuartels_by_wetland)
    if args.source in ("drone", "all"):
        from build_cuarteles_display_geojson import main as build_display_geojson

        build_display_geojson()
    print(f"Listo: {total} bloque(s) cuartel actualizados.")


if __name__ == "__main__":
    main()
