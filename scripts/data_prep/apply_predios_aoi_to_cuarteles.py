#!/usr/bin/env python3
"""
Propaga geometrías de ``predios_aoi.geojson`` (por ``predio_id``) a ``cuarteles.geojson``.

Fuente espacial maestra: AOI por predio. Los cuarteles heredan polígonos según:
- 1 cuartel → geometría completa del predio
- N cuarteles y N partes (MultiPolygon) → emparejamiento por mayor solape con geometría previa
- resto → recorte de la geometría previa al AOI del predio
"""
from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parents[2]


def _predios_aoi_path(config: dict) -> Path:
    raw = config.get("predios_aoi_path") or "data_static/predios_aoi.geojson"
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _explode_parts(geom) -> list:
    if geom is None or geom.is_empty:
        return []
    gt = geom.geom_type
    if gt == "MultiPolygon":
        return [p for p in geom.geoms if not p.is_empty]
    if gt == "Polygon":
        return [geom]
    return []


def _assign_cuartel_geometries(
    predio_geom,
    cuartel_ids: list[str],
    old_geoms: dict[str, object],
) -> dict[str, object]:
    cuartels = sorted(cuartel_ids)
    if not cuartels:
        return {}
    if len(cuartels) == 1:
        return {cuartels[0]: predio_geom}

    parts = _explode_parts(predio_geom)
    if len(parts) == len(cuartels):
        assigned: dict[str, object] = {}
        used: set[int] = set()
        for cid in cuartels:
            old = old_geoms.get(cid)
            best_i = -1
            best_area = 0.0
            for i, part in enumerate(parts):
                if i in used:
                    continue
                if old is None or old.is_empty:
                    score = part.area
                else:
                    inter = old.intersection(part)
                    score = float(inter.area) if not inter.is_empty else 0.0
                if score > best_area:
                    best_area = score
                    best_i = i
            if best_i >= 0:
                assigned[cid] = parts[best_i]
                used.add(best_i)
            else:
                assigned[cid] = predio_geom
        return assigned

    out: dict[str, object] = {}
    for cid in cuartels:
        old = old_geoms.get(cid)
        if old is not None and not old.is_empty:
            clipped = old.intersection(predio_geom)
            if not clipped.is_empty and clipped.area > 0:
                out[cid] = clipped
                continue
        out[cid] = predio_geom
    return out


def _cuarteles_by_predio(fc: dict) -> dict[str, list[str]]:
    by_predio: dict[str, list[str]] = {}
    for feat in fc.get("features") or []:
        props = feat.get("properties") or {}
        cid = str(props.get("id_cuartel") or "").strip()
        pid = str(props.get("predio_id") or "").strip().lower()
        if cid and pid:
            by_predio.setdefault(pid, []).append(cid)
    for pid in by_predio:
        by_predio[pid].sort()
    return by_predio


def apply_predios_aoi_to_cuarteles(
    config: dict,
    cuarteles_path: Path,
    *,
    aoi_path: Path | None = None,
) -> int:
    aoi_file = aoi_path or _predios_aoi_path(config)
    if not aoi_file.is_file():
        raise FileNotFoundError(f"No existe {aoi_file}")
    if not cuarteles_path.is_file():
        raise FileNotFoundError(f"No existe {cuarteles_path}")

    aoi_fc = json.loads(aoi_file.read_text(encoding="utf-8"))
    predio_geoms = {
        str(f["properties"]["predio_id"]).strip().lower(): shape(f["geometry"])
        for f in aoi_fc.get("features") or []
        if f.get("properties", {}).get("predio_id")
    }

    fc = json.loads(cuarteles_path.read_text(encoding="utf-8"))
    by_predio = _cuarteles_by_predio(fc)
    if not by_predio:
        raise ValueError("cuarteles.geojson sin predio_id en features; ejecuta sync antes.")

    old_by_cid: dict[str, object] = {}
    feat_by_cid: dict[str, dict] = {}
    for feat in fc.get("features") or []:
        props = feat.get("properties") or {}
        cid = str(props.get("id_cuartel") or "").strip()
        if not cid:
            continue
        feat_by_cid[cid] = feat
        geom = feat.get("geometry")
        old_by_cid[cid] = shape(geom) if geom else None

    n_updated = 0
    for pid, cuartel_ids in sorted(by_predio.items()):
        predio_geom = predio_geoms.get(pid)
        if predio_geom is None or predio_geom.is_empty:
            print(f"  [warn] sin AOI en predios_aoi para {pid!r}")
            continue
        assigned = _assign_cuartel_geometries(predio_geom, cuartel_ids, old_by_cid)
        for cid, new_geom in assigned.items():
            feat = feat_by_cid.get(cid)
            if not feat:
                continue
            new_geom = unary_union(new_geom) if hasattr(new_geom, "geoms") else new_geom
            old = old_by_cid.get(cid)
            if old is not None and old.equals(new_geom):
                continue
            feat["geometry"] = mapping(new_geom)
            feat.setdefault("properties", {})["fuente"] = aoi_file.relative_to(REPO_ROOT).as_posix()
            n_updated += 1

    if n_updated:
        cuarteles_path.write_text(json.dumps(fc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return n_updated
