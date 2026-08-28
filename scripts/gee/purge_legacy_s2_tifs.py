#!/usr/bin/env python3
"""Elimina TIF locales S2 con formato legacy (≠10 bandas operativas)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "gee"))

from export_s2_predio_local import EXPECTED_BAND_COUNT, COMPOSED_BANDS, purge_legacy_local_tifs


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            f"Elimina data/sentinel2/S2_*.tif con ≠{EXPECTED_BAND_COUNT} bandas. "
            f"Formato válido: {', '.join(COMPOSED_BANDS)}"
        )
    )
    ap.add_argument(
        "--dest",
        type=Path,
        default=REPO / "data" / "sentinel2",
        help="Carpeta con TIF semanales por predio.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    n = purge_legacy_local_tifs(args.dest, dry_run=args.dry_run)
    verb = "eliminaría" if args.dry_run else "eliminados"
    print(f"{verb}: {n} archivo(s)")


if __name__ == "__main__":
    main()
