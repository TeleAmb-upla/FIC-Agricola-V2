#!/usr/bin/env python3
"""Asigna comuna y provincia a cuarteles según intersección con shape comunal."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data_prep.vectors_paths import COMUNAS_SHP, CUARTELES_GEOJSON  # noqa: E402


def _comuna_for_polygon(row, comunas: gpd.GeoDataFrame) -> tuple[str, str]:
    hits = comunas[comunas.intersects(row.geometry)]
    if hits.empty:
        return "", ""
    if len(hits) == 1:
        hit = hits.iloc[0]
        return str(hit["NOM_COM"]).strip(), str(hit["PROV"]).strip()
    areas = hits.intersection(row.geometry).area
    hit = hits.loc[areas.idxmax()]
    return str(hit["NOM_COM"]).strip(), str(hit["PROV"]).strip()


def assign_comunas(
    cuarteles_path: Path = CUARTELES_GEOJSON,
    comunas_path: Path = COMUNAS_SHP,
    *,
    dry_run: bool = False,
) -> list[dict]:
    if not comunas_path.is_file():
        raise FileNotFoundError(f"No existe shape comunal: {comunas_path}")
    if not cuarteles_path.is_file():
        raise FileNotFoundError(f"No existe geojson de cuarteles: {cuarteles_path}")

    comunas = gpd.read_file(comunas_path, encoding="latin-1")
    cuarteles = gpd.read_file(cuarteles_path)
    if cuarteles.crs != comunas.crs:
        cuarteles = cuarteles.to_crs(comunas.crs)

    changes: list[dict] = []
    cid_to_loc: dict[str, tuple[str, str]] = {}
    for _, row in cuarteles.iterrows():
        cid = str(row.get("id_cuartel") or "").strip()
        if not cid:
            continue
        new_comuna, new_provincia = _comuna_for_polygon(row, comunas)
        cid_to_loc[cid] = (new_comuna, new_provincia)
        old_comuna = str(row.get("comuna") or "").strip()
        old_provincia = str(row.get("provincia") or "").strip()
        if old_comuna != new_comuna or old_provincia != new_provincia:
            changes.append(
                {
                    "id_cuartel": cid,
                    "comuna": {"old": old_comuna, "new": new_comuna},
                    "provincia": {"old": old_provincia, "new": new_provincia},
                }
            )

    if dry_run:
        return changes

    with cuarteles_path.open(encoding="utf-8") as fh:
        fc = json.load(fh)
    for feat in fc.get("features") or []:
        props = feat.setdefault("properties", {})
        cid = str(props.get("id_cuartel") or "").strip()
        if cid not in cid_to_loc:
            continue
        comuna, provincia = cid_to_loc[cid]
        props["comuna"] = comuna
        props["provincia"] = provincia

    with cuarteles_path.open("w", encoding="utf-8") as fh:
        json.dump(fc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta cambios")
    parser.add_argument("--sync", action="store_true", help="Ejecuta sync_predios_master tras actualizar")
    args = parser.parse_args()

    changes = assign_comunas(dry_run=args.dry_run)
    if not changes:
        print("Sin cambios: comuna/provincia ya coinciden con el shape comunal.")
    else:
        print(f"Cambios ({len(changes)}):")
        for ch in changes:
            print(
                f"  {ch['id_cuartel']}: "
                f"{ch['comuna']['old']}/{ch['provincia']['old']} -> "
                f"{ch['comuna']['new']}/{ch['provincia']['new']}"
            )
        if args.dry_run:
            print("(dry-run: no se escribió geojson)")

    if args.sync and not args.dry_run:
        from scripts.data_prep.sync_predios_master import main as sync_main

        sync_main()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
