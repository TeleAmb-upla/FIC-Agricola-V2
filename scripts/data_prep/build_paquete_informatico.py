#!/usr/bin/env python3
"""Empaqueta inputs y muestras de outputs para el equipo de automatización."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "documentación" / "PAQUETE_INFORMATICO"
ZIP_PATH = REPO / "documentación" / "FIC_Agro_paquete_informatico.zip"


def copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copy2(src, dst)
        print(f"  + {dst.relative_to(OUT)}")


def write_json_excerpt(src: Path, dst: Path, max_keys: int = 2) -> None:
    data = json.loads(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        keys = list(data.keys())[:max_keys]
        excerpt = {k: data[k] for k in keys}
        excerpt["_nota"] = f"Extracto de {src.name}: primeras {len(keys)} claves de {len(data)}"
    else:
        excerpt = data[:3] if isinstance(data, list) else data
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(excerpt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  + {dst.relative_to(OUT)} (extracto)")


def sample_files(folder: Path, pattern: str, n: int = 3) -> list[Path]:
    return sorted(folder.glob(pattern))[:n]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # Doc + config
    shutil.copy2(REPO / "documentación" / "PAQUETE_INFORMATICO_LEEME.md", OUT / "LEEME.md")
    copy(REPO / "config.yaml", OUT / "config.yaml")
    copy(REPO / "requirements.txt", OUT / "requirements.txt")

    # Inputs
    copy(REPO / "data" / "fic_database.csv", OUT / "01_inputs" / "data" / "fic_database.csv")
    copy(
        REPO / "data" / "vectors" / "cuarteles" / "cuarteles.geojson",
        OUT / "01_inputs" / "data" / "vectors" / "cuarteles" / "cuarteles.geojson",
    )
    copy(
        REPO / "data" / "vectors" / "vuelos" / "vuelos.geojson",
        OUT / "01_inputs" / "data" / "vectors" / "vuelos" / "vuelos.geojson",
    )
    copy(REPO / "data_static" / "predios_aoi.geojson", OUT / "01_inputs" / "data_static" / "predios_aoi.geojson")

    kml_dir = REPO / "data" / "vectors" / "kml"
    for kmz in sample_files(kml_dir, "FIC-*.kmz", 3):
        copy(kmz, OUT / "01_inputs" / "data" / "vectors" / "kml" / kmz.name)

    # Sentinel-2 outputs
    s2 = REPO / "data_static" / "sentinel2"
    copy(s2 / "metadata.json", OUT / "02_outputs_sentinel2" / "metadata.json")
    write_json_excerpt(s2 / "timeseries.json", OUT / "02_outputs_sentinel2" / "timeseries_extracto.json", 2)
    for f in sample_files(s2 / "csv", "*.csv", 3):
        copy(f, OUT / "02_outputs_sentinel2" / "csv" / f.name)
    for f in sample_files(s2 / "rasters", "*.webp", 3):
        copy(f, OUT / "02_outputs_sentinel2" / "rasters" / f.name)

    # Local S2 intermediate (if any)
    local_s2 = list(sample_files(REPO / "data" / "sentinel2", "S2_*.tif", 3))
    if local_s2:
        for f in local_s2:
            copy(f, OUT / "02_outputs_sentinel2" / "intermediate_tif" / f.name)
    else:
        note = OUT / "02_outputs_sentinel2" / "NOTA_tif_intermedios.txt"
        note.write_text(
            "data/sentinel2/ estaba vacío al empaquetar.\n"
            "Los TIF se generan con:\n"
            "  python scripts/gee/export_s2_predio_local.py --reference E_SAZO --sync-ee-weeks --fill-missing-weeks\n"
            "Patrón: data/sentinel2/S2_{S2_CODE}_Y{AAAA}_W{SS}.tif (10 bandas)\n",
            encoding="utf-8",
        )
        print(f"  + {note.relative_to(OUT)}")

    # Drone outputs
    dr = REPO / "data_static" / "drone"
    copy(dr / "metadata.json", OUT / "03_outputs_drone" / "metadata.json")
    write_json_excerpt(dr / "timeseries.json", OUT / "03_outputs_drone" / "timeseries_extracto.json", 2)
    for f in sample_files(dr / "csv", "*.csv", 3):
        copy(f, OUT / "03_outputs_drone" / "csv" / f.name)
    for f in sample_files(dr / "rasters", "*.webp", 3):
        copy(f, OUT / "03_outputs_drone" / "rasters" / f.name)
    pc = sample_files(dr / "pointclouds", "*.json", 1)
    if pc:
        copy(pc[0], OUT / "03_outputs_drone" / "pointclouds" / pc[0].name)

    # Shared static
    copy(REPO / "data_static" / "sources_manifest.json", OUT / "04_outputs_shared" / "sources_manifest.json")
    copy(REPO / "data_static" / "fic_database.csv", OUT / "04_outputs_shared" / "fic_database.csv")
    copy(REPO / "data_static" / "cuarteles_index.json", OUT / "04_outputs_shared" / "cuarteles_index.json")
    copy(
        REPO / "data_static" / "vectors" / "cuarteles" / "cuarteles.geojson",
        OUT / "04_outputs_shared" / "vectors" / "cuarteles" / "cuarteles.geojson",
    )

    # ZIP
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(OUT.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(OUT.parent).as_posix())

    mb = ZIP_PATH.stat().st_size / 1024 / 1024
    print(f"\nOK {ZIP_PATH} ({mb:.2f} MB)")


if __name__ == "__main__":
    main()
