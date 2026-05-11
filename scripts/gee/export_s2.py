#!/usr/bin/env python3
"""
Exporta mosaicos semanales Sentinel-2 a Earth Engine Assets: Cloud Score+ y, por pixel,
solo bandas de índices compuestos (NDVI, NDMI, índices tipo Sentinel Hub / SNAP, etc.)
más ``clear_pixel_count`` — sin bandas espectrales crudas del sensor.

Autenticación: ``earthengine authenticate``. Proyecto Cloud por defecto: ``teleambagr``
(``EE_CLOUD_PROJECT`` / ``--project``).

Salida bajo ``--export-prefix``; se crea la ImageCollection vacía si falta (salvo
``--skip-ensure-destination``). Por defecto **no** re-encola semanas cuyo asset hijo ya exista
(``--force`` para ignorar y volver a exportar todo).

**Incremental (por defecto, sin ``--force``):** lista hijos bajo ``--export-prefix``,
comprueba semana a semana (vía auditoría ligera) qué mosaicos faltan y **solo construye
el grafo GEE para el rango de años que aún tienen semanas pendientes**; los años cuyas
semanas con datos ya están exportadas se omiten por completo (ahorro de memoria y tiempo).

Por defecto imprime una **auditoría por semana** (escenas S2 vs pipeline con Cloud Score+,
y si se encola, omite por duplicado o no hay datos). Usa ``--no-audit`` para silenciarla.

Años: ``--year`` o ``--start-year`` / ``--end-year``; si no, ``GEE_START_YEAR`` /
``DEFAULT_START_YEAR``, y para el fin ``GEE_END_YEAR`` o ``DEFAULT_END_YEAR`` en el código
(se limita igual al año calendario actual en tiempo de ejecución).

Las semanas son **ISO 8601** (lunes=sí, domingo=sí): ventana [lunes 00:00 UTC, lunes+7d) exclusivo.
Solo se usan escenas **hasta la última semana ISO completa**: el fin del intervalo es el **lunes 00:00 UTC
de la semana en curso** (exclusivo), así no entra la semana parcial actual.

Los assets se nombran ``Y{iso_year}_W{iso_week:02d}`` (año/semana ISO, p. ej. borde de enero).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import ee

DEFAULT_AOI_ASSET = "projects/teleambagr/assets/vectores/Area_Agricola_Reg_Valpo_2025"
DEFAULT_EXPORT_PREFIX = "projects/teleambagr/assets/S2_weekly_valpo"

# Proyecto Google Cloud para ``ee.Initialize(project=...)`` (API / facturación EE).
DEFAULT_CLOUD_PROJECT = "teleambagr"

DEFAULT_START_YEAR = 2017
DEFAULT_END_YEAR = 2026


def utc_monday_00_current_week_ms() -> int:
    """Lunes 00:00 UTC de la semana ISO que contiene el instante actual."""
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp() * 1000)


def iso_week_specs_thursday_in_calendar_year(
    calendar_year: int,
    end_exclusive_ms: int,
) -> list[dict]:
    """
    Semanas ISO (lun–dom) cuyo **jueves** cae en el año civil ``calendar_year``.
    Solo incluye semanas que terminan en o antes de ``end_exclusive_ms``
    (lunes 00:00 UTC de la semana en curso, exclusivo en ``filterDate``).
    """
    ms_week = 7 * 24 * 60 * 60 * 1000
    out: dict[tuple[int, int], dict] = {}
    cur = datetime(calendar_year, 1, 1, tzinfo=timezone.utc)
    end_day = datetime(calendar_year, 12, 31, tzinfo=timezone.utc)
    while cur <= end_day:
        _iy, _iw, wd = cur.isocalendar()
        if wd == 4:
            monday = cur - timedelta(days=3)
            mms = int(monday.timestamp() * 1000)
            if mms + ms_week > end_exclusive_ms:
                cur += timedelta(days=1)
                continue
            key = (_iy, _iw)
            if key not in out:
                out[key] = {
                    "iso_year": _iy,
                    "iso_week": _iw,
                    "monday_ms": mms,
                    "calendar_year": calendar_year,
                }
        cur += timedelta(days=1)
    return [out[k] for k in sorted(out.keys())]


def export_asset_basename(iso_year: int, iso_week: int) -> str:
    return f"Y{iso_year}_W{iso_week:02d}"


def resolve_cloud_project(cli_project: str | None) -> str:
    """Orden: argumento ``--project``, env ``EE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_PROJECT``, valor por defecto."""
    if cli_project is not None and cli_project.strip() != "":
        return cli_project.strip()
    for key in ("EE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return DEFAULT_CLOUD_PROJECT


def ee_initialize(project: str) -> None:
    ee.Initialize(project=project)


def _asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException:
        return False


def list_child_asset_ids(parent_id: str) -> set[str]:
    """
    IDs completos de assets hijos bajo ``parent_id`` (ImageCollection o carpeta).
    Si no hay permiso o el padre no existe, devuelve conjunto vacío.
    """
    parent_id = parent_id.strip().rstrip("/")
    seen: set[str] = set()
    page_token: str | None = None
    while True:
        req: dict = {"parent": parent_id}
        if page_token:
            req["pageToken"] = page_token
        try:
            resp = ee.data.listAssets(req)
        except ee.EEException:
            return seen
        for a in resp.get("assets") or []:
            aid = a.get("name") or a.get("id")
            if isinstance(aid, str) and aid:
                seen.add(aid.rstrip("/"))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return seen


def load_existing_export_keys(export_prefix: str) -> tuple[set[str], int]:
    """Conjunto de claves para comprobar duplicados, y número de hijos devueltos por listAssets."""
    full = list_child_asset_ids(export_prefix.strip().rstrip("/"))
    keys: set[str] = set(full)
    for f in full:
        keys.add(f.rstrip("/").split("/")[-1])
    return keys, len(full)


def export_key_exists(asset_id: str, keys: set[str]) -> bool:
    if asset_id in keys:
        return True
    return asset_id.rstrip("/").split("/")[-1] in keys


def ensure_export_destination(export_prefix: str, *, dry_run: bool) -> None:
    """
    Crea bajo ``projects/<cloud>/assets/`` las carpetas (FOLDER) que falten y, al final del
    prefijo, un asset vacío IMAGE_COLLECTION si no existe. Es idempotente.
    Los mosaicos se exportan como imágenes hijas: ``<export_prefix>/Yxxxx_Www``.
    """
    export_prefix = export_prefix.strip().rstrip("/")
    parts = export_prefix.split("/")
    if len(parts) < 4 or parts[0] != "projects":
        raise ValueError(
            "export_prefix debe ser projects/<CLOUD_PROJECT>/assets/... "
            f"(recibido: {export_prefix!r})"
        )
    try:
        assets_idx = parts.index("assets")
    except ValueError as exc:
        raise ValueError("export_prefix debe incluir el segmento .../assets/...") from exc

    tail = parts[assets_idx + 1 :]
    if not tail:
        raise ValueError("Debe haber al menos un nombre bajo projects/.../assets/")

    cumulative = "/".join(parts[: assets_idx + 1])

    for i, segment in enumerate(tail):
        cumulative = f"{cumulative}/{segment}"
        last = i == len(tail) - 1

        if _asset_exists(cumulative):
            info = ee.data.getAsset(cumulative)
            kind = str(info.get("type", "")).upper()
            if last:
                if kind == "IMAGE_COLLECTION":
                    print(f"Colección destino ya existe: {cumulative}")
                elif kind == "FOLDER":
                    print(
                        f"Destino existe como carpeta (OK para exportar imágenes): {cumulative}"
                    )
                else:
                    raise ValueError(
                        f"El asset {cumulative} ya existe como {kind}; "
                        "usa otro export_prefix o eliminalo en Earth Engine."
                    )
            elif kind not in ("FOLDER", "IMAGE_COLLECTION"):
                raise ValueError(
                    f"No se puede usar {cumulative} como carpeta padre (tipo {kind})."
                )
            continue

        if dry_run:
            typ = "IMAGE_COLLECTION" if last else "FOLDER"
            print(f"[dry-run] crearía {typ}: {cumulative}")
            continue

        if last:
            ee.data.createAsset({"type": "IMAGE_COLLECTION"}, cumulative)
            print(f"Creada ImageCollection vacía: {cumulative}")
        else:
            ee.data.createAsset({"type": "FOLDER"}, cumulative)
            print(f"Creada carpeta: {cumulative}")


def mask_and_scale(image: ee.Image) -> ee.Image:
    mask = image.select("cs").gte(0.6)
    clear = mask.rename("clear_pixel_count")
    return (
        image.updateMask(mask)
        .divide(10000)
        .addBands(clear)
        .copyProperties(image, ["system:time_start", "system:index"])
    )


def _prop_num(img: ee.Image, key: str, default: float) -> ee.Number:
    """``img.get`` con valor por defecto si falta la propiedad (p. ej. ángulos)."""
    names = ee.List(img.propertyNames())
    has = names.contains(key)
    return ee.Number(ee.Algorithms.If(has, img.get(key), ee.Number(default)))


def _const_like(ref: ee.Image, n: ee.Number) -> ee.Image:
    return ref.multiply(0).add(n)


def _tansig_ee(x: ee.Image) -> ee.Image:
    return ee.Image(2.0).divide(ee.Image(1.0).add(x.multiply(-2.0).exp())).subtract(1.0)


def _norm_refl(b: ee.Image, lo: float, hi: float) -> ee.Image:
    return b.subtract(lo).multiply(2.0).divide(ee.Image.constant(hi - lo)).subtract(1.0)


def _norm_scalar_img(x: ee.Image, lo: float, hi: float) -> ee.Image:
    return x.subtract(lo).multiply(2.0).divide(ee.Image.constant(hi - lo)).subtract(1.0)


def _denorm_ee(x: ee.Image, lo: float, hi: float) -> ee.Image:
    return x.add(1.0).multiply(0.5).multiply(hi - lo).add(lo)


def _neuron_linear(coeffs: list[float], inputs: list[ee.Image]) -> ee.Image:
    """``coeffs`` = [bias, w0..wN-1] alineado con ``inputs``."""
    s = ee.Image.constant(coeffs[0])
    for w, inp in zip(coeffs[1:], inputs):
        s = s.add(inp.multiply(w))
    return _tansig_ee(s)


def _layer_linear(coeffs: list[float], neurons: list[ee.Image]) -> ee.Image:
    s = ee.Image.constant(coeffs[0])
    for w, n in zip(coeffs[1:], neurons):
        s = s.add(n.multiply(w))
    return s


def _snap_norm_inputs(img: ee.Image) -> list[ee.Image]:
    """
    Reflectancias y ángulos normalizados como en los evalscripts SNAP de Sentinel Hub
    (LAI, Cab, CCC, FAPAR, FCOVER). Ángulos de metadatos GEE (media de incidencia / solar).
    """
    ref = img.select("B4")
    b03 = _norm_refl(img.select("B3"), 0.0, 0.253061520471542)
    b04 = _norm_refl(img.select("B4"), 0.0, 0.290393577911328)
    b05 = _norm_refl(img.select("B5"), 0.0, 0.305398915248555)
    b06 = _norm_refl(img.select("B6"), 0.006637972542253, 0.608900395797889)
    b07 = _norm_refl(img.select("B7"), 0.013972727018939, 0.753827384322927)
    b8a = _norm_refl(img.select("B8A"), 0.026690138082061, 0.782011770669178)
    b11 = _norm_refl(img.select("B11"), 0.016388074192258, 0.493761397883092)
    b12 = _norm_refl(img.select("B12"), 0.0, 0.493025984460231)

    vz_deg = _prop_num(img, "MEAN_INCIDENCE_ZENITH_ANGLE_B8", 10.0)
    va_deg = _prop_num(img, "MEAN_INCIDENCE_AZIMUTH_ANGLE_B8", 180.0)
    sz_deg = _prop_num(img, "MEAN_SOLAR_ZENITH_ANGLE", 45.0)
    sa_deg = _prop_num(img, "MEAN_SOLAR_AZIMUTH_ANGLE", 135.0)

    vz_rad = _const_like(ref, vz_deg).multiply(math.pi / 180.0)
    sz_rad = _const_like(ref, sz_deg).multiply(math.pi / 180.0)
    rel_rad = _const_like(ref, sa_deg.subtract(va_deg)).multiply(math.pi / 180.0)

    cos_vz = vz_rad.cos()
    cos_sz = sz_rad.cos()
    rel_az = rel_rad.cos()

    view_zen_n = _norm_scalar_img(cos_vz, 0.918595400582046, 1.0)
    sun_zen_n = _norm_scalar_img(cos_sz, 0.342022871159208, 0.936206429175402)
    return [b03, b04, b05, b06, b07, b8a, b11, b12, view_zen_n, sun_zen_n, rel_az]


def _snap_lai_cab_fapar_fcover(img: ee.Image) -> tuple[ee.Image, ee.Image, ee.Image, ee.Image, ee.Image]:
    """LAI, Cab (contenido hoja), CCC (canopy), FAPAR, FCOVER — redes SNAP vía Sentinel Hub."""
    z = _snap_norm_inputs(img)

    lai_n = [
        [
            4.96238030555279,
            -0.023406878966470,
            0.921655164636366,
            0.135576544080099,
            -1.938331472397950,
            -3.342495816122680,
            0.902277648009576,
            0.205363538258614,
            -0.040607844721716,
            -0.083196409727092,
            0.260029270773809,
            0.284761567218845,
        ],
        [
            1.416008443981500,
            -0.132555480856684,
            -0.139574837333540,
            -1.014606016898920,
            -1.330890038649270,
            0.031730624503341,
            -1.433583541317050,
            -0.959637898574699,
            1.133115706551000,
            0.216603876541632,
            0.410652303762839,
            0.064760155543506,
        ],
        [
            1.075897047213310,
            0.086015977724868,
            0.616648776881434,
            0.678003876446556,
            0.141102398644968,
            -0.096682206883546,
            -1.128832638862200,
            0.302189102741375,
            0.434494937299725,
            -0.021903699490589,
            -0.228492476802263,
            -0.039460537589826,
        ],
        [
            1.533988264655420,
            -0.109366593670404,
            -0.071046262972729,
            0.064582411478320,
            2.906325236823160,
            -0.673873108979163,
            -3.838051868280840,
            1.695979344531530,
            0.046950296081713,
            -0.049709652688365,
            0.021829545430994,
            0.057483827104091,
        ],
        [
            3.024115930757230,
            -0.089939416159969,
            0.175395483106147,
            -0.081847329172620,
            2.219895367487790,
            1.713873975136850,
            0.713069186099534,
            0.138970813499201,
            -0.060771761518025,
            0.124263341255473,
            0.210086140404351,
            -0.183878138700341,
        ],
    ]
    lai_l2 = [
        1.096963107077220,
        -1.500135489728730,
        -0.096283269121503,
        -0.194935930577094,
        -0.352305895755591,
        0.075107415847473,
    ]
    lai_lo, lai_hi = 0.000319182538301, 14.4675094548151
    lai_neurons = [_neuron_linear(c, z) for c in lai_n]
    lai_raw = _denorm_ee(_layer_linear(lai_l2, lai_neurons), lai_lo, lai_hi).rename("LAI")

    cab_n = [
        [
            4.242299670155190,
            0.400396555256580,
            0.607936279259404,
            0.137468650780226,
            -2.955866573461640,
            -3.186746687729570,
            2.206800751246430,
            -0.313784336139636,
            0.256063547510639,
            -0.071613219805105,
            0.510113504210111,
            0.142813982138661,
        ],
        [
            -0.259569088225796,
            -0.250781102414872,
            0.439086302920381,
            -1.160590937522300,
            -1.861935250269610,
            0.981359868451638,
            1.634230834254840,
            -0.872527934645577,
            0.448240475035072,
            0.037078083501217,
            0.030044189670404,
            0.005956686619403,
        ],
        [
            3.130392627338360,
            0.552080132568747,
            -0.502919673166901,
            6.105041924966230,
            -1.294386119140800,
            -1.059956388352800,
            -1.394092902418820,
            0.324752732710706,
            -1.758871822827680,
            -0.036663679860328,
            -0.183105291400739,
            -0.038145312117381,
        ],
        [
            0.774423577181620,
            0.211591184882422,
            -0.248788896074327,
            0.887151598039092,
            1.143675895571410,
            -0.753968830338323,
            -1.185456953076760,
            0.541897860471577,
            -0.252685834607768,
            -0.023414901078143,
            -0.046022503549557,
            -0.006570284080657,
        ],
        [
            2.584276648534610,
            0.254790234231378,
            -0.724968611431065,
            0.731872806026834,
            2.303453821021270,
            -0.849907966921912,
            -6.425315500537270,
            2.238844558459030,
            -0.199937574297990,
            0.097303331714567,
            0.334528254938326,
            0.113075306591838,
        ],
    ]
    cab_l2 = [
        0.463426463933822,
        -0.352760040599190,
        -0.603407399151276,
        0.135099379384275,
        -1.735673123851930,
        -0.147546813318256,
    ]
    cab_lo, cab_hi = 0.007426692959872, 873.908222110306
    cab_neurons = [_neuron_linear(c, z) for c in cab_n]
    cab_raw = _denorm_ee(_layer_linear(cab_l2, cab_neurons), cab_lo, cab_hi).rename("LEAF_CHL")
    canopy = lai_raw.multiply(cab_raw).rename("CANOPY_CHL")

    fap_n = [
        [
            -0.887068364040280,
            0.268714454733421,
            -0.205473108029835,
            0.281765694196018,
            1.337443412255980,
            0.390319212938497,
            -3.612714342203350,
            0.222530960987244,
            0.821790549667255,
            -0.093664567310731,
            0.019290146147447,
            0.037364446377188,
        ],
        [
            0.320126471197199,
            -0.248998054599707,
            -0.571461305473124,
            -0.369957603466673,
            0.246031694650909,
            0.332536215252841,
            0.438269896208887,
            0.819000551890450,
            -0.934931499059310,
            0.082716247651866,
            -0.286978634108328,
            -0.035890968351662,
        ],
        [
            0.610523702500117,
            -0.164063575315880,
            -0.126303285737763,
            -0.253670784366822,
            -0.321162835049381,
            0.067082287973580,
            2.029832288655260,
            -0.023141228827722,
            -0.553176625657559,
            0.059285451897783,
            -0.034334454541432,
            -0.031776704097009,
        ],
        [
            -0.379156190833946,
            0.130240753003835,
            0.236781035723321,
            0.131811664093253,
            -0.250181799267664,
            -0.011364149953286,
            -1.857573214633520,
            -0.146860751013916,
            0.528008831372352,
            -0.046230769098303,
            -0.034509608392235,
            0.031884395036004,
        ],
        [
            1.353023396690570,
            -0.029929946166941,
            0.795804414040809,
            0.348025317624568,
            0.943567007518504,
            -0.276341670431501,
            -2.946594180142590,
            0.289483073507500,
            1.044006950440180,
            -0.000413031960419,
            0.403331114840215,
            0.068427130526696,
        ],
    ]
    fap_l2 = [
        -0.336431283973339,
        2.126038811064490,
        -0.632044932794919,
        5.598995787206250,
        1.770444140578970,
        -0.267879583604849,
    ]
    fap_lo, fap_hi = 0.000153013463222, 0.977135096979553
    fap_neurons = [_neuron_linear(c, z) for c in fap_n]
    fapar = _denorm_ee(_layer_linear(fap_l2, fap_neurons), fap_lo, fap_hi).rename("FAPAR")

    fcv_n = [
        [
            -1.45261652206,
            -0.156854264841,
            0.124234528462,
            0.235625516229,
            -1.8323910258,
            -0.217188969888,
            5.06933958064,
            -0.887578008155,
            -1.0808468167,
            -0.0323167041864,
            -0.224476137359,
            -0.195523962947,
        ],
        [
            -1.70417477557,
            -0.220824927842,
            1.28595395487,
            0.703139486363,
            -1.34481216665,
            -1.96881267559,
            -1.45444681639,
            1.02737560043,
            -0.12494641532,
            0.0802762437265,
            -0.198705918577,
            0.108527100527,
        ],
        [
            1.02168965849,
            -0.409688743281,
            1.08858884766,
            0.36284522554,
            0.0369390509705,
            -0.348012590003,
            -2.0035261881,
            0.0410357601757,
            1.22373853174,
            -0.0124082778287,
            -0.282223364524,
            0.0994993117557,
        ],
        [
            -0.498002810205,
            -0.188970957866,
            -0.0358621840833,
            0.00551248528107,
            1.35391570802,
            -0.739689896116,
            -2.21719530107,
            0.313216124198,
            1.5020168915,
            1.21530490195,
            -0.421938358618,
            1.48852484547,
        ],
        [
            -3.88922154789,
            2.49293993709,
            -4.40511331388,
            -1.91062012624,
            -0.703174115575,
            -0.215104721138,
            -0.972151494818,
            -0.930752241278,
            1.2143441876,
            -0.521665460192,
            -0.445755955598,
            0.344111873777,
        ],
    ]
    fcv_l2 = [
        -0.0967998147811,
        0.23080586765,
        -0.333655484884,
        -0.499418292325,
        0.0472484396749,
        -0.0798516540739,
    ]
    fcv_lo, fcv_hi = 0.000181230723879, 0.999638214715
    fcv_neurons = [_neuron_linear(c, z) for c in fcv_n]
    fcover = _denorm_ee(_layer_linear(fcv_l2, fcv_neurons), fcv_lo, fcv_hi).rename("FCOVER")

    return lai_raw, cab_raw, canopy, fapar, fcover


def reduce_radiometric_resolution(img: ee.Image) -> ee.Image:
    """
    Cuantiza índices a enteros tras ``×100`` (menor resolución radiométrica / tamaño).

    Se aplica **solo al mosaico semanal** listo para exportar (índices ya en reflectancia
    0–1 o rango físico del índice), no por escena: así ``median()`` y el clip no
    reintroducen float sobre enteros ya cuantizados.

    Equivale a la idea de ``img.multiply(100).toInt8()`` en JavaScript; aquí se usa
    **Int16** porque LAI, Cab, CCC y otros pueden superar el rango de Int8 tras escalar.
    ``clear_pixel_count`` no se altera.
    """
    clear = img.select("clear_pixel_count")
    idx = img.select(COMPOSED_INDEX_BANDS).multiply(100).round().toInt16()
    return idx.addBands(clear).copyProperties(img, ["system:time_start", "system:index"])


def add_indices(img: ee.Image) -> ee.Image:
    ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndmi = img.normalizedDifference(["B8", "B11"]).rename("NDMI")
    ndwi = img.normalizedDifference(["B3", "B8"]).rename("NDWI")
    mndwi = img.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    gndvi = img.normalizedDifference(["B8", "B3"]).rename("GNDVI")

    evi = img.expression(
        "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
        {"NIR": img.select("B8"), "RED": img.select("B4"), "BLUE": img.select("B2")},
    ).rename("EVI")

    savi = img.expression(
        "((NIR - RED) / (NIR + RED + L)) * (1.0 + L)",
        {"NIR": img.select("B8"), "RED": img.select("B4"), "L": ee.Image.constant(0.5)},
    ).rename("SAVI")

    msavi = img.expression(
        "(2 * NIR + 1 - sqrt(pow((2 * NIR + 1), 2) - 8 * (NIR - RED))) / 2",
        {"NIR": img.select("B8"), "RED": img.select("B4")},
    ).rename("MSAVI")

    eps = ee.Image.constant(1e-6)
    ari = img.expression(
        "1.0 / (G + eps) - 1.0 / (RE1 + eps)",
        {"G": img.select("B3"), "RE1": img.select("B5"), "eps": eps},
    ).rename("ARI")
    mari = img.expression(
        "(1.0 / (G + eps) - 1.0 / (RE1 + eps)) * NIR",
        {"G": img.select("B3"), "RE1": img.select("B5"), "NIR": img.select("B7"), "eps": eps},
    ).rename("MARI")

    y = ee.Image.constant(0.106)
    arvi = img.expression(
        "(N - R - y * (R - B)) / (N + R - y * (R - B))",
        {"N": img.select("B8A"), "R": img.select("B4"), "B": img.select("B2"), "y": y},
    ).rename("ARVI")

    chl_rededge = img.expression("NIR / RE1 - 1.0", {"NIR": img.select("B7"), "RE1": img.select("B5")}).rename(
        "CHL_REDEDGE"
    )

    b4, b5, b6, b7 = img.select("B4"), img.select("B5"), img.select("B6"), img.select("B7")
    rep_num = b4.add(b7).multiply(0.5).subtract(b5)
    rep_den = b6.subtract(b5).max(1e-6)
    rededge_position = rep_num.multiply(40.0).divide(rep_den).add(700.0).rename("REDEDGE_POSITION")

    evi2 = img.expression(
        "2.4 * (NIR - RED) / (NIR + RED + 1.0)",
        {"NIR": img.select("B8"), "RED": img.select("B4")},
    ).rename("EVI2")

    nd_diff = img.select("B8").subtract(img.select("B4"))
    nd_sum = img.select("B8").add(img.select("B4")).max(1e-6)
    kndvi = nd_diff.divide(nd_sum).pow(2).tanh().rename("kNDVI")

    mcari = img.expression(
        "((RE1 - R) - 0.2 * (RE1 - G)) * (RE1 / (R + eps))",
        {"RE1": img.select("B5"), "R": img.select("B4"), "G": img.select("B3"), "eps": eps},
    ).rename("MCARI")

    msi = img.select("B11").divide(img.select("B8").max(1e-6)).rename("MSI")

    ndmistress = img.normalizedDifference(["B8A", "B11"]).rename("NDMISTRESS")

    ndii = img.normalizedDifference(["B8", "B11"]).rename("NDII")

    ndci = img.normalizedDifference(["B5", "B4"]).rename("NDCI")

    pssrb1 = img.select("B8").divide(img.select("B4").max(1e-6)).rename("PSSRB1")

    sipi1 = img.expression(
        "(NIR - A) / (NIR - R)", {"NIR": img.select("B8"), "A": img.select("B1"), "R": img.select("B4")}
    ).rename("SIPI1")

    b6_safe = img.select("B6").max(1e-6)
    psri = img.select("B4").subtract(img.select("B2")).divide(b6_safe).rename("PSRI")

    lai, leaf_chl, canopy_chl, fapar, fcover = _snap_lai_cab_fapar_fcover(img)

    extra = [
        ndvi,
        ndmi,
        ndwi,
        mndwi,
        gndvi,
        evi,
        savi,
        msavi,
        ari,
        mari,
        arvi,
        chl_rededge,
        rededge_position,
        evi2,
        kndvi,
        mcari,
        msi,
        ndmistress,
        ndii,
        ndci,
        pssrb1,
        sipi1,
        lai,
        leaf_chl,
        canopy_chl,
        fapar,
        fcover,
        psri,
    ]
    return img.addBands(extra)


# Salida de exportación: solo estas bandas (índices compuestos arriba), sin B1…B12 ni otras del sensor.
COMPOSED_INDEX_BANDS = [
    "NDVI",
    "NDMI",
    "NDWI",
    "MNDWI",
    "GNDVI",
    "EVI",
    "SAVI",
    "MSAVI",
    "ARI",
    "MARI",
    "ARVI",
    "CHL_REDEDGE",
    "REDEDGE_POSITION",
    "EVI2",
    "kNDVI",
    "MCARI",
    "MSI",
    "NDMISTRESS",
    "NDII",
    "NDCI",
    "PSSRB1",
    "SIPI1",
    "LAI",
    "LEAF_CHL",
    "CANOPY_CHL",
    "FAPAR",
    "FCOVER",
    "PSRI",
]


def ee_date_min(a: ee.Date, b: ee.Date) -> ee.Date:
    """Menor de dos ``ee.Date``. En JavaScript existe ``ee.Date.min``; en la API Python no."""
    a, b = ee.Date(a), ee.Date(b)
    return ee.Algorithms.If(a.millis().lt(b.millis()), a, b)


def ensure_system_time_start_monday(img: ee.Image, monday_ms: int) -> ee.Image:
    """``system:time_start`` = lunes 00:00 UTC de esa semana ISO (ms)."""
    t0 = ee.Date(monday_ms).millis()
    return ee.Image(img).set("system:time_start", t0)


def get_week_audit_for_year(
    aoi: ee.Geometry,
    calendar_year: int,
    end_exclusive: ee.Date,
) -> list[dict]:
    """
    Conteos por semana ISO del año civil (jueves en ``calendar_year``), misma ventana [lun, lun+7d)
    que el mosaico. Requiere una evaluación al servidor por año.
    """
    end_ms = int(end_exclusive.millis().getInfo())
    specs = iso_week_specs_thursday_in_calendar_year(calendar_year, end_ms)
    if not specs:
        return []

    ys = ee.Date.fromYMD(calendar_year, 1, 1)
    ye = ys.advance(1, "year")
    cap = ee_date_min(ye, end_exclusive)
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi).filterDate(ys, cap)
    linked = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .linkCollection(ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"), ["cs"])
        .filterDate(ys, cap)
    )
    processed = linked.map(mask_and_scale).map(add_indices)

    ms_ee = ee.List([int(s["monday_ms"]) for s in specs])
    iy_ee = ee.List([int(s["iso_year"]) for s in specs])
    iw_ee = ee.List([int(s["iso_week"]) for s in specs])
    n = len(specs)
    idxs = ee.List.sequence(0, n - 1)

    def per_i(i: ee.ComputedObject) -> ee.Dictionary:
        i = ee.Number(i)
        start = ee.Date(ms_ee.get(i))
        end_w = start.advance(7, "day")
        col_raw = s2.filterDate(start, end_w).filterBounds(aoi)
        col_pipeline = processed.filterDate(start, end_w).filterBounds(aoi)
        return ee.Dictionary(
            {
                "iso_year": ee.Number(iy_ee.get(i)),
                "iso_week": ee.Number(iw_ee.get(i)),
                "n_s2": col_raw.size(),
                "n_pipeline": col_pipeline.size(),
            }
        )

    raw = ee.List(idxs.map(per_i)).getInfo()
    for row in raw:
        row["calendar_year"] = calendar_year
        row["week"] = int(row["iso_week"])
    return raw


def year_has_pending_weeks(
    export_prefix: str,
    existing_ids: set[str],
    audit_rows: list[dict],
) -> bool:
    """Hay al menos una semana con datos de pipeline (n_pipeline > 0) y sin asset hijo."""
    for row in audit_rows:
        if int(row.get("n_pipeline", 0)) <= 0:
            continue
        iy = int(row["iso_year"])
        iw = int(row["iso_week"])
        aid = f"{export_prefix}/{export_asset_basename(iy, iw)}"
        if not export_key_exists(aid, existing_ids):
            return True
    return False


def compute_incremental_year_plan(
    export_prefix: str,
    existing_ids: set[str],
    aoi: ee.Geometry,
    end_exclusive: ee.Date,
    start_y: int,
    end_y: int,
) -> tuple[list[int], dict[int, list[dict]]]:
    """
    Un ``get_week_audit_for_year`` por año civil en el rango. Devuelve años con al menos
    una semana exportable aún faltante, y la auditoría cacheada para no repetir la llamada.
    """
    pending: list[int] = []
    audit_by_year: dict[int, list[dict]] = {}
    for y in range(start_y, end_y + 1):
        rows = get_week_audit_for_year(aoi, y, end_exclusive)
        audit_by_year[y] = rows
        if year_has_pending_weeks(export_prefix, existing_ids, rows):
            pending.append(y)
    return pending, audit_by_year


def build_weekly_collection(
    aoi: ee.Geometry,
    end_exclusive: ee.Date,
    calendar_years: list[int],
) -> ee.ImageCollection:
    end_ms = int(end_exclusive.millis().getInfo())
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi)
    cs_plus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    s2_with_clouds = s2.linkCollection(cs_plus, ["cs"])
    processed = s2_with_clouds.filterDate("2017-01-01", end_exclusive).map(mask_and_scale).map(
        add_indices
    )
    imgs: list[ee.Image] = []
    for cy in calendar_years:
        for spec in iso_week_specs_thursday_in_calendar_year(cy, end_ms):
            monday_ms = int(spec["monday_ms"])
            start = ee.Date(monday_ms)
            end_w = start.advance(7, "day")
            week_col = processed.filterDate(start, end_w).filterBounds(aoi)
            idx_median = week_col.select(COMPOSED_INDEX_BANDS).median()
            clear_sum = week_col.select("clear_pixel_count").sum().rename("clear_pixel_count")
            iso_w = int(spec["iso_week"])
            weekly_for_export = reduce_radiometric_resolution(idx_median.addBands(clear_sum)).set(
                {
                    "year": cy,
                    "week": iso_w,
                    "system:time_start": start.millis(),
                    "n_images": week_col.size(),
                }
            )
            imgs.append(weekly_for_export)

    if not imgs:
        return ee.ImageCollection([])

    def _clip_keep_meta(im: ee.ComputedObject) -> ee.Image:
        im = ee.Image(im)
        # No usar .float(): anularía el int16 aplicado en reduce_radiometric_resolution (mosaico semanal).
        return im.clip(aoi).copyProperties(
            im,
            ["system:time_start", "week", "year", "n_images"],
        )

    return (
        ee.ImageCollection(imgs)
        .filter(ee.Filter.gt("n_images", 0))
        .map(_clip_keep_meta)
    )


def export_year(
    final_collection: ee.ImageCollection,
    aoi: ee.Geometry,
    export_prefix: str,
    calendar_year: int,
    *,
    dry_run: bool,
    existing_ids: set[str],
    skip_existing: bool,
    end_exclusive: ee.Date,
    audit: bool,
    audit_rows_precalc: list[dict] | None = None,
) -> tuple[int, int]:
    """Devuelve (encoladas_o_simuladas, omitidas_por_existir)."""
    col = final_collection.filter(ee.Filter.eq("year", calendar_year)).sort("week")
    size = col.size().getInfo()
    img_list = col.toList(size)
    week_to_img: dict[tuple[int, int], ee.Image] = {}
    for i in range(size):
        im = ee.Image(img_list.get(i))
        cy_img = int(im.get("year").getInfo())
        w_img = int(im.get("week").getInfo())
        week_to_img[(cy_img, w_img)] = im

    end_ms = int(end_exclusive.millis().getInfo())
    iso_specs = iso_week_specs_thursday_in_calendar_year(calendar_year, end_ms)

    by_key: dict[tuple[int, int], dict] = {}
    if audit:
        rows = (
            audit_rows_precalc
            if audit_rows_precalc is not None
            else get_week_audit_for_year(aoi, calendar_year, end_exclusive)
        )
        by_key = {(int(r["calendar_year"]), int(r["iso_week"])): r for r in rows}
        print(
            "  (Auditoría: semanas ISO lun–dom; n_s2 / n_pipe; fin de datos = última semana ISO "
            "completa antes del lunes UTC actual. cs≥0.6 solo enmascara píxeles.)"
        )

    n_enq = 0
    n_skip = 0
    n_no_s2 = 0
    n_gap_cs = 0

    for spec in iso_specs:
        iy = int(spec["iso_year"])
        iw = int(spec["iso_week"])
        key = (int(spec["calendar_year"]), iw)
        row = by_key.get(key, {}) if audit else {}
        n_s2 = int(row.get("n_s2", -1)) if audit else -1
        n_pipe = int(row.get("n_pipeline", -1)) if audit else -1
        img = week_to_img.get(key)
        asset_id = f"{export_prefix}/{export_asset_basename(iy, iw)}"

        if audit and n_s2 == 0:
            n_no_s2 += 1
            print(f"  ISO {iy}-W{iw:02d}: sin escenas S2 en AOI → no exportación")
            continue

        if audit and n_s2 > 0 and n_pipe == 0:
            n_gap_cs += 1
            print(
                f"  ISO {iy}-W{iw:02d}: {n_s2} escenas S2 pero 0 en pipeline "
                "(enlace CS+ / ventana) → no exportación"
            )
            continue

        if img is None:
            if audit:
                print(
                    f"  ISO {iy}-W{iw:02d}: inconsistencia (n_pipe={n_pipe}, sin imagen en colección)"
                )
            continue

        desc = f"S2_{iy}W{iw:02d}"
        if skip_existing and export_key_exists(asset_id, existing_ids):
            if audit:
                print(
                    f"  ISO {iy}-W{iw:02d}: ya existe, omitir | n_s2={n_s2} n_pipe={n_pipe} | {asset_id}"
                )
            else:
                print(f"  [ya existe, omitir] {asset_id}")
            n_skip += 1
            continue
        if dry_run:
            if audit:
                print(
                    f"  ISO {iy}-W{iw:02d}: [dry-run] encolaría | n_s2={n_s2} n_pipe={n_pipe} | {asset_id}"
                )
            else:
                print(f"  [dry-run] {desc} -> {asset_id}")
            n_enq += 1
            continue
        if audit:
            print(f"  ISO {iy}-W{iw:02d}: encolado export | n_s2={n_s2} n_pipe={n_pipe} | {asset_id}")
        img = ensure_system_time_start_monday(img, int(spec["monday_ms"]))
        task = ee.batch.Export.image.toAsset(
            image=img,
            description=desc,
            assetId=asset_id,
            region=aoi,
            scale=10,
            maxPixels=1e13,
        )
        task.start()
        n_enq += 1

    if audit:
        n_exportable = len(week_to_img)
        print(
            f"  Resumen {calendar_year}: {n_exportable} semanas con mosaico; "
            f"{n_no_s2} sin S2; {n_gap_cs} con S2 pero pipeline vacío; "
            f"encoladas/simuladas {n_enq}; omitidas (existentes) {n_skip}"
        )
    return n_enq, n_skip


def resolve_year_range(args: argparse.Namespace, now_year: int) -> tuple[int, int]:
    if args.year is not None and (args.start_year is not None or args.end_year is not None):
        raise ValueError("Usa solo --year, o bien --start-year/--end-year, no ambos.")
    if args.year is not None:
        return args.year, args.year
    env_start = os.environ.get("GEE_START_YEAR", "").strip()
    if env_start.isdigit():
        default_start = int(env_start)
    else:
        default_start = DEFAULT_START_YEAR
    env_end = os.environ.get("GEE_END_YEAR", "").strip()
    if env_end.isdigit():
        default_end = int(env_end)
    else:
        default_end = DEFAULT_END_YEAR
    start_y = args.start_year if args.start_year is not None else default_start
    end_y = args.end_year if args.end_year is not None else default_end
    if start_y > end_y:
        raise ValueError(f"--start-year ({start_y}) no puede ser mayor que el último año ({end_y}).")
    return start_y, min(end_y, now_year)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exportar mosaicos S2 semanales a Earth Engine Assets.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No encola tareas; solo lista descripciones y assetIds.",
    )
    parser.add_argument(
        "--project",
        default=None,
        metavar="GCP_PROJECT_ID",
        help=(
            f"Proyecto Google Cloud para ee.Initialize() (por defecto: {DEFAULT_CLOUD_PROJECT}, "
            "salvo EE_CLOUD_PROJECT o GOOGLE_CLOUD_PROJECT)."
        ),
    )
    parser.add_argument(
        "--aoi-asset",
        default=os.environ.get("GEE_AOI_ASSET", DEFAULT_AOI_ASSET),
        help="Earth Engine AssetId del AOI (vectores).",
    )
    parser.add_argument(
        "--export-prefix",
        default=os.environ.get("GEE_EXPORT_PREFIX", DEFAULT_EXPORT_PREFIX),
        help="Prefijo de assetIds de salida sin barra final.",
    )
    parser.add_argument(
        "--skip-ensure-destination",
        action="store_true",
        help="No crear carpeta/colección vacía antes de exportar.",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="No imprimir auditoría semana a semana (solo mensajes mínimos).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Encolar todas las semanas aunque el asset hijo ya exista.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        metavar="AAAA",
        help="Solo ese año civil; no usar junto con --start-year/--end-year.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        metavar="AAAA",
        help="Primer año (default: GEE_START_YEAR o DEFAULT_START_YEAR en el script).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        metavar="AAAA",
        help=(
            "Último año (por defecto: GEE_END_YEAR o DEFAULT_END_YEAR; "
            "nunca por encima del año actual en tiempo de ejecución)."
        ),
    )
    args = parser.parse_args()

    init_project = resolve_cloud_project(args.project)
    ee_initialize(init_project)

    aoi_asset = args.aoi_asset.strip().rstrip("/")
    export_prefix = args.export_prefix.strip().rstrip("/")

    if not args.skip_ensure_destination:
        ensure_export_destination(export_prefix, dry_run=args.dry_run)

    aoi = ee.FeatureCollection(aoi_asset).geometry()
    end_exclusive_ms = utc_monday_00_current_week_ms()
    end_exclusive = ee.Date(end_exclusive_ms)
    now_year = datetime.now(timezone.utc).year

    start_y, end_y = resolve_year_range(args, now_year)

    skip_existing = not args.force
    existing_ids: set[str] = set()
    n_listed = 0
    if skip_existing:
        existing_ids, n_listed = load_existing_export_keys(export_prefix)
        print(f"Assets hijos ya en destino (listAssets): {n_listed}")

    audit_by_year: dict[int, list[dict]] = {}
    if args.force:
        years_to_process = list(range(start_y, end_y + 1))
        print(
            "Modo --force: se procesan todos los años del rango y se re-encolan exportaciones "
            "(aunque el asset ya exista)."
        )
    else:
        print(
            "Modo incremental: comprobando semanas pendientes por año (auditoría, sin construir el grafo completo)..."
        )
        years_to_process, audit_by_year = compute_incremental_year_plan(
            export_prefix,
            existing_ids,
            aoi,
            end_exclusive,
            start_y,
            end_y,
        )
        skipped_years = [y for y in range(start_y, end_y + 1) if y not in years_to_process]
        if skipped_years:
            print(
                "  Años sin trabajo pendiente (todas las semanas con datos ya exportadas o sin datos): "
                f"{skipped_years}"
            )
        if not years_to_process:
            print(
                "Nada que exportar: no quedan semanas con datos sin asset bajo el prefijo. "
                "Usa --force para re-exportar ignorando duplicados."
            )
            return
        print(f"  Años a procesar (al menos una semana pendiente): {years_to_process}")

    build_years = sorted(years_to_process)
    build_start = min(build_years)
    build_end = max(build_years)
    final_collection = build_weekly_collection(aoi, end_exclusive, build_years)
    total = final_collection.size().getInfo()
    n_years_requested = end_y - start_y + 1
    n_years_build = len(build_years)

    print(
        "Fin datos (UTC): escenas con tiempo < lunes 00:00 de la semana ISO actual "
        f"({datetime.fromtimestamp(end_exclusive_ms/1000, tz=timezone.utc).isoformat()}); "
        "última semana exportada es la anterior completa (lun–dom)."
    )
    print("Assets: nombre `Y<iso_año>_W<ww>` con semana ISO 01–53 (p. ej. `Y2026_W09`).")
    print(f"Proyecto Cloud (initialize): {init_project}")
    print(f"AOI asset     : {aoi_asset}")
    print(f"Export prefix : {export_prefix}")
    print(f"Omitir dup.   : {'sí' if skip_existing else 'no ( --force )'}")
    print(
        f"Rango solicitado: {start_y} → {end_y} ({n_years_requested} año(s)); "
        f"grafo GEE construido para {build_start} → {build_end} ({n_years_build} año(s))."
    )
    print(f"Mosaicos con datos en ese grafo (n_images > 0): {total}")
    if n_years_requested > 1 and n_years_build < n_years_requested:
        print(
            "  (Solo años pendientes entran al grafo; el resto ya estaba completo bajo el prefijo.)"
        )

    if args.dry_run:
        print("Modo dry-run: no se envían tareas a GEE.")

    total_enq = 0
    total_skip = 0
    for y in years_to_process:
        print(f"Año {y}...")
        precalc = audit_by_year.get(y) if audit_by_year else None
        enq, skipped = export_year(
            final_collection,
            aoi,
            export_prefix,
            y,
            dry_run=args.dry_run,
            existing_ids=existing_ids,
            skip_existing=skip_existing,
            end_exclusive=end_exclusive,
            audit=not args.no_audit,
            audit_rows_precalc=precalc,
        )
        total_enq += enq
        total_skip += skipped

    print(
        f"Tareas encoladas (o simuladas en dry-run): {total_enq}; "
        f"omitidas (ya existían): {total_skip}"
    )
    print("Revisa el progreso en: https://code.earthengine.google.com/tasks")


if __name__ == "__main__":
    try:
        main()
    except (ee.EEException, ValueError) as exc:
        kind = "Earth Engine" if isinstance(exc, ee.EEException) else "Validación"
        print(f"Error ({kind}): {exc}", file=sys.stderr)
        if isinstance(exc, ee.EEException):
            emsg = str(exc).lower()
            if "memory limit" in emsg:
                print(
                    "Sugerencia: el grafo es muy grande. Sin --force el script solo construye años "
                    "con semanas pendientes; también puedes acotar con `--year AAAA`. "
                    "Si aún falla, reduce el rango (GEE_START_YEAR / GEE_END_YEAR).",
                    file=sys.stderr,
                )
            else:
                print(
                    "Ejecuta `earthengine authenticate` y configura "
                    "EE_CLOUD_PROJECT (o --project=...) con el proyecto Cloud que Earth Engine muestra en "
                    "https://code.earthengine.google.com/ en Configuration "
                    "(necesitas rol Service Usage Consumer en ese proyecto).",
                    file=sys.stderr,
                )
        sys.exit(1)
