"""
Rutas del proyecto y carpetas en Google Drive para exportaciones S2 y S1.

El nombre ``DRIVE_S2_EXPORT_FOLDER`` debe coincidir **exactamente** con la carpeta en Drive
(la que usa ``Export.image.toDrive(..., folder=...)`` en ``export_s2.py``, tras
``sanitize_drive_folder_name``). Por defecto ``FIC_RASTER_S2_semanales_por_predio`` (convención
histórica: raíz + sufijo de semanales). Sobrescribir: variable de entorno ``FIC_DRIVE_S2_EXPORT_FOLDER``.
"""
from __future__ import annotations

import os
from pathlib import Path

# Raíz del proyecto fic_agro (directorio que contiene ``scripts/`` y ``data/``).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Todo el material Sentinel-2 del repo va en un solo directorio (sin mosaics/indices/aux).
REPO_S2_DIR = PROJECT_ROOT / "data" / "sentinel2"

# Una sola carpeta en Drive (misma que usa export_s2.py para semanales y compuestos).
DRIVE_S2_EXPORT_FOLDER = (
    os.environ.get("FIC_DRIVE_S2_EXPORT_FOLDER", "").strip()
    or "FIC_RASTER_S2_semanales_por_predio"
)

# Earth Engine — proyecto Google Cloud para ``ee.Initialize(project=...)`` (API / facturación).
# Debe ser el ID del proyecto GCP registrado en Earth Engine (consola EE → Configuration).
# No confundir con el nombre del repo ``fic_agro`` ni con el esquema JSON ``fic-agro/...``.
# Sobrescribir: ``EE_CLOUD_PROJECT`` o ``GOOGLE_CLOUD_PROJECT``.
DEFAULT_EE_CLOUD_PROJECT = "teleambagr"

# ImageCollection semanal con assets ``Y{year}_W{week}`` (índices compuestos por semana ISO).
# Sobrescribir: ``GEE_STATS_COLLECTION``.
DEFAULT_S2_WEEKLY_COLLECTION = "projects/teleambagr/assets/S2_weekly_valpo"
DEFAULT_S1_WEEKLY_COLLECTION = "projects/teleambagr/assets/S1_weekly_valpo"
DEFAULT_SCALE_M = 10.0
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

REPO_S1_DIR = PROJECT_ROOT / "data" / "sentinel1"
DRIVE_S1_EXPORT_FOLDER = (
    os.environ.get("FIC_DRIVE_S1_EXPORT_FOLDER", "").strip()
    or "FIC_RASTER_S1_semanales"
)

GEE_COLLECTION = (
    os.environ.get("GEE_STATS_COLLECTION", "").strip()
    or DEFAULT_S2_WEEKLY_COLLECTION
)
GEE_S1_COLLECTION = (
    os.environ.get("GEE_S1_COLLECTION", "").strip()
    or DEFAULT_S1_WEEKLY_COLLECTION
)


def resolve_ee_cloud_project(cli: str | None = None) -> str:
    """Proyecto GCP para ``ee.Initialize``: CLI > env > default FIC."""
    if cli and str(cli).strip():
        return str(cli).strip()
    for key in ("EE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return DEFAULT_EE_CLOUD_PROJECT


GEE_CLOUD_PROJECT = resolve_ee_cloud_project()
