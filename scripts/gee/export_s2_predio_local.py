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
from shapely.geometry import mapping as shp_mapping, shape

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
STATIC_SITE_DIR = REPO_ROOT / "scripts" / "static_site"
GEE_DIR = Path(__file__).resolve().parent
for p in (str(REPO_ROOT), str(SCRIPTS_DIR), str(STATIC_SITE_DIR), str(GEE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipeline_utils import load_config, ensure_master_aoi, predios_config, load_wetland_clip_geometries
from export_s2 import COMPOSED_INDEX_BANDS
from paths import DEFAULT_S2_WEEKLY_COLLECTION, resolve_ee_cloud_project

DEFAULT_COLLECTION = DEFAULT_S2_WEEKLY_COLLECTION
LOCAL_STEM_RE = re.compile(r"^S2_([A-Za-z0-9_]+)_Y(\d{4})_W(\d{2})$", re.I)
EE_WEEK_BASENAME_RE = re.compile(r"^Y(\d{4})_W(\d{2})$", re.I)
# Mismas bandas que el asset (int16 con escala por banda + clear_pixel_count crudo).
COMPOSED_BANDS = COMPOSED_INDEX_BANDS + ["clear_pixel_count"]
SKIP_PREDIO_CODES = {"LOTE_DEMO"}


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


def discover_ee_weeks(collection_id: str) -> list[tuple[int, int]]:
    """Semanas disponibles en la ImageCollection semanal de Earth Engine."""
    from export_s2 import list_weekly_image_asset_ids

    weeks: set[tuple[int, int]] = set()
    for asset_id in list_weekly_image_asset_ids(collection_id):
        base = asset_id.rstrip("/").split("/")[-1]
        m = EE_WEEK_BASENAME_RE.match(base)
        if m:
            weeks.add((int(m.group(1)), int(m.group(2))))
    return sorted(weeks)


def merge_calendar_weeks(
    dest: Path,
    reference: str,
    collection_id: str,
    *,
    sync_ee: bool,
) -> list[tuple[int, int]]:
    weeks = set(discover_local_weeks(dest, reference))
    if sync_ee:
        weeks.update(discover_ee_weeks(collection_id))
    return sorted(weeks)


def count_local_weeks(dest: Path, code: str) -> int:
    n = 0
    prefix = f"S2_{code.upper()}_Y"
    for p in dest.glob(f"{prefix}*_W*.tif"):
        if LOCAL_STEM_RE.match(p.stem):
            n += 1
    return n


def predios_needing_export(
    dest: Path, reference: str, geoms: dict[str, dict], *, force: bool
) -> list[str]:
    ref = reference.upper()
    ref_n = count_local_weeks(dest, ref)
    if ref_n <= 0:
        return []
    out: list[str] = []
    for code in sorted(geoms):
        if code in SKIP_PREDIO_CODES or code == ref:
            continue
        if force or count_local_weeks(dest, code) < ref_n:
            out.append(code)
    return out


def load_predio_geoms(config: dict) -> dict[str, dict]:
    """Geometría de exportación: unión de cuarteles por predio (no filas sueltas del AOI)."""
    ensure_master_aoi(config)
    clip_geoms = load_wetland_clip_geometries(config)
    out: dict[str, dict] = {}
    for pid, geom_geojson in clip_geoms.items():
        pcfg = predios_config(config).get(pid, {})
        code = str(pcfg.get("s2_code") or pid).strip().upper()
        if code in SKIP_PREDIO_CODES:
            continue
        geom = shape(geom_geojson)
        if geom is None or geom.is_empty:
            continue
        minx, miny, maxx, maxy = geom.bounds
        out[code] = {
            "predio_id": pid,
            "geometry": geom,
            "bounds": (minx, miny, maxx, maxy),
        }
    return out


def asset_basename(year: int, week: int) -> str:
    return f"Y{year}_W{week:02d}"


def stamp_band_descriptions(tif_path: Path, band_names: list[str]) -> None:
    """GEE getDownloadURL no conserva nombres de banda; los escribimos como en G1."""
    import rasterio

    with rasterio.open(tif_path, "r+") as ds:
        if ds.count != len(band_names):
            raise ValueError(
                f"{tif_path.name}: {ds.count} bandas descargadas, esperadas {len(band_names)}"
            )
        for i, name in enumerate(band_names, start=1):
            ds.set_band_description(i, name)


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
    if dry_run:
        print(f"  [dry-run] {out_path.name} <- {asset_id}")
        return
    img = ee.Image(asset_id).select(COMPOSED_BANDS)
    region = geom.bounds(1)

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
    stamp_band_descriptions(tmp, COMPOSED_BANDS)
    tmp.replace(out_path)
    print(f"  OK {out_path.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export S2 semanal recortado por predio a data/sentinel2/")
    ap.add_argument("--collection", default=os.environ.get("GEE_STATS_COLLECTION", DEFAULT_COLLECTION))
    ap.add_argument("--project", default=None)
    ap.add_argument("--reference", default="E_SAZO", help="Predio cuyas semanas locales definen el calendario (s2_code en config.yaml).")
    ap.add_argument(
        "--predios",
        default=None,
        help="Códigos separados por coma. Default: predios en AOI con menos semanas que --reference.",
    )
    ap.add_argument(
        "--all-predios",
        action="store_true",
        help="Exportar todos los predios del AOI (no sólo los incompletos).",
    )
    ap.add_argument("--dest", type=Path, default=REPO_ROOT / "data" / "sentinel2")
    ap.add_argument("--scale", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--sync-ee-weeks",
        action="store_true",
        help="Incluir semanas de la ImageCollection EE (además de las locales del predio referencia).",
    )
    ap.add_argument(
        "--fill-missing-weeks",
        action="store_true",
        help="Exportar semanas faltantes para todos los predios del AOI (incluido el de referencia).",
    )
    args = ap.parse_args()

    config = load_config(REPO_ROOT / "config.yaml")
    project = resolve_ee_cloud_project(args.project)
    if not args.dry_run:
        print(f"Earth Engine - proyecto Cloud: {project}")
        print(f"Colección semanal: {args.collection}")
        ee_init(project)

    weeks = merge_calendar_weeks(
        args.dest, args.reference, args.collection, sync_ee=args.sync_ee_weeks
    )
    if not weeks:
        print(f"No hay semanas locales para S2_{args.reference.upper()}_*", file=sys.stderr)
        sys.exit(1)

    geoms = load_predio_geoms(config)
    if args.predios:
        targets = [p.strip().upper() for p in args.predios.split(",") if p.strip()]
    elif args.fill_missing_weeks or args.all_predios:
        targets = sorted(
            c for c in geoms if c not in SKIP_PREDIO_CODES
        )
    else:
        targets = predios_needing_export(args.dest, args.reference, geoms, force=args.force)

    if not targets:
        print("Todos los predios del AOI ya tienen el calendario completo de semanas.")
        return

    ref_n = len(weeks)
    print(f"Semanas en calendario ({args.reference.upper()}): {ref_n} | rango: {weeks[0]} … {weeks[-1]}")
    print(f"Destino: {args.dest}")
    print(f"Predios a exportar ({len(targets)}): {', '.join(targets)}")

    total_jobs = sum(
        1
        for code in targets
        for year, week in weeks
        if args.force or not (args.dest / f"S2_{code}_Y{year}_W{week:02d}.tif").is_file()
    )
    done = 0

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
            done += 1
            print(f"[{done}/{total_jobs}] {code} {year}-W{week:02d}", flush=True)
            try:
                export_one(
                    args.collection, code, year, week, geom_ee, out_path,
                    scale=args.scale, dry_run=args.dry_run,
                )
            except Exception as exc:
                print(f"  [ERROR] {out_path.name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
