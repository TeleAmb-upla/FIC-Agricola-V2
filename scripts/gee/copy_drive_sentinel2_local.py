"""
CLI: copia carpetas Sentinel-2 desde Google Drive al repo fic_agro.

Uso (desde la raíz ``fic_agro/``):

    python scripts/gee/copy_drive_sentinel2_local.py --dry-run
    python scripts/gee/copy_drive_sentinel2_local.py --only s2

Requisitos: ``earthengine authenticate`` y dependencias en ``requirements.txt``.
La carpeta de Drive y el destino local están en ``scripts/gee/paths.py`` (una sola carpeta).
Sobrescribir el nombre de carpeta en Drive: variable de entorno ``FIC_DRIVE_S2_EXPORT_FOLDER``.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    _repo = Path(__file__).resolve().parents[2]
    _repo_str = str(_repo)
    if _repo_str not in sys.path:
        sys.path.insert(0, _repo_str)
    __package__ = "scripts.gee"

from .drive_sync import main

if __name__ == "__main__":
    main()
