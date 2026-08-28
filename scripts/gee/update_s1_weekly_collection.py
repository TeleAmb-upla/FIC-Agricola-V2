#!/usr/bin/env python3
"""
Compone Sentinel-1 (C-band SAR GRD) por semana ISO sobre la unión de predios FIC
y lo publica en ``S1_weekly_valpo``; luego exporta a Drive y sincroniza
``data/sentinel1/``.

El AOI **no** es la huella enorme de S2 Valparaíso: es la unión de cuarteles
de ``cuarteles.geojson`` (mismo recorte que el explorador).

Bandas por semana (Float32): VV, VH (γ0 lineal), VV_DB, VH_DB, ANGLE, SCENE_COUNT.

Uso (desde ``fic_agro/``)::

    python scripts/gee/update_s1_weekly_collection.py --dry-run
    python scripts/gee/update_s1_weekly_collection.py
    python scripts/gee/update_s1_weekly_collection.py --assets-only
    python scripts/gee/update_s1_weekly_collection.py --download-only
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ee
import yaml

if __name__ == "__main__" and not __package__:
    _repo = Path(__file__).resolve().parents[2]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    __package__ = "scripts.gee"

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from . import paths
from .drive_sync import run_drive_sync
from .ee_export_helpers import (
    enqueue_exports,
    existing_local_stems,
    filter_basenames_from_start,
    initialize_ee,
    list_weekly_basenames,
    parse_iso_week_start,
    wait_for_export_tasks,
)

S1_COLLECTION_ID = "COPERNICUS/S1_GRD"
TASK_PREFIX = "S1_"
EXPORT_BANDS = ("VV", "VH", "VV_DB", "VH_DB", "ANGLE", "SCENE_COUNT")


def iso_week_specs_thursday_in_calendar_year(
    calendar_year: int,
    end_exclusive_ms: int,
) -> list[dict]:
    ms_week = 7 * 24 * 60 * 60 * 1000
    out: dict[tuple[int, int], dict] = {}
    cur = datetime(calendar_year, 1, 1, tzinfo=timezone.utc)
    end_day = datetime(calendar_year, 12, 31, tzinfo=timezone.utc)
    while cur <= end_day:
        iso_year, iso_week, weekday = cur.isocalendar()
        if weekday == 4:
            monday = cur - timedelta(days=3)
            monday_ms = int(monday.timestamp() * 1000)
            if monday_ms + ms_week <= end_exclusive_ms:
                out.setdefault(
                    (iso_year, iso_week),
                    {
                        "iso_year": iso_year,
                        "iso_week": iso_week,
                        "monday_ms": monday_ms,
                        "calendar_year": calendar_year,
                    },
                )
        cur += timedelta(days=1)
    return [out[k] for k in sorted(out)]


def export_asset_basename(iso_year: int, iso_week: int) -> str:
    return f"Y{iso_year}_W{iso_week:02d}"


def end_exclusive_last_complete_iso_week() -> ee.Date:
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    monday = datetime.fromisocalendar(iso.year, iso.week, 1).replace(tzinfo=timezone.utc)
    return ee.Date(int(monday.timestamp() * 1000))


def aoi_from_predio_cuarteles() -> ee.Geometry:
    """Unión de cuarteles FIC (no la IC S2 regional)."""
    from scripts.static_site.pipeline_utils import load_config, load_predio_clip_geometries

    clips = load_predio_clip_geometries(load_config())
    geoms = [shape(g) for g in clips.values() if g]
    if not geoms:
        raise RuntimeError("No hay geometrías de cuarteles para definir el AOI S1.")
    union = unary_union(geoms)
    geo = mapping(union)
    return ee.Geometry(geo, geodesic=False).simplify(maxError=20)


def list_existing_asset_ids(collection_id: str) -> set[str]:
    prefix = collection_id.rstrip("/")
    try:
        result = ee.data.listAssets({"parent": prefix})
    except ee.EEException:
        return set()
    return {
        (item.get("id") or "").rstrip("/")
        for item in result.get("assets", [])
        if item.get("id")
    }


def ensure_collection(collection_id: str, dry_run: bool) -> None:
    prefix = collection_id.rstrip("/")
    try:
        ee.data.getAsset(prefix)
        return
    except Exception:
        pass
    if dry_run:
        print(f"[dry-run] se crearía la ImageCollection {prefix}")
        return
    print(f"Creando ImageCollection {prefix}…")
    ee.data.createAsset({"type": "IMAGE_COLLECTION"}, prefix)


def s1_base_collection(aoi: ee.Geometry, orbit_pass: str) -> ee.ImageCollection:
    col = (
        ee.ImageCollection(S1_COLLECTION_ID)
        .filterBounds(aoi)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )
    if orbit_pass in ("ASCENDING", "DESCENDING"):
        col = col.filter(ee.Filter.eq("orbitProperties_pass", orbit_pass))
    return col


def to_gamma0_linear(image: ee.Image) -> ee.Image:
    angle = image.select("angle")
    cos_inc = angle.multiply(3.141592653589793 / 180.0).cos()
    sigma0 = ee.Image.constant(10.0).pow(image.select(["VV", "VH"]).divide(10.0))
    gamma0 = sigma0.divide(cos_inc).rename(["VV", "VH"])
    return gamma0.addBands(angle.rename("ANGLE")).copyProperties(
        image, ["system:time_start"]
    )


def weekly_image(
    base: ee.ImageCollection,
    aoi: ee.Geometry,
    monday_ms: int,
    iso_year: int,
    iso_week: int,
) -> ee.Image:
    start = ee.Date(monday_ms)
    end = start.advance(7, "day")
    week_col = base.filterDate(start, end).map(to_gamma0_linear)
    mean = week_col.select(["VV", "VH", "ANGLE"]).mean()
    count = week_col.select("VV").count().rename("SCENE_COUNT")
    db = (
        mean.select(["VV", "VH"])
        .log10()
        .multiply(10.0)
        .rename(["VV_DB", "VH_DB"])
    )
    return (
        mean.addBands(db)
        .addBands(count)
        .select(list(EXPORT_BANDS))
        .toFloat()
        .clip(aoi)
        .set(
            {
                "year": iso_year,
                "week": iso_week,
                "system:time_start": start.millis(),
            }
        )
    )


def week_scene_count(base: ee.ImageCollection, monday_ms: int) -> ee.Number:
    start = ee.Date(monday_ms)
    return base.filterDate(start, start.advance(7, "day")).size()


def enqueue_missing_weeks(
    *,
    collection_id: str,
    aoi: ee.Geometry,
    calendar_years: list[int],
    end_exclusive: ee.Date,
    orbit_pass: str,
    dry_run: bool,
    force: bool,
    iso_week_start: tuple[int, int] | None,
) -> int:
    existing = set() if force else list_existing_asset_ids(collection_id)
    print(f"Assets existentes en colección: {len(existing)}")
    if iso_week_start:
        print(f"iso_week_start: Y{iso_week_start[0]}_W{iso_week_start[1]:02d}")

    base = s1_base_collection(aoi, orbit_pass)
    end_ms = int(end_exclusive.millis().getInfo())
    prefix = collection_id.rstrip("/")

    pending: list[dict] = []
    for cy in calendar_years:
        specs = iso_week_specs_thursday_in_calendar_year(cy, end_ms)
        missing = []
        for spec in specs:
            key = (int(spec["iso_year"]), int(spec["iso_week"]))
            if iso_week_start and key < iso_week_start:
                continue
            asset_id = f"{prefix}/{export_asset_basename(*key)}"
            if force or asset_id not in existing:
                missing.append(spec)
        if missing:
            pending.extend(missing)
            print(f"  {cy}: {len(missing)} semana(s) pendientes de {len(specs)}")
        else:
            print(f"  {cy}: al día ({len(specs)} semanas)")

    if not pending:
        print("No hay semanas nuevas por exportar a la ImageCollection.")
        return 0

    counts = ee.List(
        [week_scene_count(base, int(s["monday_ms"])) for s in pending]
    ).getInfo()

    n_enq = 0
    for spec, n_scenes in zip(pending, counts):
        iso_year, iso_week = int(spec["iso_year"]), int(spec["iso_week"])
        base_name = export_asset_basename(iso_year, iso_week)
        asset_id = f"{prefix}/{base_name}"
        if not n_scenes:
            print(f"  ISO {iso_year}-W{iso_week:02d}: sin escenas S1, omitir")
            continue
        if dry_run:
            print(f"  [dry-run] {base_name} ({n_scenes} escena/s) → {asset_id}")
            n_enq += 1
            continue
        img = weekly_image(base, aoi, int(spec["monday_ms"]), iso_year, iso_week)
        task = ee.batch.Export.image.toAsset(
            image=img,
            description=f"{TASK_PREFIX}{iso_year}W{iso_week:02d}",
            assetId=asset_id,
            region=aoi,
            scale=paths.DEFAULT_SCALE_M,
            maxPixels=1e13,
        )
        task.start()
        print(f"  encolado {base_name} ({n_scenes} escena/s)")
        n_enq += 1

    return n_enq


def wait_asset_tasks(poll_seconds: float = 45.0, max_wait_h: float = 6.0) -> None:
    deadline = time.monotonic() + max_wait_h * 3600
    print(f"\nEsperando tareas toAsset (cada {poll_seconds:g}s, máx {max_wait_h:g}h)…", flush=True)
    while time.monotonic() < deadline:
        active = []
        for t in ee.batch.Task.list()[:80]:
            info = t.status()
            desc = str(info.get("description") or "")
            state = info.get("state")
            st = state.value if hasattr(state, "value") else str(state)
            if desc.startswith(TASK_PREFIX) and st in ("READY", "RUNNING", "UNSUBMITTED"):
                active.append(desc)
        if not active:
            print("  Todas las tareas S1_* toAsset terminaron.", flush=True)
            return
        print(f"  Activas {len(active)}: {active[0]}…", flush=True)
        time.sleep(max(10.0, poll_seconds))
    raise TimeoutError("Tiempo máximo de espera de tareas toAsset agotado.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Actualizar IC S1 weekly FIC (unión predios) → Drive → data/sentinel1/"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assets-only", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=45.0)
    parser.add_argument("--project", default="", help="Proyecto Cloud EE (default: teleambagr).")
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument(
        "--orbit",
        choices=("ALL", "ASCENDING", "DESCENDING"),
        default="ALL",
    )
    args = parser.parse_args(argv)

    if args.assets_only and args.download_only:
        print("Use solo uno de --assets-only o --download-only.", file=sys.stderr)
        sys.exit(2)

    collection = paths.GEE_S1_COLLECTION
    drive_folder = paths.DRIVE_S1_EXPORT_FOLDER
    dest_dir = paths.REPO_S1_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    root_cfg: dict = {}
    if paths.CONFIG_PATH.is_file():
        with open(paths.CONFIG_PATH, encoding="utf-8") as handle:
            root_cfg = yaml.safe_load(handle) or {}
    iso_start = parse_iso_week_start(root_cfg.get("iso_week_start"))
    start_y = args.start_year
    if start_y is None:
        start_y = int(root_cfg.get("year_start") or 2018)
    end_y = args.end_year or datetime.now(timezone.utc).year
    years = list(range(start_y, end_y + 1))

    print("=== Actualizar S1 semanal FIC Agro ===")
    print(f"Colección : {collection}")
    print(f"Drive     : {drive_folder}")
    print(f"Local     : {dest_dir}")
    print(f"Años      : {years}")
    print(f"Órbita    : {args.orbit}")
    if iso_start:
        print(f"Inicio    : Y{iso_start[0]}_W{iso_start[1]:02d}")

    initialize_ee((args.project or paths.GEE_CLOUD_PROJECT or "").strip())

    end_ex = end_exclusive_last_complete_iso_week()
    print(f"Fin exclusivo (lun semana actual UTC): {end_ex.format('YYYY-MM-dd').getInfo()}")

    if not args.download_only:
        ensure_collection(collection, args.dry_run)
        aoi = aoi_from_predio_cuarteles()
        print("AOI: unión de cuarteles FIC (no huella S2 Valparaíso)")
        n_enq = enqueue_missing_weeks(
            collection_id=collection,
            aoi=aoi,
            calendar_years=years,
            end_exclusive=end_ex,
            orbit_pass=args.orbit,
            dry_run=args.dry_run,
            force=args.force,
            iso_week_start=iso_start,
        )
        print(f"\nSemanas encoladas/simuladas: {n_enq}")
        if n_enq and not args.dry_run and not args.no_wait:
            wait_asset_tasks(poll_seconds=args.poll_seconds)
        elif n_enq and args.no_wait:
            print("(--no-wait: no se espera toAsset)")

    if args.assets_only:
        print("\nListo (--assets-only).")
        return

    print("\n=== Exportar semanas faltantes a Drive ===")
    basenames = filter_basenames_from_start(list_weekly_basenames(collection), iso_start)
    skip = existing_local_stems(dest_dir)
    print(f"En colección (filtrada): {len(basenames)} | Ya en local: {len(skip)}")

    if args.dry_run:
        missing = [b for b in basenames if b not in skip]
        print(f"[dry-run] a exportar={len(missing)}")
        for b in missing[:20]:
            print(f"  [dry-run] {b}.tif")
        if len(missing) > 20:
            print(f"  … y {len(missing) - 20} más")
        print("\nListo (dry-run).")
        return

    tasks = enqueue_exports(
        collection,
        basenames,
        drive_folder=drive_folder,
        scale=paths.DEFAULT_SCALE_M,
        dest_dir=dest_dir,
        skip_stems=skip,
        dry_run=False,
    )
    print(f"Tareas toDrive encoladas: {len(tasks)}")
    if tasks and not args.no_wait:
        wait_for_export_tasks(tasks, poll_seconds=args.poll_seconds)

    print("\n=== Sincronizar Drive → local (incremental) ===")
    run_drive_sync(["s1"], dry_run=False, full_replace=False)

    print("\nListo.")


if __name__ == "__main__":
    main()
