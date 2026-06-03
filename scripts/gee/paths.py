"""
Rutas del proyecto y carpeta única en Google Drive para exportaciones S2.

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
