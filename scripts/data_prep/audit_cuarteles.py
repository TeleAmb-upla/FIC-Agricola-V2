#!/usr/bin/env python3
"""Auditoría de cuarteles.geojson vs fic_database.csv y config.yaml."""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from shapely.geometry import shape
from shapely.validation import explain_validity

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.data_prep.cuartel_areas import superficie_from_geometry

GEOJSON = REPO / "data/vectors/cuarteles/cuarteles.geojson"
CSV_PATH = REPO / "data/fic_database.csv"
CONFIG = REPO / "config.yaml"

REQUIRED = [
    "id_cuartel", "nom_cuartel", "cultivo", "nom_predio", "propietario",
    "superficie", "poligono_vuelo",
]


def main() -> int:
    fc = json.loads(GEOJSON.read_text(encoding="utf-8"))
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    rows = {
        r["id_cuartel"]: r
        for r in csv.DictReader(CSV_PATH.open(encoding="utf-8-sig"))
    }
    features = fc.get("features") or []
    ids = [str((f.get("properties") or {}).get("id_cuartel") or "").strip() for f in features]

    issues: list[str] = []
    warns: list[str] = []

    print("=== CONTEO ===")
    print(f"Features geojson: {len(features)}")
    print(f"Filas CSV: {len(rows)}")
    print(f"Predios config: {len(cfg.get('predios', {}))}")

    for i, cid in enumerate(ids):
        if not cid:
            issues.append(f"Feature #{i}: sin id_cuartel")
    dups = [k for k, v in Counter(ids).items() if v > 1 and k]
    if dups:
        issues.append(f"id_cuartel duplicados: {dups}")

    geo_ids = {i for i in ids if i}
    if geo_ids - rows.keys():
        issues.append(f"En geojson pero no CSV: {sorted(geo_ids - rows.keys())}")
    if rows.keys() - geo_ids:
        issues.append(f"En CSV pero no geojson: {sorted(rows.keys() - geo_ids)}")

    by_predio: dict[str, list[str]] = defaultdict(list)
    for f in features:
        p = f.get("properties") or {}
        cid = p.get("id_cuartel")
        pid = str(p.get("predio_id") or "").strip().lower()
        by_predio[pid].append(str(cid))

    print("\n=== CUARTELES POR PREDIO ===")
    for pid in sorted(by_predio, key=lambda x: x or "?"):
        label = pid if pid else "(sin predio_id)"
        cu = sorted(by_predio[pid])
        print(f"  {label:20} {len(cu):2}  {', '.join(cu)}")

    cfg_pids = set(cfg.get("predios", {}))
    geo_pids = {p for p in by_predio if p}
    if geo_pids - cfg_pids:
        warns.append(f"predio_id sin config: {sorted(geo_pids - cfg_pids)}")
    if cfg_pids - geo_pids:
        warns.append(f"config sin cuarteles: {sorted(cfg_pids - geo_pids)}")

    print("\n=== DETALLE (solo problemas o cuarteles nuevos) ===")
    wkb_map: dict[str, list[str]] = defaultdict(list)
    for f in sorted(features, key=lambda x: (x.get("properties") or {}).get("id_cuartel", "")):
        p = f.get("properties") or {}
        cid = p.get("id_cuartel")
        line_issues: list[str] = []

        missing = [c for c in REQUIRED if not str(p.get(c) or "").strip()]
        if missing:
            line_issues.append("faltan " + ", ".join(missing))

        geom = f.get("geometry")
        calc = ""
        if not geom:
            line_issues.append("sin geometria")
        else:
            g = shape(geom)
            if g.is_empty:
                line_issues.append("geometria vacia")
            elif not g.is_valid:
                line_issues.append(explain_validity(g))
            else:
                wkb_map[g.wkb_hex].append(str(cid))
            calc = superficie_from_geometry(g)

        geo_sup = str(p.get("superficie") or "")
        csv_sup = rows.get(cid, {}).get("superficie", "") if cid in rows else ""
        if calc and geo_sup and calc != geo_sup:
            line_issues.append(f"superficie geo={geo_sup} calc={calc}")
        if cid in rows and calc and csv_sup and calc != csv_sup:
            line_issues.append(f"superficie csv={csv_sup} calc={calc}")

        pid = str(p.get("predio_id") or "").lower()
        pv = str(p.get("poligono_vuelo") or "")
        pcfg = cfg.get("predios", {}).get(pid, {})
        if pcfg.get("aoi_filter_col") == "poligono_vuelo":
            exp = pcfg.get("aoi_filter_val", "")
            if exp and pv and pv != exp:
                line_issues.append(f"poligono_vuelo {pv} != config {exp}")

        if p.get("poligono_cuartel") and cid and str(p["poligono_cuartel"]).lower() != str(cid).lower():
            warns.append(f"{cid}: poligono_cuartel={p['poligono_cuartel']}")

        if line_issues:
            print(f"  {cid} | {line_issues}")
            issues.extend(f"{cid}: {x}" for x in line_issues)

    for cids in wkb_map.values():
        if len(cids) > 1:
            warns.append(f"misma geometria: {cids}")

    # secuencia id_cuartel
    nums = sorted(int(c[1:]) for c in geo_ids if c.startswith("c") and c[1:].isdigit())
    if nums:
        missing_nums = [f"c{n:05d}" for n in range(nums[0], nums[-1] + 1) if n not in nums]
        if missing_nums:
            warns.append(f"huecos en numeracion: {missing_nums}")

    print("\n=== RESUMEN ===")
    if issues:
        print(f"Errores ({len(issues)}):")
        for x in issues:
            print(f"  - {x}")
    else:
        print("Sin errores criticos.")

    if warns:
        print(f"Avisos ({len(warns)}):")
        for x in warns:
            print(f"  - {x}")
    else:
        print("Sin avisos.")

    if not issues:
        print("\nOK: 30 cuarteles, geometrias validas, superficies coherentes con CSV.")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
