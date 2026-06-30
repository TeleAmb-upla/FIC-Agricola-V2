#!/usr/bin/env python3
"""
Convierte archivos ``.las`` en ``data/drone/`` a JSON de nube de puntos para el explorador
(misma convención que fondecyt_puc: ``data_static/drone/pointclouds/{predio}_{fecha}.json``).

Uso desde la raíz del repo::

    python scripts/lidar/build_pointclouds.py
    python scripts/lidar/build_pointclouds.py --force
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
STATIC_SITE_DIR = REPO_ROOT / "scripts" / "static_site"
for p in (str(REPO_ROOT), str(SCRIPTS_DIR), str(STATIC_SITE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pipeline_utils  # noqa: F401

from pipeline_utils import load_config, resolve_wetland_id_from_drone_code

try:
    import laspy
except ImportError as exc:
    print(f"Instala laspy: pip install laspy", file=sys.stderr)
    raise SystemExit(1) from exc

from shapely.geometry import mapping

LAS_NAME = re.compile(
    r"^((?:[A-Za-z][A-Za-z0-9_]*))_((?:\d{8})|(?:\d{4}[_-]\d{2}[_-]\d{2})).*\.las$",
    re.I,
)
MAX_POINTCLOUD_POINTS = 1_500_000
GROUND_PERCENTILE = 5.0
LIDAR_DEFAULT_ATTR = "rgb"
DRONE_PROJECTED_CRS = "EPSG:32719"
REFERENCE_CRS = "EPSG:4326"

ASPRS_CLASS_LABELS: dict[int, str] = {
    1: "No clasificado",
    2: "Suelo",
    3: "Vegetación baja",
    4: "Vegetación media",
    5: "Vegetación alta",
    6: "Edificio",
    7: "Puntos bajos",
    8: "Modelo de superficie",
    9: "Agua",
}
ASPRS_CLASS_COLORS: dict[int, str] = {
    1: "#808080",
    2: "#8B4513",
    3: "#9ACD32",
    4: "#228B22",
    5: "#006400",
    6: "#DC143C",
    7: "#4DA6FF",
    8: "#D2691E",
    9: "#1E90FF",
}
SKIP_LIDAR_DIMS = frozenset(
    {
        "x",
        "y",
        "z",
        "synthetic",
        "key_point",
        "withheld",
        "scan_direction_flag",
        "edge_of_flight_line",
    }
)


def _flat_date_to_key(raw: str) -> str:
    """Misma clave que ``export_data_ortho`` (``YYYYMMDD`` compacto)."""
    digits = re.sub(r"\D", "", raw.strip())
    if len(digits) < 8:
        raise ValueError(raw)
    return digits[:8]


def discover_las_files(drone_dir: Path, config: dict | None = None) -> list[dict]:
    out: list[dict] = []
    for path in sorted(drone_dir.glob("*.las")):
        m = LAS_NAME.match(path.name)
        if not m:
            continue
        code = m.group(1).upper()
        wid = resolve_wetland_id_from_drone_code(m.group(1), config)
        key = _flat_date_to_key(m.group(2))
        dt = datetime.strptime(key, "%Y%m%d").replace(tzinfo=timezone.utc)
        out.append(
            {
                "path": path,
                "wetland_id": wid,
                "code": code,
                "period_key": key,
                "label": dt.strftime("%d %b %Y"),
            }
        )
    return out


def load_wetland_geoms_utm(config: dict) -> dict[str, dict]:
    """Geometrías de recorte por predio: unión de cuarteles en ``cuarteles.geojson``."""
    from shapely.geometry import shape

    from pipeline_utils import load_wetland_clip_geometries

    clip_geoms = load_wetland_clip_geometries(config)
    parcels: dict[str, dict] = {}
    for wid, geom_wgs84 in clip_geoms.items():
        if not geom_wgs84:
            continue
        geom_wgs = shape(geom_wgs84)
        if geom_wgs.is_empty:
            continue
        gdf = gpd.GeoDataFrame(geometry=[geom_wgs], crs="EPSG:4326")
        geom = gdf.to_crs(DRONE_PROJECTED_CRS).geometry.iloc[0]
        if geom is None or geom.is_empty:
            continue
        minx, miny, maxx, maxy = geom.bounds
        parcels[wid] = {
            "geometry_proj": geom,
            "crs_proj": DRONE_PROJECTED_CRS,
            "leaflet_bounds": [
                [miny, minx],
                [maxy, maxx],
            ],
        }
    return parcels


def detect_las_crs(las_path: Path) -> str:
    with laspy.open(las_path) as las:
        crs = las.header.parse_crs()
        if crs is not None:
            epsg = crs.to_epsg()
            if epsg:
                return f"EPSG:{epsg}"
    return REFERENCE_CRS


def _transform_xy(xs: np.ndarray, ys: np.ndarray, src_crs: str, dst_crs: str) -> tuple[np.ndarray, np.ndarray]:
    if src_crs == dst_crs:
        return xs, ys
    from pyproj import Transformer

    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    xe, ye = transformer.transform(xs, ys)
    return np.asarray(xe, dtype=np.float64), np.asarray(ye, dtype=np.float64)


def clip_las_fields_to_parcel(las_path: Path, parcel_info: dict) -> dict[str, np.ndarray]:
    from shapely import contains_xy

    geom_proj = parcel_info["geometry_proj"]
    crs_proj = parcel_info.get("crs_proj") or DRONE_PROJECTED_CRS
    las_crs = detect_las_crs(las_path)

    las = laspy.read(las_path)
    xs = np.asarray(las.x, dtype=np.float64)
    ys = np.asarray(las.y, dtype=np.float64)
    xs, ys = _transform_xy(xs, ys, las_crs, crs_proj)

    minx, miny, maxx, maxy = geom_proj.bounds
    coarse = (xs >= minx) & (xs <= maxx) & (ys >= miny) & (ys <= maxy)
    if not np.any(coarse):
        return {"x": np.array([]), "y": np.array([]), "z": np.array([]), "crs": crs_proj}
    xs = xs[coarse]
    ys = ys[coarse]
    inside = contains_xy(geom_proj, xs, ys) if xs.size else np.array([], dtype=bool)
    fields: dict[str, np.ndarray] = {
        "x": xs[inside],
        "y": ys[inside],
        "z": np.asarray(las.z[coarse], dtype=np.float32)[inside],
        "crs": crs_proj,
    }
    for dim in las.point_format.dimension_names:
        key = dim.lower()
        if key in ("x", "y", "z") or key in SKIP_LIDAR_DIMS:
            continue
        raw = getattr(las, dim)
        arr = np.asarray(raw[coarse], copy=False)[inside]
        fields[key] = arr.astype(np.float32, copy=False)
    return fields


def discover_lidar_dimensions(las_path: Path) -> list[str]:
    with laspy.open(las_path) as las:
        return [d.lower() for d in las.header.point_format.dimension_names]


def build_lidar_attribute_catalog(dimensions: list[str]) -> list[dict]:
    dims = {d.lower() for d in dimensions}
    catalog: list[dict] = [
        {"id": "canopy", "label": "Altura dosel", "unit": "m", "colormap": "terrain", "derived": "canopy"},
    ]
    if {"red", "green", "blue"}.issubset(dims):
        catalog.append({"id": "rgb", "label": "RGB", "type": "rgb", "dims": ["red", "green", "blue"]})
    known: dict[str, dict] = {
        "intensity": {"id": "intensity", "label": "Intensidad", "colormap": "viridis", "dim": "intensity"},
        "classification": {
            "id": "classification",
            "label": "Clasificación",
            "type": "categorical",
            "dim": "classification",
        },
    }
    seen = {s["id"] for s in catalog}
    for dim, spec in known.items():
        if dim in dims and spec["id"] not in seen:
            catalog.append(spec)
            seen.add(spec["id"])
    return catalog


def ground_elevation_m(zs: np.ndarray) -> float:
    if zs.size == 0:
        return float("nan")
    return float(np.percentile(zs, GROUND_PERCENTILE))


def canopy_heights_m(zs: np.ndarray, z_ground: float) -> np.ndarray:
    if zs.size == 0:
        return zs.astype(np.float32, copy=False)
    return np.maximum(zs.astype(np.float32, copy=False) - float(z_ground), 0.0)


def subsample_pointcloud_indices(xs: np.ndarray, ys: np.ndarray, max_points: int) -> np.ndarray:
    """Reduce a ``max_points`` preservando la mayor densidad visual posible."""
    n = xs.size
    if n <= max_points:
        return np.arange(n, dtype=np.int64)
    rng = np.random.default_rng(42)
    return np.sort(rng.choice(n, max_points, replace=False))


def _predio_boundary_local_rings(parcel_info: dict) -> list[list[list[float]]]:
    """Anillos de contorno en coords locales (un anillo por polígono del predio)."""
    geom = parcel_info["geometry_proj"]
    c = geom.centroid
    e0, n0 = float(c.x), float(c.y)
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms) if geom.geom_type == "MultiPolygon" else []
    rings: list[list[list[float]]] = []
    for poly in polys:
        if poly.is_empty:
            continue
        coords = list(poly.exterior.coords)
        if len(coords) < 3:
            continue
        ring = [[round(x - e0, 3), round(y - n0, 3), 0.0] for x, y in coords]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        rings.append(ring)
    return rings


def _predio_boundary_local_ring(parcel_info: dict) -> list[list[float]]:
    rings = _predio_boundary_local_rings(parcel_info)
    return rings[0] if rings else []


def _scalar_attr_payload(values: np.ndarray, spec: dict) -> dict:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"type": "scalar", "values": [], "vmin": None, "vmax": None}
    if spec.get("type") == "categorical" or spec.get("id") == "classification":
        classes = sorted({int(v) for v in np.unique(finite)})
        return {
            "type": "categorical",
            "values": [int(v) for v in values.tolist()],
            "classes": {str(c): ASPRS_CLASS_LABELS.get(c, f"Clase {c}") for c in classes},
            "colors": {str(c): ASPRS_CLASS_COLORS.get(c, "#888888") for c in classes},
        }
    p0, p95 = np.percentile(finite, [2, 95])
    return {
        "type": "scalar",
        "values": [round(float(v), 4) for v in values.tolist()],
        "vmin": round(float(p0), 4),
        "vmax": round(float(p95), 4),
    }


def export_lidar_pointcloud_json(
    fields: dict[str, np.ndarray],
    catalog: list[dict],
    out_path: Path,
    *,
    parcel_info: dict,
    max_points: int = MAX_POINTCLOUD_POINTS,
) -> dict:
    xs = fields.get("x", np.array([]))
    ys = fields.get("y", np.array([]))
    zs = fields.get("z", np.array([]))
    if xs.size == 0:
        empty_rings = _predio_boundary_local_rings(parcel_info)
        payload = {
            "count": 0,
            "ground_z": None,
            "default_attribute": LIDAR_DEFAULT_ATTR,
            "coord_frame": "projected",
            "crs": parcel_info.get("crs_proj"),
            "origin": [],
            "boundary": empty_rings[0] if empty_rings else [],
            "boundary_rings": empty_rings,
            "positions": [],
            "attributes": {},
        }
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        return {"count": 0}

    z_ground = ground_elevation_m(zs)
    canopy = canopy_heights_m(zs, z_ground)
    geom = parcel_info["geometry_proj"]
    c = geom.centroid
    e0, n0 = float(c.x), float(c.y)
    idx = subsample_pointcloud_indices(xs, ys, max_points)
    xs, ys, zs = xs[idx], ys[idx], zs[idx]
    canopy = canopy[idx]
    mx = (xs - e0).astype(np.float32)
    my = (ys - n0).astype(np.float32)
    mz = canopy.astype(np.float32, copy=False)
    positions = np.empty(mx.size * 3, dtype=np.float32)
    positions[0::3] = mx
    positions[1::3] = my
    positions[2::3] = mz

    attributes: dict[str, dict] = {}
    for spec in catalog:
        aid = spec["id"]
        if spec.get("derived") == "canopy":
            attributes[aid] = _scalar_attr_payload(mz, spec)
            continue
        if spec.get("type") == "rgb":
            r = fields.get("red", np.array([]))
            g = fields.get("green", np.array([]))
            b = fields.get("blue", np.array([]))
            if r.size and g.size and b.size:
                attributes[aid] = {
                    "type": "rgb",
                    "red": [int(v) for v in r[idx].tolist()],
                    "green": [int(v) for v in g[idx].tolist()],
                    "blue": [int(v) for v in b[idx].tolist()],
                }
            continue
        dim = spec.get("dim")
        if dim and dim in fields:
            attributes[aid] = _scalar_attr_payload(fields[dim][idx], spec)

    boundary_rings = _predio_boundary_local_rings(parcel_info)
    payload = {
        "count": int(mx.size),
        "ground_z": round(z_ground, 4),
        "default_attribute": LIDAR_DEFAULT_ATTR,
        "coord_frame": "projected",
        "crs": fields.get("crs") or parcel_info.get("crs_proj"),
        "origin": [round(e0, 3), round(n0, 3)],
        "boundary": boundary_rings[0] if boundary_rings else [],
        "boundary_rings": boundary_rings,
        "positions": [round(float(v), 3) for v in positions.tolist()],
        "attributes": attributes,
        "zmin": round(float(np.min(mz)), 4),
        "zmax": round(float(np.max(mz)), 4),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return payload


def _scalar_stretch_from_values(values: list | np.ndarray) -> tuple[float, float] | None:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return None
    p0, p95 = np.percentile(finite, [2, 95])
    return round(float(p0), 4), round(float(p95), 4)


def _lidar_stretch_from_pointclouds(static_dir: Path, pointclouds: dict) -> dict[str, dict]:
    """Agrega vmin/vmax globales por atributo escalar (P2–P95) desde los JSON exportados."""
    stretch: dict[str, dict[str, float]] = {}
    for entry in pointclouds.values():
        rel = entry.get("p")
        if not rel:
            continue
        path = (static_dir.parent / rel).resolve()
        if not path.is_file():
            path = (static_dir / "pointclouds" / Path(str(rel)).name).resolve()
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for aid, attr in (data.get("attributes") or {}).items():
            if not isinstance(attr, dict) or attr.get("type") != "scalar":
                continue
            lim = None
            if attr.get("values"):
                lim = _scalar_stretch_from_values(attr["values"])
            if lim is None:
                vmin, vmax = attr.get("vmin"), attr.get("vmax")
                if vmin is None or vmax is None:
                    continue
                lim = (float(vmin), float(vmax))
            vmin, vmax = lim
            cur = stretch.get(aid)
            if not cur:
                stretch[aid] = {"vmin": vmin, "vmax": vmax}
            else:
                cur["vmin"] = min(cur["vmin"], vmin)
                cur["vmax"] = max(cur["vmax"], vmax)
    return stretch


def patch_drone_metadata(static_dir: Path, pointclouds: dict, catalog: list[dict], period_keys: list[str]) -> None:
    meta_path = static_dir / "metadata.json"
    if not meta_path.is_file():
        print(f"[WARN] No existe {meta_path}; corre export_data_ortho.py antes.", file=sys.stderr)
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    lidar_attrs: dict[str, dict] = {}
    for spec in catalog:
        aid = spec["id"]
        lidar_attrs[aid] = {
            "label": spec.get("label") or aid,
            "colormap": spec.get("colormap", "viridis"),
            "symmetric": False,
            "visual_only": spec.get("type") == "rgb",
        }
        if spec.get("type") == "categorical":
            lidar_attrs[aid]["type"] = "categorical"
        elif spec.get("type") == "rgb":
            lidar_attrs[aid]["type"] = "rgb"
    # Reemplaza (no fusiona) para no conservar claves legacy de ejecuciones previas
    # con wetland_id antiguos (p. ej. ``rci_…`` en vez de ``a_brito_rci_…``).
    meta["pointclouds"] = dict(pointclouds)
    meta["lidar_attributes"] = lidar_attrs
    meta["lidar_default_attribute"] = LIDAR_DEFAULT_ATTR
    meta["lidar_stretch"] = _lidar_stretch_from_pointclouds(static_dir, meta["pointclouds"])
    meta["las_available_periods"] = sorted(set(meta.get("las_available_periods", []) + period_keys))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Metadata actualizado: {meta_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="LAS → JSON pointclouds para explorador FIC.")
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--max-points",
        type=int,
        default=MAX_POINTCLOUD_POINTS,
        help=f"Máximo de puntos por nube exportada (default: {MAX_POINTCLOUD_POINTS}).",
    )
    args = ap.parse_args()

    max_points = max(10_000, int(args.max_points))

    config = load_config(REPO_ROOT / "config.yaml")
    drone_dir = (REPO_ROOT / config["sources"]["drone"]["input_root"]).resolve()
    static_dir = (REPO_ROOT / config["sources"]["drone"]["static_root"]).resolve()
    pc_dir = static_dir / "pointclouds"

    flights = discover_las_files(drone_dir, config)
    if not flights:
        print(f"No hay .las en {drone_dir}", file=sys.stderr)
        sys.exit(0)

    parcels = load_wetland_geoms_utm(config)
    catalog: list[dict] = []
    pointclouds_meta: dict[str, dict] = {}
    period_keys: list[str] = []

    for fl in flights:
        wid = fl["wetland_id"]
        pinfo = parcels.get(wid)
        if not pinfo:
            print(f"[WARN] Sin geometría para {wid}; omito {fl['path'].name}")
            continue
        if not catalog:
            catalog = build_lidar_attribute_catalog(discover_lidar_dimensions(fl["path"]))
        stem = f"{wid}_{fl['period_key']}"
        out_json = pc_dir / f"{stem}.json"
        if out_json.is_file() and not args.force:
            print(f"  [omitir] {out_json.name}")
        else:
            print(f"  Procesando {fl['path'].name} -> {out_json.name}")
            fields = clip_las_fields_to_parcel(fl["path"], pinfo)
            export_lidar_pointcloud_json(
                fields, catalog, out_json, parcel_info=pinfo, max_points=max_points
            )
        rel = f"drone/pointclouds/{stem}.json"
        pointclouds_meta[stem] = {
            "p": rel,
            "l": f"{wid.upper()} · {fl['label']} · LiDAR",
            "period_key": fl["period_key"],
            "predio_id": wid,
            "wetland_id": wid,
            "z_unit": "canopy_m",
            "attribute_ids": [s["id"] for s in catalog],
        }
        period_keys.append(fl["period_key"])

    patch_drone_metadata(static_dir, pointclouds_meta, catalog, period_keys)
    print(f"Listo: {len(pointclouds_meta)} nube(s) LiDAR.")


if __name__ == "__main__":
    main()
