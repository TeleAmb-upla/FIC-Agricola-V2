#!/usr/bin/env python3
"""
Construye ``data/vectors/cuarteles/cuarteles.geojson`` (WGS84) a partir de:

- Geometrías existentes en ``cuarteles.geojson`` (fuente de verdad espacial)
- Atributos de ``data/fic_database.csv`` (sin ``superficie``; se calcula desde geometría)
- KMZ en ``data/vectors/kml/`` (cuarteles sin polígono en el GeoJSON maestro)

Tras regenerar, ejecuta ``sync_predios_master.py`` para propagar superficies al CSV.

Uso::

    python scripts/data_prep/build_predios_geojson.py
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon, mapping
from shapely.ops import transform as shp_transform

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data_prep.vectors_paths import (  # noqa: E402
    CUARTELES_GEOJSON,
    CUARTELES_ROOT,
    KML_ROOT,
    VUELOS_ROOT,
)
from scripts.data_prep.cuartel_areas import superficie_from_geometry  # noqa: E402

DB_CSV = REPO_ROOT / "data" / "fic_database.csv"
OUTPUT = CUARTELES_GEOJSON

# Nombres truncados típicos de shapefile (≤10 caracteres).
SHAPE_ATTR_ALIASES = {
    "nom_cuarte": "nom_cuartel",
    "propietari": "propietario",
    "poligono_c": "poligono_cuartel",
    "poligono_v": "poligono_vuelo",
}

SHAPE_SKIP_COLS = frozenset({"Shape_Leng", "Shape_Area", "geometry"})

PROP_COLS = [
    "id_cuartel",
    "nom_cuartel",
    "cultivo",
    "nom_predio",
    "propietario",
    "superficie",
    "asesor",
    "area_indap",
    "comuna",
    "provincia",
    "poligono_cuartel",
    "poligono_vuelo",
]

# Columnas exportadas en cuarteles.geojson (mismo orden en cada feature).
GEOJSON_PROP_COLS = PROP_COLS + ["fuente", "plot_id"]


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").strip().lower())


def _flight_key(s: str) -> str:
    """``FIC-R-PORFIRI-X`` → ``ficrporfiri``."""
    t = str(s or "").strip().upper()
    t = re.sub(r"-X$", "", t, flags=re.I)
    return _norm_key(t)


FLIGHT_KEY_ALIASES = {
    "ficbporfiri": "ficrporfiri",
    "ficoporfiri": "ficrporfiri",
    "rivarola60m": "ficbdevoto",
}


def _resolve_flight_key(s: str) -> str:
    k = _flight_key(s)
    return FLIGHT_KEY_ALIASES.get(k, k)


def _to_2d(geom):
    if geom is None:
        return None
    return shp_transform(lambda x, y, z=None: (x, y), geom)


def _close_ring(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(coords) < 3:
        return coords
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return coords


def parse_kml_polygons(kml_text: str) -> list[Polygon]:
    polys: list[Polygon] = []
    for block in re.finditer(r"<Polygon[^>]*>(.*?)</Polygon>", kml_text, re.DOTALL | re.I):
        m = re.search(r"<coordinates>([^<]+)</coordinates>", block.group(1), re.I)
        if not m:
            continue
        ring: list[tuple[float, float]] = []
        for token in m.group(1).split():
            parts = token.split(",")
            if len(parts) < 2:
                continue
            ring.append((float(parts[0]), float(parts[1])))
        ring = _close_ring(ring)
        if len(ring) >= 4:
            polys.append(Polygon(ring))
    return polys


def _inner_kml_name(names: list[str]) -> str:
    lowered = [(n.replace("\\", "/"), n.replace("\\", "/").lower()) for n in names]
    for pref in ("wpmz/template.kml", "doc.kml"):
        for full, low in lowered:
            if low.endswith(pref):
                return full
    kmls = [n for n in names if n.lower().endswith(".kml")]
    if not kmls:
        raise ValueError("KMZ sin .kml interno")
    return kmls[0]


def read_kml_or_kmz(path: Path) -> list[Polygon]:
    path = path.resolve()
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path) as zf:
            inner = _inner_kml_name(zf.namelist())
            text = zf.read(inner).decode("utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return parse_kml_polygons(text)


def load_flight_polygons() -> dict[str, dict]:
    """``flight_key`` → {geometry, fuente, nombre_archivo}."""
    out: dict[str, dict] = {}
    for pattern in ("*.kml", "*.kmz"):
        for path in sorted(KML_ROOT.rglob(pattern)):
            polys = read_kml_or_kmz(path)
            if not polys:
                print(f"  [warn] sin polígono: {path.relative_to(REPO_ROOT)}")
                continue
            from shapely.ops import unary_union

            geom = _to_2d(unary_union(polys) if len(polys) > 1 else polys[0])
            key = _flight_key(path.stem)
            entry = {
                "geometry": geom,
                "fuente": path.relative_to(REPO_ROOT).as_posix(),
                "poligono_vuelo": path.stem.upper(),
            }
            out[key] = entry
            alias = FLIGHT_KEY_ALIASES.get(key)
            if alias:
                out[alias] = entry
            print(f"  [kml] {path.name} -> {key}")
    return out


def _parse_popup(popup: str) -> dict[str, str]:
    text = str(popup or "")
    out: dict[str, str] = {}
    m_id = re.search(r"\bID\s+(\d+)\b", text, re.I)
    if m_id:
        out["plot_id"] = m_id.group(1).strip()
    m_ag = re.search(r"AGRICULTOR\s+(.+?)(?:\s{2,}|<br|$)", text, re.I)
    if m_ag:
        out["propietario"] = re.sub(r"\s+", " ", m_ag.group(1)).strip()
    return out


def load_cultivo_polygons() -> list[dict]:
    shp = CUARTELES_ROOT / "_legacy" / "predios_gv" / "predios_gv.shp"
    if not shp.exists():
        return []
    gdf = gpd.read_file(shp)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    rows: list[dict] = []
    for _, row in gdf.iterrows():
        popup = _parse_popup(row.get("PopupInfo"))
        if not popup.get("propietario") and not popup.get("plot_id"):
            layer = str(row.get("layer") or "").strip().upper()
            if layer in {"G6", "NOG"}:
                rows.append(
                    {
                        "geometry": _to_2d(row.geometry),
                        "fuente": shp.relative_to(REPO_ROOT).as_posix(),
                        "wetland_id": layer.lower(),
                        "layer": layer,
                    }
                )
            continue
        rows.append(
            {
                "geometry": _to_2d(row.geometry),
                "fuente": shp.relative_to(REPO_ROOT).as_posix(),
                "plot_id": popup.get("plot_id"),
                "propietario": popup.get("propietario"),
                "layer": str(row.get("layer") or "").strip() or None,
                "name": str(row.get("Name") or "").strip() or None,
            }
        )
    print(f"  [shp] {len(rows)} polígonos desde predios_gv.shp")
    return rows


def load_predio_geojson(name: str, wetland_id: str) -> dict | None:
    path = VUELOS_ROOT / name
    if not path.exists():
        path = REPO_ROOT / name
    if not path.exists():
        return None
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    if gdf.empty:
        return None
    geom = _to_2d(gdf.geometry.union_all())
    return {
        "geometry": geom,
        "fuente": path.relative_to(REPO_ROOT).as_posix(),
        "wetland_id": wetland_id,
    }


def load_csv_rows() -> list[dict]:
    rows: list[dict] = []
    with open(DB_CSV, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(line for line in f if line.strip())
        fields = reader.fieldnames or []
        for raw in reader:
            row = {k: re.sub(r"\s+", " ", (raw.get(k) or "").strip()) for k in PROP_COLS if k in fields}
            if not row.get("id_cuartel"):
                continue
            rows.append(row)
    print(f"  [csv] {len(rows)} cuarteles en fic_database.csv")
    return rows


def _same_person(a: str, b: str) -> bool:
    return bool(a and b and a.strip().casefold() == b.strip().casefold())


def pick_geometry(row: dict, flights: dict[str, dict], cultivos: list[dict], predios: dict[str, dict]):
    fk = _resolve_flight_key(row.get("poligono_vuelo", ""))
    if fk and fk in flights:
        return flights[fk]

    prop = row.get("propietario", "")
    nom_predio = (row.get("nom_predio") or "").lower()
    flight_raw = (row.get("poligono_vuelo") or "").upper()

    if "HERRERA" in flight_raw or _norm_key(prop) == "oscarherrera":
        for c in cultivos:
            if c.get("plot_id") == "24" or (c.get("name") or "").upper() == "NECTARINO":
                return c

    if _same_person(prop, "Alejandra Brito"):
        if "resguardo" in nom_predio and "rpa" in predios:
            return predios["rpa"]
        if "rci" in predios:
            return predios["rci"]

    if _same_person(prop, "Bruno Devoto"):
        if "rivarola" in nom_predio.lower():
            fk_dev = _flight_key("FIC-B-DEVOTO")
            if fk_dev in flights:
                return flights[fk_dev]
        if "riv" in predios:
            return predios["riv"]

    if _same_person(prop, "Claudio Alvarado"):
        for c in cultivos:
            if c.get("wetland_id") == "nog":
                return c

    for c in cultivos:
        if c.get("propietario") and _same_person(c["propietario"], prop):
            return c

    if fk and fk in flights:
        return flights[fk]
    return None


def _normalize_shape_props(raw: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key in SHAPE_SKIP_COLS:
            continue
        canon = SHAPE_ATTR_ALIASES.get(key, key)
        if canon in PROP_COLS or canon in {"fuente", "plot_id", "wetland_id"}:
            if val is None:
                text = ""
            elif isinstance(val, float):
                if math.isnan(val):
                    text = ""
                else:
                    text = str(int(val)) if val == int(val) else str(val)
            else:
                text = str(val).strip()
            out[canon] = text
    return out


def load_existing_cuarteles_by_id() -> dict[str, dict]:
    if not OUTPUT.is_file():
        return {}
    fc = json.loads(OUTPUT.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for feat in fc.get("features") or []:
        props = feat.get("properties") or {}
        cid = str(props.get("id_cuartel") or "").strip()
        if not cid:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        from shapely.geometry import shape as shp_shape

        out[cid] = {"geometry": _to_2d(shp_shape(geom)), "properties": props}
    print(f"  [geojson] {len(out)} cuarteles en {OUTPUT.name}")
    return out


def _merge_csv_and_shape(csv_row: dict, shape_entry: dict) -> dict:
    props = {col: csv_row.get(col, "") for col in PROP_COLS if col != "superficie"}
    props["superficie"] = superficie_from_geometry(shape_entry.get("geometry"))
    shape_props = shape_entry["properties"]
    for key in ("fuente", "plot_id"):
        val = shape_props.get(key, "")
        if val not in ("", None):
            props[key] = val
        else:
            props[key] = ""
    return {col: props.get(col, "") for col in GEOJSON_PROP_COLS}


def _shape_only_properties(shape_props: dict, geometry=None) -> dict:
    props = {col: shape_props.get(col, "") for col in PROP_COLS if col != "superficie"}
    if geometry is not None:
        props["superficie"] = superficie_from_geometry(geometry)
    else:
        props["superficie"] = ""
    for key in ("fuente", "plot_id"):
        props[key] = shape_props.get(key, "") or ""
    return {col: props.get(col, "") for col in GEOJSON_PROP_COLS}


def _register_rivarola_flight(flights: dict[str, dict]) -> None:
    rivarola = KML_ROOT / "Rivarola-60m.kmz"
    if not rivarola.exists():
        return
    key = _flight_key("FIC-B-DEVOTO")
    if key in flights:
        return
    polys = read_kml_or_kmz(rivarola)
    if not polys:
        return
    from shapely.ops import unary_union

    flights[key] = {
        "geometry": _to_2d(unary_union(polys)),
        "fuente": rivarola.relative_to(REPO_ROOT).as_posix(),
        "poligono_vuelo": "FIC-B-DEVOTO",
    }


def _flight_key_counts(features: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feat in features:
        fk = _resolve_flight_key(feat.get("properties", {}).get("poligono_vuelo", ""))
        if fk:
            counts[fk] = counts.get(fk, 0) + 1
    return counts


def _apply_kml_flights(features: list[dict], flights: dict[str, dict]) -> set[str]:
    """Usa KMZ cuando un único cuartel comparte el vuelo (p. ej. Contreras, Alvarado)."""
    used: set[str] = set()
    fk_counts = _flight_key_counts(features)
    for feat in features:
        props = feat.get("properties") or {}
        fk = _resolve_flight_key(props.get("poligono_vuelo", ""))
        if not fk or fk not in flights or fk_counts.get(fk, 0) != 1:
            continue
        flight = flights[fk]
        feat["geometry"] = mapping(flight["geometry"])
        props["fuente"] = flight.get("fuente", props.get("fuente"))
        used.add(fk)
        print(f"  [kml->cuartel] {props.get('id_cuartel')} <- {flight.get('poligono_vuelo')}")
    return used


def _append_orphan_kml_features(
    features: list[dict],
    flights: dict[str, dict],
    used_flight_keys: set[str],
) -> None:
    covered_flights = {
        _resolve_flight_key(f.get("properties", {}).get("poligono_vuelo", ""))
        for f in features
        if f.get("properties", {}).get("poligono_vuelo")
    }
    seen_sources: set[str] = set()
    for fk, flight in flights.items():
        resolved = _resolve_flight_key(flight.get("poligono_vuelo", ""))
        if fk in used_flight_keys or resolved in covered_flights or resolved in used_flight_keys:
            continue
        src = str(flight.get("fuente") or "")
        if src in seen_sources:
            continue
        seen_sources.add(src)
        covered_flights.add(resolved)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id_cuartel": None,
                    "poligono_vuelo": flight.get("poligono_vuelo"),
                    "fuente": flight.get("fuente"),
                },
                "geometry": mapping(flight["geometry"]),
            }
        )
        print(f"  [kml+] {flight.get('poligono_vuelo')}")


def _merge_kml_flights(features: list[dict]) -> None:
    flights = load_flight_polygons()
    _register_rivarola_flight(flights)
    used = _apply_kml_flights(features, flights)
    _append_orphan_kml_features(features, flights, used)


def _feature_from_csv_and_kml(row: dict, flights: dict[str, dict]) -> dict | None:
    fk = _resolve_flight_key(row.get("poligono_vuelo", ""))
    src = flights.get(fk) if fk else None
    if not src or src.get("geometry") is None or src["geometry"].is_empty:
        return None
    props = {col: row.get(col, "") for col in PROP_COLS if col != "superficie"}
    props["superficie"] = superficie_from_geometry(src["geometry"])
    props["fuente"] = src.get("fuente", "")
    props["plot_id"] = ""
    return {
        "type": "Feature",
        "properties": {col: props.get(col, "") for col in GEOJSON_PROP_COLS},
        "geometry": mapping(src["geometry"]),
    }


def _build_from_existing_and_csv(existing: dict[str, dict], rows: list[dict]) -> list[dict]:
    """Exporta cuarteles.geojson desde geometrías existentes + fic_database.csv + KMZ."""
    features: list[dict] = []
    csv_ids: set[str] = set()
    flights = load_flight_polygons()
    _register_rivarola_flight(flights)

    for row in rows:
        cid = row.get("id_cuartel")
        if not cid:
            continue
        csv_ids.add(cid)
        if cid in existing:
            entry = existing[cid]
            features.append(
                {
                    "type": "Feature",
                    "properties": _merge_csv_and_shape(row, entry),
                    "geometry": mapping(entry["geometry"]),
                }
            )
            continue

        feat = _feature_from_csv_and_kml(row, flights)
        if feat:
            print(f"  [kml+csv] {cid} <- {row.get('poligono_vuelo')}")
            features.append(feat)
        else:
            print(f"  [warn] {cid} en CSV sin geometría ({row.get('propietario')})")

    for cid, entry in sorted(existing.items()):
        if cid in csv_ids:
            continue
        print(f"  [warn] {cid} en cuarteles.geojson pero no en fic_database.csv")
        features.append(
            {
                "type": "Feature",
                "properties": _shape_only_properties(entry["properties"], entry["geometry"]),
                "geometry": mapping(entry["geometry"]),
            }
        )

    used = _apply_kml_flights(features, flights)
    _append_orphan_kml_features(features, flights, used)
    return features


def _build_legacy_features(master: dict[str, dict], rows: list[dict]) -> list[dict]:
    flights = load_flight_polygons()
    cultivos = load_cultivo_polygons()
    predios = {
        "rci": load_predio_geojson("predio_RCI.geojson", "rci"),
        "rpa": load_predio_geojson("predio_RPA.geojson", "rpa"),
        "riv": load_predio_geojson("predio_RIV.geojson", "riv"),
    }
    predios = {k: v for k, v in predios.items() if v}

    # Rivarola KMZ también como vuelo Bruno Devoto si el nombre del archivo no coincide con FIC-B-DEVOTO
    rivarola = KML_ROOT / "Rivarola-60m.kmz"
    if rivarola.exists():
        key = _flight_key("FIC-B-DEVOTO")
        if key not in flights:
            polys = read_kml_or_kmz(rivarola)
            if polys:
                from shapely.ops import unary_union

                flights[key] = {
                    "geometry": _to_2d(unary_union(polys)),
                    "fuente": rivarola.relative_to(REPO_ROOT).as_posix(),
                    "poligono_vuelo": "FIC-B-DEVOTO",
                }

    features: list[dict] = []
    used_flight_keys: set[str] = set()
    csv_ids: set[str] = set()

    for row in rows:
        cid = row.get("id_cuartel")
        if cid:
            csv_ids.add(cid)
        if cid and cid in master:
            entry = master[cid]
            props = _merge_csv_and_shape(row, entry)
            fk = _resolve_flight_key(props.get("poligono_vuelo", ""))
            if fk:
                used_flight_keys.add(fk)
            features.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": mapping(entry["geometry"]),
                }
            )
            continue

        src = pick_geometry(row, flights, cultivos, predios)
        if not src or src.get("geometry") is None or src["geometry"].is_empty:
            print(f"  [warn] sin geometría para {row['id_cuartel']} ({row.get('propietario')})")
            continue
        props = dict(row)
        props["fuente"] = src.get("fuente")
        if src.get("wetland_id"):
            props["wetland_id"] = src["wetland_id"]
        if src.get("plot_id"):
            props["plot_id"] = src["plot_id"]
        fk = _resolve_flight_key(row.get("poligono_vuelo", ""))
        if fk:
            used_flight_keys.add(fk)
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": mapping(src["geometry"]),
            }
        )

    # Cuarteles sólo en predios_fic.shp (p. ej. c00023 Siebenthal).
    for cid, entry in sorted(master.items()):
        if cid in csv_ids:
            continue
        props = dict(entry["properties"])
        fk = _resolve_flight_key(props.get("poligono_vuelo", ""))
        if fk:
            used_flight_keys.add(fk)
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": mapping(entry["geometry"]),
            }
        )

    # KMZ/KML sin fila en CSV ni en predios_fic.shp
    covered_flights = {
        _resolve_flight_key(f.get("properties", {}).get("poligono_vuelo", ""))
        for f in features
        if f.get("properties", {}).get("poligono_vuelo")
    }
    for fk, flight in flights.items():
        if fk in used_flight_keys:
            continue
        flight_code = _flight_key(flight.get("poligono_vuelo", ""))
        if flight_code in covered_flights or fk in covered_flights:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id_cuartel": None,
                    "poligono_vuelo": flight.get("poligono_vuelo"),
                    "fuente": flight.get("fuente"),
                },
                "geometry": mapping(flight["geometry"]),
            }
        )

    return features


def build_features() -> list[dict]:
    existing = load_existing_cuarteles_by_id()
    rows = load_csv_rows()
    missing = [r["id_cuartel"] for r in rows if r.get("id_cuartel") not in existing]
    if missing:
        print(f"  [mode] cuarteles.geojson + fic_database.csv (+ {len(missing)} desde KMZ)")
    else:
        print("  [mode] cuarteles.geojson + fic_database.csv")
    return _build_from_existing_and_csv(existing, rows)


def main() -> None:
    print("Construyendo cuarteles.geojson …")
    features = build_features()
    if not features:
        raise SystemExit("No se generó ningún polígono.")

    fc = {
        "type": "FeatureCollection",
        "name": "cuarteles",
        "features": features,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Listo: {OUTPUT.relative_to(REPO_ROOT)} ({len(features)} features)")


if __name__ == "__main__":
    main()
