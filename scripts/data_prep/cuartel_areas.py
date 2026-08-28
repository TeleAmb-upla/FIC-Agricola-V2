"""Superficie (ha) derivada de geometrías WGS84 de cuarteles."""
from __future__ import annotations

import geopandas as gpd


def area_ha_wgs84(geom) -> float:
    gdf = gpd.GeoDataFrame([1], geometry=[geom], crs="EPSG:4326")
    return float(gdf.to_crs(32719).geometry.area.iloc[0] / 10000.0)


def format_superficie_ha(ha: float) -> str:
    if ha <= 0:
        return ""
    return f"{ha:.5f}".rstrip("0").rstrip(".")


def superficie_from_geometry(geom) -> str:
    if geom is None or geom.is_empty:
        return ""
    return format_superficie_ha(area_ha_wgs84(geom))
