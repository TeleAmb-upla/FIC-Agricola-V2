#!/usr/bin/env python3
"""
Escaneo ligero de data/drone: detecta códigos de predio temporal tipo G1, G12 a partir del nombre
de carpetas/archivos (no abre rásteres).

Uso desde la raíz del repo::

  python scripts/data_prep/scan_drone_predio_codes.py

La carpeta data/ suele estar en .gitignore; el script igual crea rutas relativas locales.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Ej.: G1, G02, G12, NOG … al inicio de segmento de ruta o en nombre de archivo
CODE_RE = re.compile(r"\b((?:G\d+)|(?:NOG))\b", re.IGNORECASE)


def iter_paths(root: Path) -> None:
    if not root.exists():
        print(f"[WARN] No existe {root}: crea la carpeta y coloca ahí ortofotos o subcarpetas por predio.", file=sys.stderr)
        return
    codes: set[str] = set()
    for p in root.rglob("*"):
        if not p.name or p.name.startswith("."):
            continue
        rel = p.relative_to(root)
        for segment in [*rel.parts, p.stem]:
            for m in CODE_RE.finditer(segment):
                codes.add(m.group(1).upper())
        for m in CODE_RE.finditer(p.name):
            codes.add(m.group(1).upper())
    print("predio_temporal (deducidos):")
    for c in sorted(codes, key=lambda x: (len(x), x)):
        slug = c.lower()
        print(f"  {c}  -> wetland_id sugerido en config.yaml: '{slug}'")
    if not codes:
        print("  (sin coincidencias; nombra con patrón G+número o NOG)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Listar códigos G* en data/drone.")
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "drone",
        help="Carpeta raíz de dron (default: data/drone).",
    )
    args = ap.parse_args()
    iter_paths(args.root)


if __name__ == "__main__":
    main()
