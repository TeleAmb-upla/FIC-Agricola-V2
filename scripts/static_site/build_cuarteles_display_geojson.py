#!/usr/bin/env python3
"""
Genera ``data_static/vectors/cuarteles/cuarteles_display.geojson``: contornos de cuarteles
alineados al píxel visible del WebP dron (intersección geometría maestra × alpha).

Solo para **visualización** en el mapa; el clip de exportación sigue siendo el AOI del predio.

Uso::

    python scripts/static_site/build_cuarteles_display_geojson.py
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

from pipeline_utils import (  # noqa: E402
    bootstrap_proj_environment,
    load_config,
    load_cuartels_by_wetland,
)

bootstrap_proj_environment()

from scripts.data_prep.vectors_paths import STATIC_CUARTELES_DISPLAY, STATIC_CUARTELES_GEOJSON  # noqa: E402

OUTPUT = STATIC_CUARTELES_DISPLAY
STATIC_DRONE = REPO_ROOT / "data_static" / "drone"


def _latest_ndvi_raster(rasters: dict, wetland_id: str) -> dict | None:
    want = wetland_id.lower()
    best = None
    best_key = ""
    for rk, meta in rasters.items():
        if str(meta.get("predio_id") or meta.get("wetland_id") or "").lower() != want:
            continue
        if str(meta.get("index") or "").lower() != "ndvi":
            continue
        vis = meta.get("visual") or {}
        if not vis.get("path") or not vis.get("bounds"):
            continue
        if rk > best_key:
            best_key = rk
            best = meta
    return best


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


def _feature_props(cu: dict, predio_id: str) -> dict:
    return {
        "id_cuartel": cu["id_cuartel"],
        "nom_cuartel": cu.get("nom_cuartel"),
        "cultivo": cu.get("cultivo"),
        "nom_predio": cu.get("nom_predio"),
        "propietario": cu.get("propietario"),
        "superficie": cu.get("superficie"),
        "predio_id": predio_id,
        "fuente": "cuarteles_display",
    }


def sync_cuarteles_geojson(config: dict) -> Path | None:
    src = Path(config.get("cuarteles_path") or config.get("shapefile_path") or "data/vectors/cuarteles/cuarteles.geojson")
    if not src.is_file():
        src = REPO_ROOT / src
    if not src.is_file():
        return None
    dst = STATIC_CUARTELES_GEOJSON
    dst.parent.mkdir(parents=True, exist_ok=True)
    fc = json.loads(src.read_text(encoding="utf-8"))
    try:
        cuartels_by_wetland = load_cuartels_by_wetland(config)
        cid_to_wid: dict[str, str] = {}
        for wetland_id, cuartels in cuartels_by_wetland.items():
            for cu in cuartels:
                cid = str(cu.get("id_cuartel") or "").strip()
                if cid:
                    cid_to_wid[cid] = wetland_id
        for feat in fc.get("features") or []:
            props = feat.setdefault("properties", {})
            cid = str(props.get("id_cuartel") or "").strip()
            if cid and cid in cid_to_wid:
                props["predio_id"] = cid_to_wid[cid]
    except Exception as exc:
        print(f"  [warn] wetland_id en cuarteles.geojson: {exc}")
    dst.write_text(json.dumps(fc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dst


def sync_predios_geojson(config: dict) -> Path | None:
    """Alias legacy."""
    return sync_cuarteles_geojson(config)


def sync_fic_database_csv() -> Path | None:
    src = REPO_ROOT / "data" / "fic_database.csv"
    if not src.is_file():
        return None
    dst = REPO_ROOT / "data_static" / "fic_database.csv"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def build_features(config: dict) -> list[dict]:
    meta_path = STATIC_DRONE / "metadata.json"
    if not meta_path.is_file():
        print("  [skip] falta data_static/drone/metadata.json")
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rasters = meta.get("rasters") or {}
    cuartels_by_wetland = load_cuartels_by_wetland(config)
    features: list[dict] = []

    for wetland_id, cuartels in cuartels_by_wetland.items():
        ref = _latest_ndvi_raster(rasters, wetland_id)
        if not ref:
            for cu in cuartels:
                features.append(
                    {
                        "type": "Feature",
                        "properties": _feature_props(cu, wetland_id),
                        "geometry": cu["geometry"],
                    }
                )
            continue

        vis = ref["visual"]
        bounds = vis["bounds"]
        south, west = bounds[0]
        north, east = bounds[1]
        disp = vis.get("display_size") or vis.get("native_size")
        if not disp or len(disp) < 2:
            continue
        w, h = int(disp[0]), int(disp[1])
        webp_path = REPO_ROOT / "data_static" / vis["path"]
        if not webp_path.is_file():
            print(f"  [warn] {wetland_id}: WebP no encontrado ({vis['path']})")
            continue
        alpha = np.array(Image.open(webp_path))
        if alpha.ndim == 2:
            alpha = np.where(alpha > 0, 255, 0).astype(np.uint8)
        elif alpha.shape[2] >= 4:
            alpha = alpha[..., 3]
        else:
            alpha = np.where(
                (alpha[..., 0] < 250) | (alpha[..., 1] < 250) | (alpha[..., 2] < 250),
                255,
                0,
            ).astype(np.uint8)
        transform = from_bounds(west, south, east, north, w, h)

        alpha_footprint = None
        for cu in cuartels:
            geom = shape(cu["geometry"])
            vis_geom = _visible_geom(geom, alpha, transform)
            if not vis_geom and len(cuartels) == 1:
                if alpha_footprint is None:
                    alpha_footprint = _alpha_footprint_geom(alpha, transform)
                vis_geom = alpha_footprint
            features.append(
                {
                    "type": "Feature",
                    "properties": _feature_props(cu, wetland_id),
                    "geometry": vis_geom if vis_geom else cu["geometry"],
                }
            )
    return features


def main() -> None:
    config = load_config()
    cuarteles_dst = sync_cuarteles_geojson(config)
    if cuarteles_dst:
        print(f"cuarteles.geojson -> {cuarteles_dst.relative_to(REPO_ROOT)}")
    db_dst = sync_fic_database_csv()
    if db_dst:
        print(f"fic_database.csv -> {db_dst.relative_to(REPO_ROOT)}")

    features = build_features(config)
    if not features:
        print("Sin features display (¿exportar dron antes?)")
        return

    fc = {"type": "FeatureCollection", "name": "cuarteles_display", "features": features}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Listo: {OUTPUT.relative_to(REPO_ROOT)} ({len(features)} features)")


if __name__ == "__main__":
    main()
