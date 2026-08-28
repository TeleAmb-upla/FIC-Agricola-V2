"""Rutas estándar bajo ``data/vectors/``."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VECTORS_ROOT = REPO_ROOT / "data" / "vectors"
KML_ROOT = VECTORS_ROOT / "kml"
VUELOS_ROOT = VECTORS_ROOT / "vuelos"
CUARTELES_ROOT = VECTORS_ROOT / "cuarteles"
COMUNAS_ROOT = VECTORS_ROOT / "comunas"

CUARTELES_GEOJSON = CUARTELES_ROOT / "cuarteles.geojson"
COMUNAS_SHP = COMUNAS_ROOT / "comunas.shp"
VUELOS_GEOJSON = VUELOS_ROOT / "vuelos.geojson"

STATIC_VECTORS_ROOT = REPO_ROOT / "data_static" / "vectors"
STATIC_CUARTELES_GEOJSON = STATIC_VECTORS_ROOT / "cuarteles" / "cuarteles.geojson"
STATIC_CUARTELES_DISPLAY = STATIC_VECTORS_ROOT / "cuarteles" / "cuarteles_display.geojson"
