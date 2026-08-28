#!/usr/bin/env python3
"""Exporta un único GeoTIFF dron (p. ej. térmica nueva) y lo fusiona en metadata.json."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "static_site"
for p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from export_data_ortho import (  # noqa: E402
    RGB_PREVIEW_MASK_REVISION,
    THERMAL_PREVIEW_REVISION,
    _clip_aoi_buffer_m,
    _label_flat_period,
    build_export_signature,
    build_file_fingerprint,
    build_preview_rgb,
    build_preview_raster,
    build_preview_thermal,
    buffer_geom_wgs84,
    extract_zonal_median,
    iso_now,
    json_dump,
    preview_clip_geom_digest,
    reuse_existing_preview,
    _reuse_previews_allowed,
)
from pipeline_utils import load_config, load_json_if_exists, load_predio_clip_geometries, predios_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta un GeoTIFF dron y actualiza metadata.json")
    parser.add_argument("tiff", type=Path, help="Ruta al GeoTIFF (p. ej. data/drone/J_CONTRERAS_..._thermal.tif)")
    args = parser.parse_args()

    tiff_path = args.tiff if args.tiff.is_absolute() else REPO_ROOT / args.tiff
    if not tiff_path.is_file():
        raise SystemExit(f"No existe: {tiff_path}")

    stem = tiff_path.stem
    parts = stem.rsplit("_", 1)
    if len(parts) != 2:
        raise SystemExit(f"Nombre no reconocido: {tiff_path.name}")
    code_date, index_key = parts[0], parts[1].lower()
    code_parts = code_date.rsplit("_", 3)
    if len(code_parts) < 4:
        raise SystemExit(f"Fecha no reconocida en: {tiff_path.name}")
    drone_code = "_".join(code_parts[:-3]).upper()
    ymd_raw = "".join(code_parts[-3:])

    config = load_config()
    predio_id = None
    for pid, pcfg in predios_config(config).items():
        if str(pcfg.get("drone_code", "")).upper() == drone_code:
            predio_id = pid
            break
    if not predio_id:
        raise SystemExit(f"Sin predio para drone_code={drone_code}")

    indices_cfg = config["indices"]
    if index_key not in indices_cfg:
        raise SystemExit(f"Índice no configurado: {index_key}")

    clip_geoms = load_predio_clip_geometries(config)
    geom_union = clip_geoms.get(predio_id)
    if not geom_union:
        raise SystemExit(f"Sin AOI para {predio_id}")

    viz = dict(config.get("raster_visualization", {}))
    viz["overwrite_preview_file"] = True
    clip_aoi_buffer_m = _clip_aoi_buffer_m(viz)
    preview_geom = buffer_geom_wgs84(geom_union, clip_aoi_buffer_m)
    index_cfg = indices_cfg[index_key]
    visual_only = index_cfg.get("visual_only", False)

    static_root = Path(config["sources"]["drone"]["static_root"])
    rasters_dir = static_root / "rasters"
    rasters_dir.mkdir(parents=True, exist_ok=True)
    preview_ext = str(viz.get("preview_format", "WEBP")).lower()
    preview_path = rasters_dir / f"{stem}.{preview_ext}"

    metadata_path = static_root / "metadata.json"
    metadata = load_json_if_exists(metadata_path, {})
    existing_rasters = metadata.get("rasters", {})

    period_key, period_human, year = _label_flat_period(ymd_raw)
    raster_key = f"{predio_id}_{period_key}_{index_key}"
    source_fingerprint = build_file_fingerprint(tiff_path)
    try:
        source_fingerprint["path"] = tiff_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        pass

    if visual_only:
        preview_mode = "thermal_visual_flat" if index_key == "thermal" else "rgb_visual_flat"
        export_signature = build_export_signature(
            {
                "visualization": viz,
                "index": index_key,
                "buffer_m": clip_aoi_buffer_m,
                "preview_geom_sha": preview_clip_geom_digest(geom_union),
                "mode": preview_mode,
                "rgb_preview_mask_revision": RGB_PREVIEW_MASK_REVISION,
                **(
                    {"thermal_preview_revision": THERMAL_PREVIEW_REVISION}
                    if index_key == "thermal"
                    else {}
                ),
            }
        )
        preview_meta = reuse_existing_preview(
            existing_rasters,
            raster_key,
            preview_path,
            export_signature,
            source_fingerprint,
            reuse_if_unchanged=_reuse_previews_allowed(viz),
        )
        if preview_meta is None:
            if index_key == "thermal":
                preview_meta = build_preview_thermal(
                    tiff_path, preview_path, viz, preview_geom, index_cfg, predio_id
                )
            else:
                preview_meta = build_preview_rgb(tiff_path, preview_path, viz, preview_geom)
    else:
        export_signature = build_export_signature(
            {
                "visualization": viz,
                "index": index_key,
                "colormap": index_cfg["colormap"],
                "vmin": index_cfg["vmin"],
                "vmax": index_cfg["vmax"],
                "preview_geom_sha": preview_clip_geom_digest(geom_union),
                "mode": "analytic_preview_flat",
            }
        )
        preview_meta = reuse_existing_preview(
            existing_rasters,
            raster_key,
            preview_path,
            export_signature,
            source_fingerprint,
            reuse_if_unchanged=_reuse_previews_allowed(viz),
        )
        if preview_meta is None:
            preview_meta = build_preview_raster(
                tiff_path,
                preview_path,
                index_cfg["colormap"],
                index_cfg["vmin"],
                index_cfg["vmax"],
                viz,
                preview_geom,
            )

    if preview_meta is None:
        raise SystemExit("No se pudo generar la vista previa")

    outline_rel = f"drone/outlines/{raster_key}.geojson"
    preview_meta = dict(preview_meta)
    preview_meta["cuartel_outlines"] = outline_rel

    existing_rasters[raster_key] = {
        "source": "drone",
        "predio_id": predio_id,
        "index": index_key,
        "year": year,
        "season": period_human,
        "period_key": period_key,
        "visual": preview_meta,
        "analytic_path": tiff_path.relative_to(REPO_ROOT).as_posix(),
        "source_fingerprint": source_fingerprint,
        "export_signature": export_signature,
    }
    metadata["rasters"] = existing_rasters
    metadata["generated_at"] = iso_now()
    if metadata.get("predios", {}).get(predio_id):
        info = metadata["predios"][predio_id]
        periods = {
            rv["period_key"]: rv["season"]
            for rv in existing_rasters.values()
            if rv.get("predio_id") == predio_id
        }
        if periods:
            pkeys = sorted(periods.keys())
            info["n_periods"] = len(periods)
            info["last_period"] = periods[pkeys[-1]]
            info["available_years"] = sorted({int(pk[:4]) for pk in periods if len(pk) >= 4})
            info["status"] = "ready"

    json_dump(metadata_path, metadata)
    try:
        preview_rel = preview_path.relative_to(REPO_ROOT)
    except ValueError:
        preview_rel = preview_path
    print(f"OK {raster_key} -> {preview_rel}")
    try:
        meta_rel = metadata_path.relative_to(REPO_ROOT)
    except ValueError:
        meta_rel = metadata_path
    print(f"metadata -> {meta_rel}")


if __name__ == "__main__":
    main()
