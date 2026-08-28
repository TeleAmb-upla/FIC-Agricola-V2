#!/usr/bin/env python3
"""
Genera ``data/vectors/vuelos/vuelos.geojson`` compilando todos los KMZ/KML de ``data/vectors/kml/``.

Uso::

    python scripts/data_prep/build_vuelos_geojson.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from shapely.geometry import mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data_prep.build_predios_geojson import (  # noqa: E402
    _register_rivarola_flight,
    load_flight_polygons,
)
from scripts.data_prep.vectors_paths import KML_ROOT, VUELOS_GEOJSON, VUELOS_ROOT


def main() -> None:
    if not KML_ROOT.is_dir():
        raise SystemExit(f"No existe {KML_ROOT.relative_to(REPO_ROOT)}")

    if VUELOS_ROOT.exists():
        for path in VUELOS_ROOT.iterdir():
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)

    VUELOS_ROOT.mkdir(parents=True, exist_ok=True)

    flights = load_flight_polygons()
    _register_rivarola_flight(flights)

    features: list[dict] = []
    seen_sources: set[str] = set()

    for _fk, flight in sorted(flights.items(), key=lambda item: str(item[1].get("poligono_vuelo") or "")):
        src = str(flight.get("fuente") or "")
        if src in seen_sources:
            continue
        seen_sources.add(src)
        stem = str(flight.get("poligono_vuelo") or "").upper()
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "poligono_vuelo": stem,
                    "fuente": flight.get("fuente"),
                },
                "geometry": mapping(flight["geometry"]),
            }
        )
        print(f"  [vuelo] {stem}")

    VUELOS_GEOJSON.write_text(
        json.dumps(
            {"type": "FeatureCollection", "name": "vuelos", "features": features},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Listo: {len(features)} vuelos -> {VUELOS_GEOJSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
