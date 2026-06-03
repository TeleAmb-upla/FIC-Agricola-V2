#!/usr/bin/env python3
"""
Exporta recortes locales por predio desde la ImageCollection semanal S2 en Earth Engine.

Salida: ``data/sentinel2/S2_{PREDIO}_Y{year}_W{week}.tif`` (mismas bandas compuestas que el asset).

Replica el conjunto de semanas ya presentes para un predio de referencia (p. ej. G1) y rellena
los predios nuevos (RCI, RPA, …).

Uso::

    python scripts/gee/export_s2_predio_local.py --dry-run
    python scripts/gee/export_s2_predio_local.py --reference G1
    python scripts/gee/export_s2_predio_local.py --predios RCI,RPA
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import ee
import geopandas as gpd
from shapely.geometry import mapping as shp_mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
for p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipeline_utils import load_config, ensure_master_aoi

DEFAULT_COLLECTION = "projects/ee-javiermedinam/assets/S2_weekly_walpo"
DEFAULT_CLOUD_PROJECT = "ee-javiermedinam"
LOCAL_STEM_RE = re.compile(r"^S2_([A-Za-z0-9]+)_Y(\d{4})_W(\d{2})$", re.I)
COMPOSED_BANDS = ["NDVI", "NDMI", "NDWI", "MNDWI", "GNDVI", "EVI", "SAVI", "MSAVI", "clear_pixel_count"]


def resolve_cloud_project(cli: str | None) -> str:
    if cli and cli.strip():
        return cli.strip()
    for key in ("EE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return DEFAULT_CLOUD_PROJECT


def ee_init(project: str) -> None:
    ee.Initialize(project=project)


def discover_local_weeks(dest: Path, reference: str) -> list[tuple[int, int]]:
    ref = reference.upper()
    weeks: set[tuple[int, int]] = set()
    for p in dest.glob(f"S2_{ref}_Y*_W*.tif"):
        m = LOCAL_STEM_RE.match(p.stem)
        if m:
            weeks.add((int(m.group(2)), int(m.group(3))))
    return sorted(weeks)


def load_predio_geoms(config: dict) -> dict[str, dict]:
    master = ensure_master_aoi(config)
    gdf = gpd.read_file(master).to_crs("EPSG:4326")
    id_col = config["shapefile_id_col"]
    out: dict[str, dict] = {}
    for _, row in gdf.iterrows():
        wid = str(row[id_col]).strip().lower()
        code = wid.upper()
        if wid == "nog":
            code = "NOG"
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        minx, miny, maxx, maxy = geom.bounds
        out[code] = {
            "wetland_id": wid,
            "geometry": geom,
            "bounds": (minx, miny, maxx, maxy),
        }
    return out


def asset_basename(year: int, week: int) -> str:
    return f"Y{year}_W{week:02d}"


def export_one(
    collection_id: str,
    code: str,
    year: int,
    week: int,
    geom: ee.Geometry | None,
    out_path: Path,
    *,
    scale: float,
    dry_run: bool,
) -> None:
    asset_id = f"{collection_id.rstrip('/')}/{asset_basename(year, week)}"
    img = ee.Image(asset_id).select(COMPOSED_BANDS)
    region = geom.bounds(1)
    if dry_run:
        print(f"  [dry-run] {out_path.name}")
        return

    url = img.getDownloadURL(
        {
            "scale": scale,
            "crs": "EPSG:4326",
            "region": region,
            "format": "GEO_TIFF",
        }
    )
    import urllib.request

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tif.part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(out_path)
    print(f"  OK {out_path.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export S2 semanal recortado por predio a data/sentinel2/")
    ap.add_argument("--collection", default=os.environ.get("GEE_STATS_COLLECTION", DEFAULT_COLLECTION))
    ap.add_argument("--project", default=None)
    ap.add_argument("--reference", default="G1", help="Predio cuyas semanas locales definen el calendario.")
    ap.add_argument("--predios", default=None, help="Códigos separados por coma (default: todos en AOI menos referencia).")
    ap.add_argument("--dest", type=Path, default=REPO_ROOT / "data" / "sentinel2")
    ap.add_argument("--scale", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    config = load_config(REPO_ROOT / "config.yaml")
    project = resolve_cloud_project(args.project)
    if not args.dry_run:
        ee_init(project)

    weeks = discover_local_weeks(args.dest, args.reference)
    if not weeks:
        print(f"No hay semanas locales para S2_{args.reference.upper()}_*", file=sys.stderr)
        sys.exit(1)

    geoms = load_predio_geoms(config)
    if args.predios:
        targets = [p.strip().upper() for p in args.predios.split(",") if p.strip()]
    else:
        targets = sorted(c for c in geoms if c != args.reference.upper())

    print(f"Semanas a replicar: {len(weeks)} | destino: {args.dest}")
    print(f"Predios: {', '.join(targets)}")

    for code in targets:
        info = geoms.get(code)
        if not info:
            print(f"[WARN] Sin geometría para {code}", file=sys.stderr)
            continue
        geom_ee = None if args.dry_run else ee.Geometry(shp_mapping(info["geometry"]))
        for year, week in weeks:
            out_path = args.dest / f"S2_{code}_Y{year}_W{week:02d}.tif"
            if out_path.is_file() and not args.force:
                continue
            try:
                if args.dry_run:
                    export_one(
                        args.collection, code, year, week, None, out_path,
                        scale=args.scale, dry_run=True,
                    )
                else:
                    export_one(
                        args.collection, code, year, week, geom_ee, out_path,
                        scale=args.scale, dry_run=False,
                    )
            except Exception as exc:
                print(f"  [ERROR] {out_path.name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
