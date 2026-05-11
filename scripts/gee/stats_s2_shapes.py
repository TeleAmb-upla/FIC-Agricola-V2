#!/usr/bin/env python3
"""
Estadísticas zonal sobre mosaicos semanales S2 ya exportados a Earth Engine
(la misma convención que ``export_s2.py``: ``year``, ``week``, bandas tipo ``NDVI``, …).

Carga geometrías de predios desde ``data/shapefiles/aoi.geojson`` (por defecto) o desde
``.shp`` bajo ese directorio, recorre cada imagen de la colección y calcula por predio:

- reducción espacial (**media**) de la banda elegida en cada semana (ya es mediana temporal
  en el pipeline de exportación).

**Serie anual** (solo **años calendario completos** en el explorador JSON, ``≤ año_actual − 1``):

  Por predio: **mediana** semanal dentro del año (sobre valores de media espacial ya agregados).
  Entre predios: **mediana central**, **P25** y **P75** de esas medianas predio-año.

**Serie mensual**:
  Por predio y (año, mes calendario): promedio de las medias semanales cuya
  ``system:time_start`` cae en ese mes.
  - Columna histórica: **mediana** de todos los valores (todos los predios y todos los
    años **<= último año completo**, excluido **el año civil actual**).

  ``último año completo``: ``año_actual - 1`` (p. ej. en 2026 el histórico usa hasta 2025).
  - Columna año actual: **promedio** entre predios de ese mes del año civil en curso.

Salida: CSV (anual/mensual) y gráficos PNG opcionales (matplotlib).

Extracción zonal tipo ``genius_upla/scripts/gee/products/ndvi`` (solo raster + CSV/table):

- Zonal por semana: ``im.select(banda).addBands(ee.Image(1)).reduceRegions(..., ee.Reducer.mean())``
  como en NDVI yearly GeoJSON/table; así el mean es coherente con ese pipeline.
- Resumen tabular local: una ``getInfo()`` por **año** (colección flatten de todas las semanas).
- Opcional Drive: CSV zonal año a año (``Export.table.toDrive``, ``CSV``) y raster anual median
  (``Export.image.toDrive``, ``EPSG:4326``, escala m, NDVI escalado ``int16`` / divisor 10000
  igual que NDVI yearly en genius_upla).

Autenticación: ``earthengine authenticate``; proyecto: ``EE_CLOUD_PROJECT`` / ``--project``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import ee
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping
from shapely.ops import unary_union

DEFAULT_CLOUD_PROJECT = "ee-javiermedinam"
DEFAULT_COLLECTION = "projects/ee-javiermedinam/assets/S2_weekly_walpo"
DEFAULT_SHAPES = "data/shapefiles/aoi.geojson"
DEFAULT_BAND = "NDVI"

MONTH_NAMES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def resolve_cloud_project(cli_project: str | None) -> str:
    if cli_project is not None and cli_project.strip() != "":
        return cli_project.strip()
    for key in ("EE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return DEFAULT_CLOUD_PROJECT


def ee_initialize(project: str) -> None:
    ee.Initialize(project=project)


def load_features_from_aoi_geojson(path: Path, *, exclude_patterns: tuple[str, ...]) -> tuple[gpd.GeoDataFrame, ee.FeatureCollection]:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    else:
        gdf = gdf.to_crs(epsg=4326)
    id_candidates = ["wetland_id", "predio_id", "name", "id"]
    id_col = next((c for c in id_candidates if c in gdf.columns), None)
    if id_col is None:
        gdf = gdf.reset_index(drop=True).assign(predio_id=lambda d: index_to_ids(d.index))
        id_col = "predio_id"
    else:
        gdf = gdf.rename(columns={id_col: "predio_id"})
    gdf["predio_id"] = gdf["predio_id"].astype(str).str.strip()
    mask = ~(gdf["predio_id"].str.lower().isin({p.lower() for p in exclude_patterns}))
    if "lote_demo" not in exclude_patterns:
        mask = mask & (~gdf["predio_id"].str.lower().eq("lote_demo"))
    gdf = gdf.loc[mask].copy()
    feats = [_gdf_row_to_feature(r) for _, r in gdf.iterrows()]
    ee_fc = ee.FeatureCollection(feats)
    return gdf[["predio_id", "geometry"]], ee_fc


def index_to_ids(idx):
    """Fallback ids like predio_0."""
    return [f"predio_{i}" for i in idx]


def _gdf_row_to_feature(row) -> ee.Feature:
    geom_js = mapping(row.geometry)
    pid = str(row["predio_id"]).strip()
    return ee.Feature(geom_js, {"predio_id": pid})


def gdf_union_ee_geometry(gdf: gpd.GeoDataFrame) -> ee.Geometry:
    """Unión disuelta para ``region``/clip exports (EPSG:4326)."""
    u = unary_union(gdf.geometry.values)
    return ee.Geometry(mapping(u))


def int16_scaled_band(image: ee.Image, band: str, divisor: float) -> ee.Image:
    """Valor físico ≈ pixel_int16 / divisor (mismo criterio que NDVI yearly Drive en genius_upla)."""
    return image.select(band).multiply(divisor).round().toInt16().rename(band)


def load_features_from_shapefiles(root: Path) -> tuple[gpd.GeoDataFrame, ee.FeatureCollection]:
    shps = sorted(root.glob("**/*.shp"))
    if not shps:
        raise FileNotFoundError(f"No hay .shp bajo {root}")
    parts = []
    for p in shps:
        gf = gpd.read_file(p)
        if gf.crs is None:
            gf = gf.set_crs(epsg=4326)
        else:
            gf = gf.to_crs(epsg=4326)
        pid = p.stem.upper()
        gid = gf.dissolve()
        gid["predio_id"] = pid
        parts.append(gid[["predio_id", "geometry"]])
    gdf = pd.concat(parts, ignore_index=True)
    feats = [_gdf_row_to_feature(r) for _, r in gdf.iterrows()]
    ee_fc = ee.FeatureCollection(feats)
    return gdf, ee_fc


def _spatial_mean_scalar_from_props(prop: dict, band: str) -> float:
    """Lee la media zonala reducida; con ``addBands(1)`` el resultado puede llamarse como la banda o ``mean``."""
    if not prop:
        return float("nan")
    raw = prop.get(band)
    if raw is None:
        raw = prop.get("mean")
    try:
        return float(raw) if raw is not None else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _distinct_year_lookup(ic: ee.ImageCollection) -> dict[int, object]:
    """Mapea año entero → valor de metadato en la colección (por si no es ee.Number puro)."""
    raw = ic.limit(50000).aggregate_array("year").distinct().sort().getInfo() or []
    return {int(y): y for y in raw}


def fc_zonal_weekly_genius_like(img: ee.Image, fc_ee: ee.FeatureCollection, band: str, scale_m: float) -> ee.FeatureCollection:
    """
    Misma mecánica que NDVI zonal yearly en ``products/ndvi/linear/geojson.py``:
    ``select(banda) + ee.Image(1)`` antes de ``reduceRegions`` con ``Reducer.mean()`` y escala típica 10.
    Devuelve un ``FeatureCollection`` donde cada feature incluye año/semana/fecha/mediana zonala.
    """
    im = ee.Image(img)
    reduced = (
        im.select(band)
        .addBands(ee.Image(1))
        .reduceRegions(
            collection=fc_ee,
            reducer=ee.Reducer.mean(),
            scale=scale_m,
            tileScale=8,
        )
    )

    band_const = ee.String(band)

    def attach_meta(f: ee.Feature | ee.ComputedObject) -> ee.Feature:
        ff = ee.Feature(f)
        has_b = ee.List(ff.propertyNames()).contains(band_const)
        val = ee.Algorithms.If(has_b, ff.get(band), ff.get("mean"))
        ts = im.get("system:time_start")
        return ee.Feature(ff).set(
            {
                "year": im.get("year"),
                "week": im.get("week"),
                "month": ee.Date(ts).get("month"),
                "iso_week_start": ee.Date(ts).format("YYYY-MM-dd"),
                "spatial_mean_week": val,
            }
        )

    return reduced.map(attach_meta)


def collect_weekly_table(
    collection_id: str,
    fc_ee,
    *,
    band: str,
    years: list[int],
    scale_m: float,
) -> pd.DataFrame:
    """
    Construye la tabla semanal local con **una petición GeoJSON grande por año**
    en lugar de N semanas × reduceRegions().
    """
    ic_full = ee.ImageCollection(collection_id)
    rows: list[dict] = []
    year_lookup = _distinct_year_lookup(ic_full)

    for yr in years:
        orig_y = year_lookup.get(int(yr), yr)
        col_y = ic_full.filter(ee.Filter.eq("year", orig_y)).sort("week")
        n = col_y.size().getInfo()
        if n == 0:
            continue

        def _mapped_loc(im):
            return fc_zonal_weekly_genius_like(ee.Image(im), fc_ee, band, scale_m)

        merged = ee.FeatureCollection(col_y.map(_mapped_loc)).flatten()
        feats = merged.getInfo()["features"]

        for f in feats:
            prop = dict(f.get("properties") or {})
            pid = prop.get("predio_id")
            if pid is None:
                continue
            m = _spatial_mean_scalar_from_props(prop, band)
            yv = prop.get("year")
            wv = prop.get("week")
            iso = prop.get("iso_week_start") or ""
            month_v = prop.get("month")

            iy = int(yv if yv is not None else yr)
            iw = int(wv) if wv is not None else None
            if month_v is not None:
                mo = int(month_v)
            elif isinstance(iso, str) and len(iso) >= 7:
                mo = datetime.fromisoformat(iso.replace("Z", "+00:00")).month if "T" in iso else datetime.strptime(iso[:10], "%Y-%m-%d").month
            else:
                mo = None

            rows.append(
                {
                    "year": iy,
                    "week": iw,
                    "month": mo,
                    "predio_id": str(pid).strip(),
                    "spatial_mean_week": m,
                    "iso_week_start": str(iso)[:10],
                }
            )
    return pd.DataFrame(rows)


def enqueue_drive_weekly_zonal_csv_tasks(
    collection_id: str,
    fc_ee: ee.FeatureCollection,
    *,
    band: str,
    years: list[int],
    scale_m: float,
    drive_folder: str,
    description_prefix: str,
) -> list[ee.batch.Task]:
    """Un ``Export.table.toDrive`` (CSV) por año, mismo FeatureCollection que la tabla local."""
    ic_full = ee.ImageCollection(collection_id)
    tasks: list[ee.batch.Task] = []
    year_lookup = _distinct_year_lookup(ic_full)

    selectors = ["predio_id", "year", "week", "month", "iso_week_start", "spatial_mean_week"]

    for yr in years:
        orig_y = year_lookup.get(int(yr), yr)
        col_y = ic_full.filter(ee.Filter.eq("year", orig_y)).sort("week")
        n = col_y.size().getInfo()
        if n == 0:
            continue

        stem = f"{description_prefix}_weekly_zonal_{yr}"

        def _mapped_csv(im):
            return fc_zonal_weekly_genius_like(ee.Image(im), fc_ee, band, scale_m)

        merged = ee.FeatureCollection(col_y.map(_mapped_csv)).flatten()
        t = ee.batch.Export.table.toDrive(
            collection=merged,
            description=stem,
            folder=drive_folder,
            fileNamePrefix=stem,
            fileFormat="CSV",
            selectors=selectors,
        )
        t.start()
        tasks.append(t)

    return tasks


def enqueue_yearly_median_raster_tasks_drive(
    collection_id: str,
    *,
    band: str,
    years: list[int],
    scale_m: float,
    clip_geom: ee.Geometry,
    drive_folder: str,
    stem_prefix: str,
    quantize_divisor: float,
) -> list[ee.batch.Task]:
    """Patrón ``yearly_median_raster_exports_from_yearmonth`` (genius NDVI yearly raster): median anual → int16_scaled → Drive."""
    ic_full = ee.ImageCollection(collection_id)
    tasks: list[ee.batch.Task] = []
    year_lookup = _distinct_year_lookup(ic_full)

    for yr in sorted(years):
        orig_y = year_lookup.get(int(yr), yr)
        median_img = (
            ic_full.filter(ee.Filter.eq("year", orig_y))
            .select(band)
            .median()
            .rename(band)
            .clip(clip_geom)
            .set({"year_export": yr})
        )
        out = int16_scaled_band(median_img, band, quantize_divisor)
        stem = f"{stem_prefix}_{yr}"
        t = ee.batch.Export.image.toDrive(
            image=out,
            description=stem,
            folder=drive_folder,
            fileNamePrefix=stem,
            scale=scale_m,
            region=clip_geom,
            crs="EPSG:4326",
            maxPixels=1e13,
        )
        t.start()
        tasks.append(t)

    return tasks


def annual_summary(df_weekly: pd.DataFrame) -> pd.DataFrame:
    """Una fila/año: mediana, P25 y P75 entre predios (cada predio = mediana semanal del año)."""
    if df_weekly.empty:
        return pd.DataFrame(columns=["year", "median_across_predios", "p25_across_predios", "p75_across_predios"])
    agg_p = df_weekly.groupby(["predio_id", "year"], as_index=False)["spatial_mean_week"].median()
    out_rows = []
    for y in sorted(agg_p["year"].unique()):
        s = agg_p.loc[agg_p["year"] == y, "spatial_mean_week"].dropna()
        if s.empty:
            continue
        out_rows.append(
            {
                "year": int(y),
                "median_across_predios": float(np.median(s)),
                "p25_across_predios": float(np.percentile(s, 25)),
                "p75_across_predios": float(np.percentile(s, 75)),
            }
        )
    return pd.DataFrame(out_rows)


def monthly_summary(df_weekly: pd.DataFrame, current_calendar_year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Histórico: mediana pooled (predio×año×mes), años <= año completo anterior (curr-1).

    Actual: por mes del año calendar actual, media entre predios.
    Devuelve (tabla ancha mensual, detalle predio×año×mes con ``monthly_agg``).
    """
    if df_weekly.empty:
        empty = pd.DataFrame(
            [
                {
                    "month": m,
                    "month_label": MONTH_NAMES_ES[m - 1],
                    "historic_monthly_median": float("nan"),
                    "current_year_monthly_mean": float("nan"),
                }
                for m in range(1, 13)
            ]
        )
        return empty, empty

    dm = (
        df_weekly.groupby(["predio_id", "year", "month"], as_index=False)["spatial_mean_week"]
        .mean()
        .rename(columns={"spatial_mean_week": "monthly_agg"})
    )

    last_complete_year = current_calendar_year - 1
    hist_pool = dm[dm["year"] <= last_complete_year].copy()
    curr = dm[dm["year"] == current_calendar_year].copy()

    hm = []
    cm = []
    for m in range(1, 13):
        hvals = hist_pool.loc[hist_pool["month"] == m, "monthly_agg"].dropna().values
        hm.append({"month": m, "month_label": MONTH_NAMES_ES[m - 1], "historic_monthly_median": float(np.median(hvals)) if hvals.size else float("nan")})

        cv = curr.loc[curr["month"] == m, "monthly_agg"].dropna().values
        cm_mean = float(np.mean(cv)) if cv.size else float("nan")
        cm.append(
            {"month": m, "month_label": MONTH_NAMES_ES[m - 1], "current_year_monthly_mean": cm_mean}
        )

    df_h = pd.DataFrame(hm)
    df_c = pd.DataFrame(cm)
    merged = df_h.merge(df_c[["month", "current_year_monthly_mean"]], on="month")
    return merged, dm


