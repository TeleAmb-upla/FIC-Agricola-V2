#!/usr/bin/env python3
"""
Genera contornos por cuartel alineados al alpha de cada WebP dron.

Salida: ``data_static/drone/outlines/{raster_key}.geojson`` (uno por entrada en metadata).

Uso::

    python scripts/static_site/build_drone_raster_cuartel_outlines.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.features import geometry_mask, shapes
from rasterio.transform import from_bounds
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "static_site"
for p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipeline_utils import bootstrap_proj_environment, load_config, load_cuartels_by_wetland  # noqa: E402

bootstrap_proj_environment()

STATIC_DRONE = REPO_ROOT / "data_static" / "drone"
OUT_DIR = STATIC_DRONE / "outlines"


def _alpha_footprint_geom(alpha: np.ndarray, transform) -> dict | None:
    visible = (alpha > 128).astype(np.uint8)
    if not visible.any():
        return None
    parts = []
    for geom, val in shapes(visible, transform=transform, connectivity=8):
        if val != 1:
            continue
        shp = shape(geom)
        if shp.is_empty or shp.area <= 0:
            continue
        parts.append(shp)
    if not parts:
        return None
    merged = unary_union(parts)
    if merged.is_empty:
        return None
    return mapping(merged)


def _visible_geom(cuartel_geom, alpha: np.ndarray, transform) -> dict | None:
    cmask = geometry_mask(
        [mapping(cuartel_geom)],
        out_shape=alpha.shape,
        transform=transform,
        invert=True,
    )
    visible = (cmask & (alpha > 128)).astype(np.uint8)
    if not visible.any():
        return None
    parts = []
    for geom, val in shapes(visible, transform=transform, connectivity=8):
        if val != 1:
            continue
        shp = shape(geom)
        if shp.is_empty or shp.area <= 0:
            continue
        parts.append(shp)
    if not parts:
        return None
    merged = unary_union(parts)
    if merged.is_empty:
        return None
    return mapping(merged)


def _feature_props(cu: dict, wetland_id: str, raster_key: str) -> dict:
    return {
        "id_cuartel": cu["id_cuartel"],
        "nom_cuartel": cu.get("nom_cuartel"),
        "cultivo": cu.get("cultivo"),
        "nom_predio": cu.get("nom_predio"),
        "propietario": cu.get("propietario"),
        "superficie": cu.get("superficie"),
        "wetland_id": wetland_id,
        "raster_key": raster_key,
        "fuente": "drone_webp_alpha",
    }


def outlines_for_raster(
    raster_key: str,
    meta: dict,
    cuartels_by_wetland: dict[str, list[dict]],
) -> list[dict] | None:
    vis = meta.get("visual") or {}
    path = vis.get("path")
    bounds = vis.get("bounds")
    if not path or not bounds:
        return None
    wetland_id = str(meta.get("wetland_id") or "").lower()
    cuartels = cuartels_by_wetland.get(wetland_id) or []
    if not cuartels:
        return None

    disp = vis.get("display_size") or vis.get("native_size")
    if not disp or len(disp) < 2:
        return None
    w, h = int(disp[0]), int(disp[1])
    webp_path = REPO_ROOT / "data_static" / path
    if not webp_path.is_file():
        print(f"  [skip] {raster_key}: WebP no encontrado ({path})")
        return None

    south, west = bounds[0]
    north, east = bounds[1]
    img = Image.open(webp_path)
    arr = np.array(img)
    if arr.ndim == 2:
        alpha = np.where(arr > 0, 255, 0).astype(np.uint8)
    elif arr.shape[2] >= 4:
        alpha = arr[..., 3]
    else:
        alpha = np.where(
            (arr[..., 0] < 250) | (arr[..., 1] < 250) | (arr[..., 2] < 250),
            255,
            0,
        ).astype(np.uint8)
    transform = from_bounds(west, south, east, north, w, h)

    features: list[dict] = []
    alpha_footprint = None
    for cu in cuartels:
        geom = shape(cu["geometry"])
        vis_geom = _visible_geom(geom, alpha, transform)
        if not vis_geom and len(cuartels) == 1:
            if alpha_footprint is None:
                alpha_footprint = _alpha_footprint_geom(alpha, transform)
            vis_geom = alpha_footprint
        if not vis_geom:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": _feature_props(cu, wetland_id, raster_key),
                "geometry": vis_geom,
            }
        )
    return features


def main() -> None:
    meta_path = STATIC_DRONE / "metadata.json"
    if not meta_path.is_file():
        print("Falta data_static/drone/metadata.json")
        return

    config = load_config()
    cuartels_by_wetland = load_cuartels_by_wetland(config)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rasters = meta.get("rasters") or {}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    for raster_key, rmeta in sorted(rasters.items()):
        features = outlines_for_raster(raster_key, rmeta, cuartels_by_wetland)
        if not features:
            skipped += 1
            continue
        out_path = OUT_DIR / f"{raster_key}.geojson"
        fc = {
            "type": "FeatureCollection",
            "name": f"drone_outlines_{raster_key}",
            "features": features,
        }
        out_path.write_text(json.dumps(fc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rel = f"drone/outlines/{raster_key}.geojson"
        vis = rmeta.setdefault("visual", {})
        vis["cuartel_outlines"] = rel
        written += 1

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Listo: {written} archivos en {OUT_DIR.relative_to(REPO_ROOT)} ({skipped} omitidos)")


if __name__ == "__main__":
    main()
