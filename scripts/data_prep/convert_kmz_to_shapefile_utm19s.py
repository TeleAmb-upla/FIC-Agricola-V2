#!/usr/bin/env python3
"""
Convierte cada .kmz de una carpeta a shapefile en WGS 84 / UTM zona 19 sur (EPSG:32719).

Los KMZ de DJI suelen llevar ``wpmz/template.kml`` y geometrías con anillos no cerrados;
se usa ``OGR_GEOMETRY_ACCEPT_UNCLOSED_RING=YES`` para que GDAL las cierre al importar.

Requiere GDAL en PATH (ogr2ogr). Ejemplo::

    python scripts/data_prep/convert_kmz_to_shapefile_utm19s.py --input-dir data/shapefiles
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


EPSG_UTM19S = "EPSG:32719"


def _pref_kml_inside_kmz(names: list[str]) -> str:
    lowered = [(n.replace("\\", "/"), n.replace("\\", "/").lower()) for n in names]
    for pref in ("wpmz/template.kml", "doc.kml"):
        for full, low in lowered:
            if low.endswith(pref):
                return full
    kml = [n for n in names if n.lower().endswith(".kml")]
    if not kml:
        raise ValueError("El KMZ no contiene ningún archivo .kml.")
    return kml[0]


def convert_one(kmz_path: Path, output_root: Path) -> Path | None:
    kmz_path = kmz_path.resolve()
    stem = kmz_path.stem

    import zipfile

    with zipfile.ZipFile(kmz_path, "r") as z:
        inner_kml = _pref_kml_inside_kmz(z.namelist())

    p = kmz_path.resolve().as_posix()
    # GDAL requiere /vsizip//ruta/absoluta/archivo.zip/interior.kml
    vsizip = f"/vsizip//{p}"
    src = f"{vsizip}/{inner_kml}"

    out_dir = output_root / f"{stem}_utm19s"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_shp = out_dir / f"{stem}.shp"

    ogr2ogr = shutil.which("ogr2ogr")
    if not ogr2ogr:
        print("ogr2ogr no encontrado en PATH (instala gdal-bin).", file=sys.stderr)
        sys.exit(2)

    env = {
        **os.environ.copy(),
        "OGR_GEOMETRY_ACCEPT_UNCLOSED_RING": "YES",
    }

    cmd = [
        ogr2ogr,
        "-overwrite",
        "-t_srs",
        EPSG_UTM19S,
        "-f",
        "ESRI Shapefile",
        "-lco",
        "ENCODING=UTF-8",
        str(out_shp),
        src,
    ]
    subprocess.run(cmd, env=env, check=True)
    try:
        rel = out_shp.resolve().relative_to(output_root.resolve())
    except ValueError:
        rel = out_shp
    print(f"[ok] {kmz_path.name} → {rel}")

    return out_shp


def main() -> None:
    parser = argparse.ArgumentParser(description="KMZ → shapefile EPSG:32719 (UTM 19S).")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/shapefiles"),
        help="Carpeta que contiene los *.kmz (por defecto: data/shapefiles)",
    )
    parser.add_argument(
        "--glob",
        default="*.kmz",
        help="Patrón de archivo (por defecto: *.kmz)",
    )
    args = parser.parse_args()

    input_dir = args.input_dir
    if not input_dir.is_dir():
        print(f"No existe la carpeta: {input_dir}", file=sys.stderr)
        sys.exit(1)

    kmzs = sorted(input_dir.glob(args.glob))
    if not kmzs:
        print(f"No hay archivos {args.glob} en {input_dir}", file=sys.stderr)
        sys.exit(1)

    for kmz in kmzs:
        try:
            convert_one(kmz, input_dir)
        except subprocess.CalledProcessError as e:
            print(f"[error] {kmz.name}: ogr2ogr falló (código {e.returncode})", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"[error] {kmz.name}: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
