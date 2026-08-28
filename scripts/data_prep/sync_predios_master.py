#!/usr/bin/env python3
"""Sincroniza cuarteles.geojson → CSV, índice y copia estática."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_STATIC = REPO_ROOT / "scripts" / "static_site"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
for p in (str(REPO_ROOT), str(SCRIPTS_STATIC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from scripts.data_prep.apply_predios_aoi_to_cuarteles import apply_predios_aoi_to_cuarteles  # noqa: E402
from scripts.data_prep.cuartel_areas import superficie_from_geometry  # noqa: E402

from pipeline_utils import (  # noqa: E402
    bootstrap_proj_environment,
    build_cuarteles_index,
    load_config,
    load_cuartels_by_predio,
)
from scripts.data_prep.vectors_paths import CUARTELES_GEOJSON, STATIC_CUARTELES_GEOJSON

DB_CSV = REPO_ROOT / "data" / "fic_database.csv"
DB_STATIC = REPO_ROOT / "data_static" / "fic_database.csv"
INDEX_PATH = REPO_ROOT / "data_static" / "cuarteles_index.json"

PROP_COLS = [
    "id_cuartel", "nom_cuartel", "cultivo", "nom_predio", "propietario", "superficie",
    "asesor", "area_indap", "comuna", "provincia", "poligono_cuartel", "poligono_vuelo",
]


def _apply_superficie_from_geometry(fc: dict) -> int:
    """Recalcula ``superficie`` (ha) desde la geometría de cada feature."""
    n = 0
    for feat in fc.get("features") or []:
        props = feat.setdefault("properties", {})
        cid = str(props.get("id_cuartel") or "").strip()
        if not cid:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        new_val = superficie_from_geometry(shape(geom))
        if not new_val:
            continue
        if str(props.get("superficie") or "").strip() != new_val:
            n += 1
        props["superficie"] = new_val
    return n


def _inject_predio_ids(fc: dict, config: dict) -> None:
    cid_to_pid: dict[str, str] = {}
    for predio_id, cuartels in load_cuartels_by_predio(config).items():
        for cu in cuartels:
            cid = str(cu.get("id_cuartel") or "").strip()
            if cid:
                cid_to_pid[cid] = predio_id
    for feat in fc.get("features") or []:
        props = feat.setdefault("properties", {})
        cid = str(props.get("id_cuartel") or "").strip()
        if cid and cid in cid_to_pid:
            props["predio_id"] = cid_to_pid[cid]
        props.pop("wetland_id", None)



def _update_fic_database_csv(fc: dict) -> tuple[int, int, int]:
    """Reescribe ``fic_database.csv`` desde las propiedades del GeoJSON."""
    csv_by_id: dict[str, dict[str, str]] = {}
    if DB_CSV.is_file():
        with open(DB_CSV, encoding="utf-8-sig", newline="") as f:
            for raw in csv.DictReader(line for line in f if line.strip()):
                cid = str(raw.get("id_cuartel") or "").strip()
                if cid:
                    csv_by_id[cid] = {k: (raw.get(k) or "").strip() for k in raw}

    rows: list[dict] = []
    n_updated = 0
    n_added = 0
    for feat in sorted(fc.get("features") or [], key=lambda f: (f.get("properties") or {}).get("id_cuartel", "")):
        props = feat.get("properties") or {}
        cid = str(props.get("id_cuartel") or "").strip()
        if not cid:
            continue
        prev = csv_by_id.get(cid, {})
        row = {col: str(props.get(col) or prev.get(col) or "").strip() for col in PROP_COLS}
        if prev:
            if any(row[col] != prev.get(col, "") for col in PROP_COLS):
                n_updated += 1
        else:
            n_added += 1
        rows.append(row)

    with open(DB_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROP_COLS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in PROP_COLS})
    return len(rows), n_updated, n_added


def _write_geojson(path: Path, fc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    bootstrap_proj_environment()
    if not CUARTELES_GEOJSON.is_file():
        raise SystemExit(f"No existe {CUARTELES_GEOJSON}")

    config = load_config()

    if not config.get("cuarteles_geojson_authoritative"):
        n_geom = apply_predios_aoi_to_cuarteles(config, CUARTELES_GEOJSON)
        if n_geom:
            print(f"  Geometrías desde predios_aoi: {n_geom} cuartel(es)")

    fc = json.loads(CUARTELES_GEOJSON.read_text(encoding="utf-8"))
    n = _apply_superficie_from_geometry(fc)
    if n:
        print(f"  Superficie actualizada en {n} cuartel(es) (desde geometría)")
    _inject_predio_ids(fc, config)
    _write_geojson(CUARTELES_GEOJSON, fc)
    _write_geojson(STATIC_CUARTELES_GEOJSON, fc)
    print(f"  cuarteles.geojson -> {STATIC_CUARTELES_GEOJSON.relative_to(REPO_ROOT)}")

    n_rows, n_csv, n_added = _update_fic_database_csv(fc)
    DB_STATIC.write_text(DB_CSV.read_text(encoding="utf-8"), encoding="utf-8")
    msg = f"  fic_database.csv ({n_rows} filas, {n_csv} superficie(s) actualizadas"
    if n_added:
        msg += f", {n_added} fila(s) nuevas"
    print(msg + ")")

    cuartels_by_predio = load_cuartels_by_predio(config)
    index = build_cuarteles_index(config)
    doc = {
        "cuarteles": index,
        "by_predio": {pid: [c["id_cuartel"] for c in cu_list] for pid, cu_list in cuartels_by_predio.items()},
    }
    INDEX_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  cuarteles_index.json ({len(index)} cuarteles, {len(doc['by_predio'])} predios)")
    print("Listo.")


if __name__ == "__main__":
    main()
