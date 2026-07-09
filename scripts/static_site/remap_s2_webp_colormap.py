#!/usr/bin/env python3
"""
Re-mapea WebPs Sentinel-2 ya renderizados cuando cambia el colormap en metadata.

Útil sin GeoTIFF fuente: cada píxel opaco proviene de una LUT de 256 colores,
así que se puede traducir color_antiguo → color_nuevo por índice normalizado.

Ejemplo NDMI: RdYlBu_r → RdYlBu (bajo=rojo, alto=azul).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from matplotlib import colormaps as mpl_cmaps
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RASTERS_DIR = REPO_ROOT / "data_static" / "sentinel2" / "rasters"


def _lut_rgba(name: str) -> np.ndarray:
    cmap = mpl_cmaps[name]
    return (cmap(np.linspace(0.0, 1.0, 256)) * 255).astype(np.uint8)


def _build_rgb_remap(from_cmap: str, to_cmap: str) -> dict[tuple[int, int, int], tuple[int, int, int]]:
    """Para cada nivel i de la LUT origen, el color pasa al nivel i de la LUT destino."""
    lut_from = _lut_rgba(from_cmap)
    lut_to = _lut_rgba(to_cmap)
    out: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for i in range(256):
        fr = tuple(int(x) for x in lut_from[i, :3])
        tr = tuple(int(x) for x in lut_to[i, :3])
        out[fr] = tr
    return out


def remap_webp(path: Path, from_cmap: str, to_cmap: str, *, dry_run: bool = False) -> bool:
    lut_from_rgb = _lut_rgba(from_cmap)[:, :3]
    lut_to_rgb = _lut_rgba(to_cmap)[:, :3]

    img = Image.open(path).convert("RGBA")
    arr = np.array(img, copy=True)
    flat = arr.reshape(-1, 4)
    rgb = flat[:, :3]
    alpha = flat[:, 3]
    opaque = alpha > 0
    if not np.any(opaque):
        return False

    changed = False
    for i in range(256):
        src = lut_from_rgb[i]
        mask = opaque & (rgb[:, 0] == src[0]) & (rgb[:, 1] == src[1]) & (rgb[:, 2] == src[2])
        if not np.any(mask):
            continue
        dst = lut_to_rgb[i]
        if not np.array_equal(src, dst):
            changed = True
            if dry_run:
                return True
            flat[mask, 0] = dst[0]
            flat[mask, 1] = dst[1]
            flat[mask, 2] = dst[2]

    if not changed:
        # Colores fuera de la LUT (compresión): re-mapear por índice más cercano.
        near_mask = opaque.copy()
        for i in range(256):
            src = lut_from_rgb[i]
            exact = near_mask & (rgb[:, 0] == src[0]) & (rgb[:, 1] == src[1]) & (rgb[:, 2] == src[2])
            near_mask &= ~exact
        if np.any(near_mask):
            idx = _nearest_lut_index_batch(rgb[near_mask], lut_from_rgb)
            new_rgb = lut_to_rgb[idx]
            if dry_run:
                return True
            flat[near_mask, 0:3] = new_rgb
            changed = True

    if not changed:
        return False
    if dry_run:
        return True

    out = Image.fromarray(arr, mode="RGBA")
    out.save(path, format="WEBP", quality=86, method=4)
    return True


def _nearest_lut_index_batch(rgb: np.ndarray, lut_rgb: np.ndarray) -> np.ndarray:
    diff = rgb.astype(np.int16)[:, None, :] - lut_rgb.astype(np.int16)[None, :, :]
    return np.argmin(np.sum(diff * diff, axis=2), axis=1).astype(np.int16)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-mapea colormap en WebPs S2 existentes.")
    parser.add_argument(
        "--rasters-dir",
        type=Path,
        default=DEFAULT_RASTERS_DIR,
        help="Carpeta data_static/sentinel2/rasters",
    )
    parser.add_argument("--band", default="NDMI", help="Sufijo de banda en el nombre del archivo.")
    parser.add_argument("--from-cmap", default="RdYlBu_r", help="Colormap con el que se generaron los WebPs.")
    parser.add_argument("--to-cmap", default="RdYlBu", help="Colormap destino (debe coincidir con metadata).")
    parser.add_argument("--dry-run", action="store_true", help="Solo contar archivos afectados.")
    args = parser.parse_args(argv)

    rasters_dir = Path(args.rasters_dir)
    if not rasters_dir.is_dir():
        print(f"No existe {rasters_dir}", file=sys.stderr)
        return 1

    pattern = f"*_{args.band.upper()}.webp"
    files = sorted(rasters_dir.glob(pattern))
    if not files:
        pattern = f"*_{args.band.lower()}.webp"
        files = sorted(rasters_dir.glob(pattern))
    if not files:
        print(f"Sin WebPs {args.band} en {rasters_dir}", file=sys.stderr)
        return 1

    print(
        f"Remapeo {args.band}: {args.from_cmap} -> {args.to_cmap} | "
        f"{len(files)} archivo(s){' (dry-run)' if args.dry_run else ''}"
    )
    n_ok = 0
    for i, fp in enumerate(files, 1):
        try:
            if remap_webp(fp, args.from_cmap, args.to_cmap, dry_run=args.dry_run):
                n_ok += 1
        except (OSError, ValueError) as exc:
            print(f"  [error] {fp.name}: {exc}", file=sys.stderr)
        if i % 100 == 0 or i == len(files):
            print(f"  ... {i}/{len(files)} procesados, {n_ok} actualizados", flush=True)

    print(f"Listo: {n_ok} WebP(s) {'serían ' if args.dry_run else ''}actualizados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
