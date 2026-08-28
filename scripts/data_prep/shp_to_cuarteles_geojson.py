#!/usr/bin/env python3
"""
Convierte ``data/vectors/cuarteles/cuarteles.shp`` → ``cuarteles.geojson`` (WGS84).

Uso::

    python scripts/data_prep/shp_to_cuarteles_geojson.py
    python scripts/data_prep/shp_to_cuarteles_geojson.py --sync
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data_prep.build_predios_geojson import (  # noqa: E402
    GEOJSON_PROP_COLS,
    _normalize_shape_props,
    _to_2d,
)
from scripts.data_prep.cuartel_areas import superficie_from_geometry
from scripts.data_prep.vectors_paths import CUARTELES_GEOJSON, CUARTELES_ROOT

DEFAULT_SHP = CUARTELES_ROOT / "cuarteles.shp"


def build_features_from_shp(shp_path: Path) -> list[dict]:
    if not shp_path.is_file():
        raise FileNotFoundError(shp_path)

    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    features: list[dict] = []
    for _, row in gdf.iterrows():
        geom = _to_2d(row.geometry)
        if geom is None or geom.is_empty:
            continue
        props = _normalize_shape_props(row.to_dict())
        props["id_cuartel"] = str(props.get("id_cuartel") or "").strip().lower()
        if props.get("poligono_cuartel"):
            props["poligono_cuartel"] = str(props["poligono_cuartel"]).strip().lower()
        if str(props.get("plot_id") or "").lower() in {"nan", "none"}:
            props["plot_id"] = ""
        if cid := props.get("id_cuartel"):
            if cid == "c00030" and str(props.get("nom_predio") or "").strip().lower() == "trinidad":
                props["predio_id"] = "v_fernandez"
                props["poligono_vuelo"] = "FIC-V-FERNANDEZ-X"
        props["superficie"] = superficie_from_geometry(geom)
        props["fuente"] = str(props.get("fuente") or shp_path.relative_to(REPO_ROOT).as_posix())
        feature_props = {col: props.get(col, "") for col in GEOJSON_PROP_COLS}
        features.append(
            {
                "type": "Feature",
                "properties": feature_props,
                "geometry": mapping(geom),
            }
        )

    features.sort(key=lambda f: f["properties"].get("id_cuartel", ""))
    return features


def write_geojson(features: list[dict], output: Path = CUARTELES_GEOJSON) -> None:
    fc = {"type": "FeatureCollection", "name": "cuarteles", "features": features}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="SHP de cuarteles → cuarteles.geojson")
    ap.add_argument("--shp", type=Path, default=DEFAULT_SHP, help="Ruta al .shp")
    ap.add_argument("--output", type=Path, default=CUARTELES_GEOJSON)
    ap.add_argument("--sync", action="store_true", help="Ejecutar sync_predios_master.py después")
    args = ap.parse_args()

    features = build_features_from_shp(args.shp.resolve())
    if not features:
        raise SystemExit("No se generó ningún polígono desde el shapefile.")

    write_geojson(features, args.output.resolve())
    print(f"Listo: {args.output.relative_to(REPO_ROOT)} ({len(features)} cuarteles)")

    if args.sync:
        sync = REPO_ROOT / "scripts" / "data_prep" / "sync_predios_master.py"
        subprocess.run([sys.executable, str(sync)], check=True, cwd=REPO_ROOT)


if __name__ == "__main__":
    main()