def _json_safe(x):
    """None en lugar de NaN para JSON."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return float(v)


def build_explorer_json_payload(
    *,
    df_week: pd.DataFrame,
    dm_detail: pd.DataFrame,
    portfolio_annual: pd.DataFrame,
    portfolio_monthly: pd.DataFrame,
    current_calendar_year: int,
    last_complete_year: int,
    band: str,
    collection_id: str,
) -> dict:
    """JSON para explorador HTML: Sentinel-2 por predio (`wetland_id` en minúsculas) + cartera."""
    gen = datetime.now(timezone.utc).isoformat()

    portfolio_annual_rows = []
    if not portfolio_annual.empty:
        for _, r in portfolio_annual.iterrows():
            yv = int(r["year"])
            if yv > last_complete_year:
                continue
            portfolio_annual_rows.append(
                {
                    "year": yv,
                    "median": _json_safe(r.get("median_across_predios")),
                    "p25": _json_safe(r.get("p25_across_predios")),
                    "p75": _json_safe(r.get("p75_across_predios")),
                }
            )

    portfolio_monthly_rows = []
    if not portfolio_monthly.empty:
        dfm = portfolio_monthly
        for m in range(1, 13):
            rows = dfm[dfm["month"] == m]
            if rows.empty:
                portfolio_monthly_rows.append(
                    {
                        "month": m,
                        "month_label": MONTH_NAMES_ES[m - 1],
                        "historic_median_between_predios": None,
                        "current_year_mean_between_predios": None,
                    }
                )
                continue
            r0 = rows.iloc[0]
            portfolio_monthly_rows.append(
                {
                    "month": m,
                    "month_label": MONTH_NAMES_ES[m - 1],
                    "historic_median_between_predios": _json_safe(r0.get("historic_monthly_median")),
                    "current_year_mean_between_predios": _json_safe(r0.get("current_year_monthly_mean")),
                }
            )

    by_predio: dict = {}
    if not df_week.empty:
        apr = df_week.groupby(["predio_id", "year"], as_index=False)["spatial_mean_week"].median()

        predios = apr["predio_id"].astype(str).str.strip().unique()
        dm = dm_detail.copy() if not dm_detail.empty else pd.DataFrame()

        for raw_pid in predios:
            key = raw_pid.strip().lower()
            ann = []
            sub = apr[apr["predio_id"].astype(str).str.strip() == raw_pid].sort_values("year")
            for _, r in sub.iterrows():
                yi = int(r["year"])
                if yi > last_complete_year:
                    continue
                ann.append(
                    {
                        "year": yi,
                        "median_weekly_ndvi_agg": _json_safe(r["spatial_mean_week"]),
                    }
                )

            months_out = []
            for m in range(1, 13):
                if dm.empty:
                    months_out.append(
                        {
                            "month": m,
                            "month_label": MONTH_NAMES_ES[m - 1],
                            "historic_median_within_predio_months": None,
                            "current_year_value": None,
                        }
                    )
                    continue
                mask_p = dm["predio_id"].astype(str).str.strip() == raw_pid
                yrs_hist = (dm["year"] <= last_complete_year) & (dm["month"] == m) & mask_p
                vals = dm.loc[yrs_hist, "monthly_agg"].dropna().astype(float).values
                hist_m = float(np.median(vals)) if vals.size else float("nan")
                row_c = dm.loc[mask_p & (dm["year"] == current_calendar_year) & (dm["month"] == m), "monthly_agg"]
                cur_v = row_c.iloc[0] if len(row_c) else float("nan")

                months_out.append(
                    {
                        "month": m,
                        "month_label": MONTH_NAMES_ES[m - 1],
                        "historic_median_within_predio_months": _json_safe(hist_m),
                        "current_year_value": _json_safe(cur_v),
                    }
                )

            by_predio[key] = {"annual": ann, "monthly": months_out}

    return {
        "schema": "fic-agro/s2-explorer-charts/1",
        "generated_at": gen,
        "band": band,
        "gee_collection_id": collection_id.strip().rstrip("/"),
        "current_calendar_year": current_calendar_year,
        "last_complete_year": last_complete_year,
        "labels": {
            "annual_chart": "Distribución Sentinel-2 anual: mediana espacial por semanas vs P25/P75 entre predios (años completos)",
            "annual_predio_series": "Mediana anual dentro del predio (sobre valores semanales)",
            "month_chart": "Referencias cartera entre predios vs tu predio (mes)",
        },
        "portfolio_between_predios": {
            "annual": portfolio_annual_rows,
            "monthly": portfolio_monthly_rows,
        },
        "by_predio": by_predio,
    }


def try_plot_series(
    df_annual: pd.DataFrame,
    df_monthly: pd.DataFrame,
    out_dir: Path,
    *,
    current_calendar_year: int,
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: WPS433
    except ImportError:
        print("matplotlib no instalado; omito PNG.", flush=True)
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 5))
    if not df_annual.empty:
        ax.plot(df_annual["year"], df_annual["median_across_predios"], color="#1f77b4", label="Mediana entre predios")
        ax.fill_between(
            df_annual["year"],
            df_annual["p25_across_predios"],
            df_annual["p75_across_predios"],
            color="#aad4ff",
            alpha=0.6,
            label="P25–P75 entre predios",
        )
        ax.legend()
        ax.set_xlabel("Año")
        ax.set_ylabel("NDVI (valor agregado)")
        ax.grid(True, linestyle=":")
    ax.set_title("Serie anual: mediana anual por predio y P25-P75 entre predios")
    png_a = out_dir / "serie_anual_distribucion_predios.png"
    fig.savefig(png_a, dpi=144, bbox_inches="tight")
    plt.close(fig)

    fig2, ax2 = plt.subplots(figsize=(10, 5))
    if not df_monthly.empty and "historic_monthly_median" in df_monthly.columns:
        mx = df_monthly["month"]
        ax2.plot(mx, df_monthly["historic_monthly_median"], "--o", color="#7f7f7f", label=f"Histórico (medianas mes, ≤ año {current_calendar_year - 1})")
        ax2.plot(mx, df_monthly["current_year_monthly_mean"], "-o", color="#d62728", label=f"Año actual ({current_calendar_year}), media entre predios")
        ax2.set_xticks(mx)
        ax2.set_xticklabels(df_monthly["month_label"])
        ax2.legend()
        ax2.set_xlabel("Mes")
        ax2.set_ylabel("NDVI (valor agregado)")
        ax2.grid(True, linestyle=":")
    ax2.set_title("Serie mensual: baseline vs año actual")
    png_m = out_dir / "serie_mensual_historico_vs_actual.png"
    fig2.savefig(png_m, dpi=144, bbox_inches="tight")
    plt.close(fig2)

    print(f"Gráficos: {png_a} ; {png_m}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Estadísticas zonal desde ImageCollection GEE weekly + shapes locales.")
    parser.add_argument("--project", default=None, metavar="GCP", help=f"GCP para ee.Initialize (default {DEFAULT_CLOUD_PROJECT} + env)")
    parser.add_argument(
        "--collection",
        default=os.environ.get("GEE_STATS_COLLECTION", DEFAULT_COLLECTION),
        help=f"Earth Engine AssetId de ImageCollection ({DEFAULT_COLLECTION}).",
    )
    parser.add_argument(
        "--shapes-root",
        type=Path,
        default=Path(os.environ.get("GEE_STATS_SHAPES", DEFAULT_SHAPES)).resolve(),
        help=f"Directorio/archivo geojson ({DEFAULT_SHAPES}) o usa --from-shapes.",
    )
    parser.add_argument(
        "--from-shapes",
        action="store_true",
        help="Leer todas las geometrías de **/**/*.shp bajo shapes-root en lugar de aoi.geojson.",
    )
    parser.add_argument("--exclude-predio", action="append", default=[], metavar="ID", help="Excluir predio_id del conjunto.")
    parser.add_argument("--band", default=DEFAULT_BAND, metavar="NAM", help="Banda dentro de cada imagen (default NDVI).")
    parser.add_argument("--scale", type=float, default=10.0, help="Escala metros reduceRegions.")
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        help="Filtrado de años procesados desde la colección (default: año mínimo en metadatos).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Último año incluido (default: igual al año calendario actual).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Carpeta para CSV/gráficos (default: data_processed/s2_shapes_stats/).",
    )
    parser.add_argument(
        "--explorer-json-out",
        type=Path,
        default=Path("data_static/satellite2/s2_chart_data.json"),
        help="Ruta JSON consumida por explorador.html (Chart.js). Omitir con --no-explorer-json.",
    )
    parser.add_argument(
        "--no-explorer-json",
        action="store_true",
        help="No escribir el JSON del explorador.",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="No generar PNG con matplotlib.",
    )
    parser.add_argument(
        "--drive-zonal-csv-folder",
        default=None,
        metavar="CARPETA",
        help="Carpeta en Google Drive: encola un CSV zonal por año (Export.table, mismo patrón reduceRegions que NDVI Genius).",
    )
    parser.add_argument(
        "--drive-zonal-stem-prefix",
        default=os.environ.get("GEE_DRIVE_ZONAL_PREFIX", "s2_shapes"),
        metavar="PREFIX",
        help="Prefijo de exportación Drive para CSV semanales (default s2_shapes o GEE_DRIVE_ZONAL_PREFIX).",
    )
    parser.add_argument(
        "--drive-yearly-raster-folder",
        default=None,
        metavar="CARPETA",
        help="Carpeta en Google Drive: mediana anual de la banda por pixel, GeoTIFF int16/divisor como NDVI_Yearly (Genius).",
    )
    parser.add_argument(
        "--drive-yearly-raster-stem-prefix",
        default=os.environ.get("GEE_DRIVE_RASTER_PREFIX", "S2_Yearly_median"),
        metavar="PREFIX",
        help="Prefijo de archivos raster anuales (default S2_Yearly_median o GEE_DRIVE_RASTER_PREFIX).",
    )
    parser.add_argument(
        "--drive-raster-quantize-divisor",
        type=float,
        default=10000.0,
        help="Divisor para cuantizar raster Drive (valor físico ≈ DN/divisor; default 10000).",
    )
    args = parser.parse_args()

    init_project = resolve_cloud_project(args.project)
    ee_initialize(init_project)

    shapes_arg = Path(args.shapes_root)
    if args.from_shapes:
        gdf_plain, ee_fc = load_features_from_shapefiles(shapes_arg if shapes_arg.is_dir() else shapes_arg.parent)
    else:
        p = shapes_arg if shapes_arg.suffix.lower() == ".geojson" else Path(DEFAULT_SHAPES).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"No encontré {p}; usa --from-shapes o --shapes-root correcto.")
        gdf_plain, ee_fc = load_features_from_aoi_geojson(p, exclude_patterns=tuple(args.exclude_predio))

    if gdf_plain.empty:
        raise SystemExit("No quedaron polígonos tras filtros (--exclude-predio).")

    ic = ee.ImageCollection(args.collection.strip().rstrip("/"))
    ylist_raw = ic.limit(50000).aggregate_array("year").distinct().getInfo()
    if not ylist_raw:
        raise SystemExit(f"Colección vacía o sin propiedad «year»: {args.collection}")
    ymin, ymax = int(min(ylist_raw)), int(max(ylist_raw))
    ymin = max(int(ymin), args.start_year) if args.start_year else int(ymin)
    ymax = args.end_year if args.end_year is not None else int(ymax)

    cy = datetime.now(timezone.utc).year
    ymax = min(ymax, cy)

    years = list(range(ymin, ymax + 1))
    print(f"Proyecto EE: {init_project}", flush=True)
    print(f"Colección: {args.collection}", flush=True)
    print(f"Predios: {list(gdf_plain['predio_id'])} ({len(gdf_plain)} geometrías)", flush=True)
    print(f"Banda: {args.band} | años: {ymin}–{ymax} ({len(years)})", flush=True)
    print("Descargando medias zonales (un getInfo grande por año, reduceRegions tipo NDVI Genius)...", flush=True)

    coll_id = args.collection.strip().rstrip("/")
    df_week = collect_weekly_table(coll_id, ee_fc, band=args.band, years=years, scale_m=float(args.scale))
    if df_week.empty:
        print("Sin filas tabuladas: revisá banda, colección u años disponibles.", file=sys.stderr)

    df_y = annual_summary(df_week)
    df_month, dm_detail = monthly_summary(df_week, current_calendar_year=cy)

    out_dir = (args.output_dir or Path("data_processed/s2_shapes_stats")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if not df_week.empty:
        wpath_pq = out_dir / "serie_semanas_predio.parquet"
        try:
            df_week.to_parquet(wpath_pq, index=False)
            print(f"Detalle semanal (parquet): {wpath_pq}", flush=True)
        except ImportError:
            wpath_csv = out_dir / "serie_semanas_predio.csv"
            df_week.to_csv(wpath_csv, index=False)
            print(f"Detalle semanal (csv): {wpath_csv} (instalar pyarrow para parquet)", flush=True)
    csv_a = out_dir / "serie_anual_medianas_p25_p75.csv"
    csv_m = out_dir / "serie_mensual_historico_vs_actual.csv"

    dm_detail.to_csv(out_dir / "detalle_predio_year_month.csv", index=False)

    df_y.to_csv(csv_a, index=False)
    df_month.to_csv(csv_m, index=False)

    print(df_y.to_string(index=False))
    print("--- Mensual ---", flush=True)
    print(df_month.to_string(index=False), flush=True)
    print(f"Salida CSV: {csv_a} ; {csv_m}", flush=True)

    geom_union = gdf_union_ee_geometry(gdf_plain)
    if args.drive_zonal_csv_folder and str(args.drive_zonal_csv_folder).strip():
        folder = str(args.drive_zonal_csv_folder).strip()
        prefix = str(args.drive_zonal_stem_prefix).strip() or "s2_shapes"
        t_csv = enqueue_drive_weekly_zonal_csv_tasks(
            coll_id,
            ee_fc,
            band=args.band,
            years=years,
            scale_m=float(args.scale),
            drive_folder=folder,
            description_prefix=prefix.replace(" ", "_"),
        )
        print(f"Tareas Export.table CSV (Drive carpeta '{folder}'): {len(t_csv)}", flush=True)

    if args.drive_yearly_raster_folder and str(args.drive_yearly_raster_folder).strip():
        r_folder = str(args.drive_yearly_raster_folder).strip()
        r_stem = str(args.drive_yearly_raster_stem_prefix).strip() or "S2_Yearly_median"
        t_r = enqueue_yearly_median_raster_tasks_drive(
            coll_id,
            band=args.band,
            years=years,
            scale_m=float(args.scale),
            clip_geom=geom_union,
            drive_folder=r_folder,
            stem_prefix=r_stem.replace(" ", "_"),
            quantize_divisor=float(args.drive_raster_quantize_divisor),
        )
        print(f"Tareas Export.image raster anual (Drive carpeta '{r_folder}'): {len(t_r)}", flush=True)

    if not args.no_plots:
        try_plot_series(df_y, df_month, out_dir, current_calendar_year=cy)

    lc = cy - 1
    if not args.no_explorer_json:
        payload = build_explorer_json_payload(
            df_week=df_week,
            dm_detail=dm_detail,
            portfolio_annual=df_y,
            portfolio_monthly=df_month,
            current_calendar_year=cy,
            last_complete_year=lc,
            band=str(args.band),
            collection_id=str(args.collection).strip().rstrip("/"),
        )
        ej = args.explorer_json_out.resolve()
        ej.parent.mkdir(parents=True, exist_ok=True)
        with open(ej, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"JSON explorador: {ej}", flush=True)

    print("\nInterpretación rápida:", flush=True)
    print(
        " • Anual: por predio, mediana de semanas dentro del año; CSV = estadísticos entre predios (medianas + P25/P75). Solo años cerrados pasan al JSON explorador.",
        flush=True,
    )
    print(
        f" • Mensual histórico: mediana pooled (predio/año mes) sólo años ≤ {cy - 1}; actual {cy}: media entre predios por mes.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except (ee.EEException, ImportError, OSError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, ee.EEException):
            print(
                "Autenticate con `earthengine authenticate` y define EE_CLOUD_PROJECT acorde "
                "a tu proyecto EE.",
                file=sys.stderr,
            )
        sys.exit(1)
