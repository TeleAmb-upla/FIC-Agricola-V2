#!/usr/bin/env python3
"""
Estima el tamaño de un asset semanal S2_weekly_valpo según el esquema de cuantización.

Uso::

    python scripts/gee/estimate_asset_storage.py
    python scripts/gee/estimate_asset_storage.py --width 16971 --height 20308 --mb 305.8
"""
from __future__ import annotations

import argparse

from export_s2 import COMPOSED_INDEX_BANDS, band_storage_dtype

CLEAR_PIXEL_COUNT_BYTES = {"int8": 1, "int16": 2, "int64": 8}


def bytes_per_pixel_legacy() -> int:
    """Esquema anterior: 9×Int16 + clear_pixel_count Int64."""
    return len(COMPOSED_INDEX_BANDS) * 2 + CLEAR_PIXEL_COUNT_BYTES["int64"]


def bytes_per_pixel_optimized() -> tuple[int, dict[str, int]]:
    """Esquema optimizado: Int8 en índices normalizados + Int16 solo donde hace falta."""
    per_band: dict[str, int] = {}
    total = 0
    for band in COMPOSED_INDEX_BANDS:
        b = 2 if band_storage_dtype(band) == "int16" else 1
        per_band[band] = b
        total += b
    per_band["clear_pixel_count"] = CLEAR_PIXEL_COUNT_BYTES["int8"]
    total += CLEAR_PIXEL_COUNT_BYTES["int8"]
    return total, per_band


def fmt_mb(n_bytes: float) -> str:
    return f"{n_bytes / 1024 / 1024:.1f} MB"


def main() -> None:
    ap = argparse.ArgumentParser(description="Estima ahorro de almacenamiento S2 en GEE.")
    ap.add_argument("--width", type=int, default=16971, help="Ancho del asset (píxeles).")
    ap.add_argument("--height", type=int, default=20308, help="Alto del asset (píxeles).")
    ap.add_argument(
        "--mb",
        type=float,
        default=305.8,
        help="Tamaño observado del asset actual en GEE (MB), p. ej. Y2018_W01.",
    )
    ap.add_argument("--weeks", type=int, default=440, help="Semanas en la colección (aprox.).")
    args = ap.parse_args()

    n_pix = args.width * args.height
    legacy_bpp = bytes_per_pixel_legacy()
    opt_bpp, per_band = bytes_per_pixel_optimized()

    legacy_raw = n_pix * legacy_bpp
    opt_raw = n_pix * opt_bpp
    ratio = opt_bpp / legacy_bpp

    # Escalar el tamaño observado (comprimido en GEE) proporcionalmente.
    est_legacy = args.mb
    est_opt = args.mb * ratio
    est_only_clear = args.mb * ((legacy_bpp - 7) / legacy_bpp)  # Int64→Int8 solo en clear

    print("=== Esquema de bandas (optimizado) ===")
    for band in COMPOSED_INDEX_BANDS:
        dtype = band_storage_dtype(band)
        print(f"  {band:18s}  {dtype.upper():5s}  ({per_band[band]} B/px)")
    print(f"  {'clear_pixel_count':18s}  INT8   ({per_band['clear_pixel_count']} B/px)")

    print(f"\n=== Por asset ({args.width} x {args.height} = {n_pix:,} px) ===")
    print(f"  Sin comprimir legacy:  {legacy_bpp} B/px -> {fmt_mb(legacy_raw)}")
    print(f"  Sin comprimir nuevo:   {opt_bpp} B/px -> {fmt_mb(opt_raw)}")
    print(f"  Observado legacy GEE:  {est_legacy:.1f} MB  (referencia)")
    print(f"  Estimado nuevo GEE:    {est_opt:.1f} MB  (~{(1 - ratio) * 100:.0f}% menos)")
    print(f"  Solo Int64->Int8:      {est_only_clear:.1f} MB  (~{(1 - (legacy_bpp-7)/legacy_bpp)*100:.0f}% menos)")

    print(f"\n=== Colección (~{args.weeks} semanas) ===")
    print(f"  Legacy estimado:   {est_legacy * args.weeks / 1024:.1f} GB")
    print(f"  Optimizado est.:   {est_opt * args.weeks / 1024:.1f} GB")
    print(f"  Ahorro estimado:   {(est_legacy - est_opt) * args.weeks / 1024:.1f} GB")

    print("\n=== Precisión ===")
    print("  Indices normalizados: resolucion 0.01 (escala x100, Int8)")
    print("  PSRI:                 resolucion 0.1  (escala x10,  Int8)")
    print("  REDEDGE_POSITION:     resolucion 0.1 nm (escala x10, Int16) — unica Int16")
    print("  clear_pixel_count:    entero 0-127 (Int8)")


if __name__ == "__main__":
    main()
