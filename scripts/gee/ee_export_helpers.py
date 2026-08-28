"""Helpers compartidos: listar IC semanal Y*_W*, encolar toDrive y esperar tareas."""
from __future__ import annotations

import re
import time
from pathlib import Path

import ee

from . import paths

_WEEKLY_BASENAME_RE = re.compile(r"^Y(\d{4})_W(\d{2})$", re.IGNORECASE)


def parse_iso_week_start(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    s = str(raw).strip().upper().replace("_", "-")
    m = re.match(r"^Y?(\d{4})-?W(\d{1,2})$", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def filter_basenames_from_start(basenames: list[str], start: tuple[int, int] | None) -> list[str]:
    if not start:
        return basenames
    sy, sw = start
    out: list[str] = []
    for b in basenames:
        m = _WEEKLY_BASENAME_RE.match(b)
        if not m:
            continue
        y, w = int(m.group(1)), int(m.group(2))
        if (y, w) >= (sy, sw):
            out.append(b)
    return out


def initialize_ee(cloud_project: str) -> str:
    project = (cloud_project or paths.resolve_ee_cloud_project()).strip()
    try:
        ee.Initialize(project=project)
        print(f"Earth Engine inicializado con proyecto Cloud: {project}")
        return project
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo inicializar Earth Engine con el proyecto Cloud «{project}».\n"
            f"  earthengine authenticate --project={project} --force\n"
            f"  Error: {exc}"
        ) from exc


def _image_for_drive_export(img: ee.Image) -> ee.Image:
    return ee.Image(img).toDouble()


def list_weekly_basenames(collection_id: str) -> list[str]:
    prefix = collection_id.rstrip("/")
    try:
        result = ee.data.listAssets({"parent": prefix})
    except ee.EEException as exc:
        raise RuntimeError(f"No se pudo listar assets en {prefix}: {exc}") from exc

    basenames: set[str] = set()
    for item in result.get("assets", []):
        asset_id = (item.get("id") or "").rstrip("/")
        base = asset_id.split("/")[-1]
        if _WEEKLY_BASENAME_RE.match(base):
            basenames.add(base)

    def _sort_key(b: str) -> tuple[int, int]:
        m = _WEEKLY_BASENAME_RE.match(b)
        assert m is not None
        return int(m.group(1)), int(m.group(2))

    return sorted(basenames, key=_sort_key)


def existing_local_stems(dest_dir: Path) -> set[str]:
    stems: set[str] = set()
    if not dest_dir.is_dir():
        return stems
    for p in dest_dir.iterdir():
        if p.is_file() and p.suffix.lower() in (".tif", ".tiff"):
            stems.add(p.stem)
    return stems


def wait_for_export_tasks(
    tasks: list[ee.batch.Task],
    *,
    poll_seconds: float = 30.0,
) -> None:
    if not tasks:
        return
    n = len(tasks)
    poll_seconds = max(5.0, poll_seconds)
    print(f"\nEsperando {n} tarea(s) GEE → Drive (cada {poll_seconds:g}s)…", flush=True)
    start = time.monotonic()
    while True:
        if not any(t.active() for t in tasks):
            break
        elapsed = int(time.monotonic() - start)
        active = sum(1 for t in tasks if t.active())
        print(f"  {n - active}/{n} completadas; {active} activas ({elapsed}s)…", flush=True)
        time.sleep(poll_seconds)

    failed: list[str] = []
    for t in tasks:
        info = t.status()
        st = info.get("state")
        st_s = st.value if hasattr(st, "value") else str(st)
        if st_s != "COMPLETED":
            failed.append(f"{st_s}: {info.get('error_message', '')}")
    if failed:
        raise RuntimeError("Exportaciones fallidas:\n" + "\n".join(failed[:20]))


def enqueue_exports(
    collection_id: str,
    basenames: list[str],
    *,
    drive_folder: str,
    scale: float,
    dest_dir: Path,
    skip_stems: set[str],
    dry_run: bool,
    desc_prefix: str = "FIC_S1",
) -> list[ee.batch.Task]:
    tasks: list[ee.batch.Task] = []
    prefix = collection_id.rstrip("/")

    for base in basenames:
        stem = base
        if stem in skip_stems:
            print(f"  [omitir] {stem}.tif (ya en {dest_dir})")
            continue

        asset_id = f"{prefix}/{base}"
        img = ee.Image(asset_id)
        region = img.geometry().bounds()

        if dry_run:
            print(f"  [dry-run] {stem}.tif → Drive/{drive_folder}")
            continue

        desc = f"{desc_prefix}_{stem}"[:100]
        task = ee.batch.Export.image.toDrive(
            image=_image_for_drive_export(img),
            description=desc,
            folder=drive_folder,
            fileNamePrefix=stem,
            region=region,
            scale=scale,
            crs="EPSG:4326",
            maxPixels=1e13,
            fileFormat="GeoTIFF",
        )
        task.start()
        tasks.append(task)
        print(f"  Encolado: {stem}.tif → Drive/{drive_folder}")

    return tasks
