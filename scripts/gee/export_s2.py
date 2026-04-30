#!/usr/bin/env python3
"""
Exporta mosaicos semanales Sentinel-2 a Earth Engine Assets: Cloud Score+ y, por pixel,
solo bandas de índices compuestos (NDVI, NDMI, NDWI, …) más ``clear_pixel_count`` —
sin bandas espectrales crudas del sensor.

Autenticación: ``earthengine authenticate``. Proyecto Cloud por defecto: ``ee-javiermedinam``
(``EE_CLOUD_PROJECT`` / ``--project``).

Salida bajo ``--export-prefix``; se crea la ImageCollection vacía si falta (salvo
``--skip-ensure-destination``). Por defecto **no** re-encola semanas cuyo asset hijo ya exista
(``--force`` para ignorar y volver a exportar todo).

Años: ``--year`` o ``--start-year`` / ``--end-year``; si no, ``GEE_START_YEAR`` /
``DEFAULT_START_YEAR``, y para el fin ``GEE_END_YEAR`` o ``DEFAULT_END_YEAR`` en el código
(se limita igual al año calendario actual en tiempo de ejecución).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import ee

DEFAULT_AOI_ASSET = "projects/teleambagr/assets/vectores/Area_Agricola_Reg_Valpo_2025"
DEFAULT_EXPORT_PREFIX = "projects/ee-javiermedinam/assets/S2_weekly_walpo"

# Proyecto Google Cloud para ``ee.Initialize(project=...)`` (API / facturación EE).
DEFAULT_CLOUD_PROJECT = "ee-javiermedinam"

DEFAULT_START_YEAR = 2026
DEFAULT_END_YEAR = 2026


def resolve_cloud_project(cli_project: str | None) -> str:
    """Orden: argumento ``--project``, env ``EE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_PROJECT``, valor por defecto."""
    if cli_project is not None and cli_project.strip() != "":
        return cli_project.strip()
    for key in ("EE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return DEFAULT_CLOUD_PROJECT


def ee_initialize(project: str) -> None:
    ee.Initialize(project=project)


def _asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException:
        return False


def list_child_asset_ids(parent_id: str) -> set[str]:
    """
    IDs completos de assets hijos bajo ``parent_id`` (ImageCollection o carpeta).
    Si no hay permiso o el padre no existe, devuelve conjunto vacío.
    """
    parent_id = parent_id.strip().rstrip("/")
    seen: set[str] = set()
    page_token: str | None = None
    while True:
        req: dict = {"parent": parent_id}
        if page_token:
            req["pageToken"] = page_token
        try:
            resp = ee.data.listAssets(req)
        except ee.EEException:
            return seen
        for a in resp.get("assets") or []:
            aid = a.get("name") or a.get("id")
            if isinstance(aid, str) and aid:
                seen.add(aid.rstrip("/"))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return seen


def load_existing_export_keys(export_prefix: str) -> tuple[set[str], int]:
    """Conjunto de claves para comprobar duplicados, y número de hijos devueltos por listAssets."""
    full = list_child_asset_ids(export_prefix.strip().rstrip("/"))
    keys: set[str] = set(full)
    for f in full:
        keys.add(f.rstrip("/").split("/")[-1])
    return keys, len(full)


def export_key_exists(asset_id: str, keys: set[str]) -> bool:
    if asset_id in keys:
        return True
    return asset_id.rstrip("/").split("/")[-1] in keys


def ensure_export_destination(export_prefix: str, *, dry_run: bool) -> None:
    """
    Crea bajo ``projects/<cloud>/assets/`` las carpetas (FOLDER) que falten y, al final del
    prefijo, un asset vacío IMAGE_COLLECTION si no existe. Es idempotente.
    Los mosaicos se exportan como imágenes hijas: ``<export_prefix>/Yxxxx_Www``.
    """
    export_prefix = export_prefix.strip().rstrip("/")
    parts = export_prefix.split("/")
    if len(parts) < 4 or parts[0] != "projects":
        raise ValueError(
            "export_prefix debe ser projects/<CLOUD_PROJECT>/assets/... "
            f"(recibido: {export_prefix!r})"
        )
    try:
        assets_idx = parts.index("assets")
    except ValueError as exc:
        raise ValueError("export_prefix debe incluir el segmento .../assets/...") from exc

    tail = parts[assets_idx + 1 :]
    if not tail:
        raise ValueError("Debe haber al menos un nombre bajo projects/.../assets/")

    cumulative = "/".join(parts[: assets_idx + 1])

    for i, segment in enumerate(tail):
        cumulative = f"{cumulative}/{segment}"
        last = i == len(tail) - 1

        if _asset_exists(cumulative):
            info = ee.data.getAsset(cumulative)
            kind = str(info.get("type", "")).upper()
            if last:
                if kind == "IMAGE_COLLECTION":
                    print(f"Colección destino ya existe: {cumulative}")
                elif kind == "FOLDER":
                    print(
                        f"Destino existe como carpeta (OK para exportar imágenes): {cumulative}"
                    )
                else:
                    raise ValueError(
                        f"El asset {cumulative} ya existe como {kind}; "
                        "usa otro export_prefix o eliminalo en Earth Engine."
                    )
            elif kind not in ("FOLDER", "IMAGE_COLLECTION"):
                raise ValueError(
                    f"No se puede usar {cumulative} como carpeta padre (tipo {kind})."
                )
            continue

        if dry_run:
            typ = "IMAGE_COLLECTION" if last else "FOLDER"
            print(f"[dry-run] crearía {typ}: {cumulative}")
            continue

        if last:
            ee.data.createAsset({"type": "IMAGE_COLLECTION"}, cumulative)
            print(f"Creada ImageCollection vacía: {cumulative}")
        else:
            ee.data.createAsset({"type": "FOLDER"}, cumulative)
            print(f"Creada carpeta: {cumulative}")


def mask_and_scale(image: ee.Image) -> ee.Image:
    mask = image.select("cs").gte(0.6)
    clear = mask.rename("clear_pixel_count")
    return (
        image.updateMask(mask)
        .divide(10000)
        .addBands(clear)
        .copyProperties(image, ["system:time_start", "system:index"])
    )


def add_indices(img: ee.Image) -> ee.Image:
    ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndmi = img.normalizedDifference(["B8", "B11"]).rename("NDMI")
    ndwi = img.normalizedDifference(["B3", "B8"]).rename("NDWI")
    mndwi = img.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    gndvi = img.normalizedDifference(["B8", "B3"]).rename("GNDVI")

    evi = img.expression(
        "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
        {"NIR": img.select("B8"), "RED": img.select("B4"), "BLUE": img.select("B2")},
    ).rename("EVI")

    savi = img.expression(
        "((NIR - RED) / (NIR + RED + 0.5)) * 1.5",
        {"NIR": img.select("B8"), "RED": img.select("B4")},
    ).rename("SAVI")

    msavi = img.expression(
        "(2 * NIR + 1 - sqrt(pow((2 * NIR + 1), 2) - 8 * (NIR - RED))) / 2",
        {"NIR": img.select("B8"), "RED": img.select("B4")},
    ).rename("MSAVI")

    return img.addBands([ndvi, ndmi, ndwi, mndwi, gndvi, evi, savi, msavi])


# Salida de exportación: solo estas bandas (índices compuestos arriba), sin B1…B12 ni otras del sensor.
COMPOSED_INDEX_BANDS = ["NDVI", "NDMI", "NDWI", "MNDWI", "GNDVI", "EVI", "SAVI", "MSAVI"]


def build_weekly_collection(
    aoi: ee.Geometry,
    end_date: ee.Date,
    start_year: int,
    end_year: int,
) -> ee.ImageCollection:
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi)
    cs_plus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    s2_with_clouds = s2.linkCollection(cs_plus, ["cs"])

    processed = (
        s2_with_clouds.filterDate("2017-01-01", end_date).map(mask_and_scale).map(add_indices)
    )

    years = ee.List.sequence(start_year, end_year)
    weeks = ee.List.sequence(0, 51)

    def per_year(y: ee.ComputedObject) -> ee.List:
        y = ee.Number(y)
        year_start = ee.Date.fromYMD(y, 1, 1)

        def per_week(w: ee.ComputedObject) -> ee.Image:
            w = ee.Number(w)
            start = year_start.advance(w, "week")
            end = start.advance(1, "week")
            week_col = processed.filterDate(start, end).filterBounds(aoi)
            idx_median = week_col.select(COMPOSED_INDEX_BANDS).median()
            clear_sum = week_col.select("clear_pixel_count").sum().rename("clear_pixel_count")
            return idx_median.addBands(clear_sum).set(
                {
                    "week": w.add(1),
                    "year": y,
                    "system:time_start": start.millis(),
                    "n_images": week_col.size(),
                }
            )

        return weeks.map(per_week)

    weekly_mosaics = years.map(per_year).flatten()
    final_collection = (
        ee.ImageCollection(weekly_mosaics)
        .filter(ee.Filter.gt("n_images", 0))
        .map(lambda im: ee.Image(im).clip(aoi).float())
    )
    return final_collection


def export_year(
    final_collection: ee.ImageCollection,
    aoi: ee.Geometry,
    export_prefix: str,
    year: int,
    *,
    dry_run: bool,
    existing_ids: set[str],
    skip_existing: bool,
) -> tuple[int, int]:
    """Devuelve (encoladas_o_simuladas, omitidas_por_existir)."""
    col = final_collection.filter(ee.Filter.eq("year", year)).sort("week")
    size = col.size().getInfo()
    img_list = col.toList(size)
    n_enq = 0
    n_skip = 0
    for i in range(size):
        img = ee.Image(img_list.get(i))
        week = img.get("week").getInfo()
        desc = f"S2_W{week}_{year}"
        asset_id = f"{export_prefix}/Y{year}_W{week}"
        if skip_existing and export_key_exists(asset_id, existing_ids):
            print(f"  [ya existe, omitir] {asset_id}")
            n_skip += 1
            continue
        if dry_run:
            print(f"  [dry-run] {desc} -> {asset_id}")
            n_enq += 1
            continue
        task = ee.batch.Export.image.toAsset(
            image=img,
            description=desc,
            assetId=asset_id,
            region=aoi,
            scale=10,
            maxPixels=1e13,
        )
        task.start()
        n_enq += 1
    return n_enq, n_skip


def resolve_year_range(args: argparse.Namespace, now_year: int) -> tuple[int, int]:
    if args.year is not None and (args.start_year is not None or args.end_year is not None):
        raise ValueError("Usa solo --year, o bien --start-year/--end-year, no ambos.")
    if args.year is not None:
        return args.year, args.year
    env_start = os.environ.get("GEE_START_YEAR", "").strip()
    if env_start.isdigit():
        default_start = int(env_start)
    else:
        default_start = DEFAULT_START_YEAR
    env_end = os.environ.get("GEE_END_YEAR", "").strip()
    if env_end.isdigit():
        default_end = int(env_end)
    else:
        default_end = DEFAULT_END_YEAR
    start_y = args.start_year if args.start_year is not None else default_start
    end_y = args.end_year if args.end_year is not None else default_end
    if start_y > end_y:
        raise ValueError(f"--start-year ({start_y}) no puede ser mayor que el último año ({end_y}).")
    return start_y, min(end_y, now_year)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exportar mosaicos S2 semanales a Earth Engine Assets.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No encola tareas; solo lista descripciones y assetIds.",
    )
    parser.add_argument(
        "--project",
        default=None,
        metavar="GCP_PROJECT_ID",
        help=(
            f"Proyecto Google Cloud para ee.Initialize() (por defecto: {DEFAULT_CLOUD_PROJECT}, "
            "salvo EE_CLOUD_PROJECT o GOOGLE_CLOUD_PROJECT)."
        ),
    )
    parser.add_argument(
        "--aoi-asset",
        default=os.environ.get("GEE_AOI_ASSET", DEFAULT_AOI_ASSET),
        help="Earth Engine AssetId del AOI (vectores).",
    )
    parser.add_argument(
        "--export-prefix",
        default=os.environ.get("GEE_EXPORT_PREFIX", DEFAULT_EXPORT_PREFIX),
        help="Prefijo de assetIds de salida sin barra final.",
    )
    parser.add_argument(
        "--skip-ensure-destination",
        action="store_true",
        help="No crear carpeta/colección vacía antes de exportar.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Encolar todas las semanas aunque el asset hijo ya exista.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        metavar="AAAA",
        help="Solo ese año civil; no usar junto con --start-year/--end-year.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        metavar="AAAA",
        help="Primer año (default: GEE_START_YEAR o DEFAULT_START_YEAR en el script).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        metavar="AAAA",
        help=(
            "Último año (por defecto: GEE_END_YEAR o DEFAULT_END_YEAR; "
            "nunca por encima del año actual en tiempo de ejecución)."
        ),
    )
    args = parser.parse_args()

    init_project = resolve_cloud_project(args.project)
    ee_initialize(init_project)

    aoi_asset = args.aoi_asset.strip().rstrip("/")
    export_prefix = args.export_prefix.strip().rstrip("/")

    if not args.skip_ensure_destination:
        ensure_export_destination(export_prefix, dry_run=args.dry_run)

    aoi = ee.FeatureCollection(aoi_asset).geometry()
    end_date = ee.Date(int(time.time() * 1000))
    now_year = end_date.get("year").getInfo()

    start_y, end_y = resolve_year_range(args, now_year)

    final_collection = build_weekly_collection(aoi, end_date, start_y, end_y)
    total = final_collection.size().getInfo()
    n_years = end_y - start_y + 1
    teorico_cap = n_years * 52

    skip_existing = not args.force
    existing_ids: set[str] = set()
    n_listed = 0
    if skip_existing:
        existing_ids, n_listed = load_existing_export_keys(export_prefix)
        print(f"Assets hijos ya en destino (listAssets): {n_listed}")

    print(f"Proyecto Cloud (initialize): {init_project}")
    print(f"AOI asset     : {aoi_asset}")
    print(f"Export prefix : {export_prefix}")
    print(f"Omitir dup.   : {'sí' if skip_existing else 'no ( --force )'}")
    print(f"Rango años    : {start_y} → {end_y} ({n_years} año(s); hasta {teorico_cap} ranuras de 1 semana).")
    print(f"Mosaicos con datos (n_images > 0): {total}")
    if n_years > 1:
        print(
            "  Nota: el total es sobre varios años. Para solo 52 ranuras típicas usa --year AAAA.",
        )

    years_range = range(start_y, end_y + 1)

    if args.dry_run:
        print("Modo dry-run: no se envían tareas a GEE.")

    total_enq = 0
    total_skip = 0
    for y in years_range:
        print(f"Año {y}...")
        enq, skipped = export_year(
            final_collection,
            aoi,
            export_prefix,
            y,
            dry_run=args.dry_run,
            existing_ids=existing_ids,
            skip_existing=skip_existing,
        )
        total_enq += enq
        total_skip += skipped

    print(
        f"Tareas encoladas (o simuladas en dry-run): {total_enq}; "
        f"omitidas (ya existían): {total_skip}"
    )
    print("Revisa el progreso en: https://code.earthengine.google.com/tasks")


if __name__ == "__main__":
    try:
        main()
    except (ee.EEException, ValueError) as exc:
        kind = "Earth Engine" if isinstance(exc, ee.EEException) else "Validación"
        print(f"Error ({kind}): {exc}", file=sys.stderr)
        if isinstance(exc, ee.EEException):
            print(
                "Ejecuta `earthengine authenticate` y configura EE_CLOUD_PROJECT (o --project=...) "
                "con el proyecto Cloud que Earth Engine muestra en https://code.earthengine.google.com/ "
                "en Configuration (necesitas rol Service Usage Consumer en ese proyecto).",
                file=sys.stderr,
            )
        sys.exit(1)
