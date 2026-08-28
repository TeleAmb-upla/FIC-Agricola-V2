#!/usr/bin/env python3
"""Genera PDF de identidad visual (colores + tipografía) para FIC Agro."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fpdf import FPDF
from matplotlib import colormaps as mpl_colormaps

REPO = Path(__file__).resolve().parents[2]
OUT_PDF = REPO / "documentación" / "PALETA_COLORES_TIPOGRAFIA.pdf"
LOGO_PATH = REPO / "assets" / "img" / "Logo_FIC_Teleamb.png"

WIN_FONTS = Path("C:/Windows/Fonts")
FONT_REGULAR = WIN_FONTS / "calibri.ttf"
FONT_BOLD = WIN_FONTS / "calibrib.ttf"
FONT_ITALIC = WIN_FONTS / "calibrii.ttf"
FONT_BOLD_ITALIC = WIN_FONTS / "calibriz.ttf"
if not FONT_REGULAR.is_file():
    FONT_REGULAR = WIN_FONTS / "arial.ttf"
    FONT_BOLD = WIN_FONTS / "arialbd.ttf"
    FONT_ITALIC = WIN_FONTS / "ariali.ttf"
    FONT_BOLD_ITALIC = WIN_FONTS / "arialbi.ttf"
FONT_FAMILY = "FicSans"

# Layout
ML = 14.0
MR = 14.0
PAGE_W = 210.0
CONTENT_W = PAGE_W - ML - MR  # 182
BOTTOM_SAFE = 272.0


def _hex_to_rgb(hx: str) -> tuple[int, int, int]:
    hx = hx.lstrip("#")
    return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)


class FicPaletaPDF(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_xy(ML, 10)
        self.set_font(FONT_FAMILY, "", 9)
        self.set_text_color(74, 92, 78)
        self.cell(CONTENT_W * 0.7, 6, "FIC Agro  ·  Guía de identidad visual", align="L")
        self.cell(CONTENT_W * 0.3, 6, str(self.page_no()), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(29, 107, 74)
        self.set_line_width(0.45)
        self.line(ML, 17, PAGE_W - MR, 17)
        self.set_y(22)

    def footer(self) -> None:
        self.set_y(-11)
        self.set_font(FONT_FAMILY, "", 8)
        self.set_text_color(140, 150, 145)
        self.cell(0, 6, "Generado desde el código fuente del repositorio FIC Agro", align="C")

    def _fit(self, text: str, width: float) -> str:
        t = str(text or "")
        if self.get_string_width(t) <= width:
            return t
        ell = "…"
        while t and self.get_string_width(t + ell) > width:
            t = t[:-1]
        return t + ell

    def _ensure(self, needed: float) -> None:
        if self.get_y() + needed > BOTTOM_SAFE:
            self.add_page()

    def section_title(self, title: str) -> None:
        self._ensure(18)
        self.set_x(ML)
        self.set_font(FONT_FAMILY, "B", 18)
        self.set_text_color(15, 36, 24)
        self.cell(CONTENT_W, 9, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(45, 157, 110)
        self.set_line_width(1.1)
        self.line(ML, self.get_y(), ML + 42, self.get_y())
        self.ln(6)

    def subsection(self, title: str) -> None:
        self._ensure(72)
        self.set_x(ML)
        self.set_font(FONT_FAMILY, "B", 13)
        self.set_text_color(29, 107, 74)
        self.cell(CONTENT_W, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str) -> None:
        self.set_x(ML)
        self.set_font(FONT_FAMILY, "", 11)
        self.set_text_color(18, 32, 24)
        self.multi_cell(CONTENT_W, 6, text)
        self.ln(3)

    def bullet(self, text: str) -> None:
        self._ensure(10)
        x = ML
        y = self.get_y()
        self.set_xy(x, y)
        self.set_font(FONT_FAMILY, "B", 11)
        self.set_text_color(29, 107, 74)
        self.cell(6, 6, "·")
        self.set_xy(x + 6, y)
        self.set_font(FONT_FAMILY, "", 11)
        self.set_text_color(18, 32, 24)
        self.multi_cell(CONTENT_W - 6, 6, text)
        self.ln(1.5)

    def pantone_chip(
        self,
        x: float,
        y: float,
        w: float,
        name: str,
        hex_code: str,
        rgb: tuple[int, int, int],
        pantone: str,
        usage: str = "",
        color_h: float = 28.0,
        label_h: float = 26.0,
    ) -> None:
        """Ficha tipo pantonera: bloque de color + faja blanca con códigos."""
        r, g, b = rgb
        total_h = color_h + label_h
        self.set_draw_color(210, 218, 212)
        self.set_line_width(0.25)
        self.set_fill_color(r, g, b)
        self.rect(x, y, w, color_h, style="F")
        self.set_fill_color(255, 255, 255)
        self.rect(x, y + color_h, w, label_h, style="F")
        self.rect(x, y, w, total_h, style="D")

        pad = 2.4
        tw = w - pad * 2
        self.set_xy(x + pad, y + color_h + 1.6)
        self.set_font(FONT_FAMILY, "B", 9)
        self.set_text_color(18, 32, 24)
        self.cell(tw, 4.2, self._fit(name, tw), align="L")

        self.set_xy(x + pad, y + color_h + 6.0)
        self.set_font(FONT_FAMILY, "B", 8)
        self.set_text_color(29, 107, 74)
        self.cell(tw, 3.8, self._fit(f"Pantone {pantone}", tw))

        self.set_xy(x + pad, y + color_h + 10.0)
        self.set_font(FONT_FAMILY, "", 8)
        self.set_text_color(74, 92, 78)
        self.cell(tw, 3.6, hex_code.upper())

        self.set_xy(x + pad, y + color_h + 13.6)
        self.set_font(FONT_FAMILY, "", 7.5)
        self.set_text_color(107, 127, 114)
        rr, gg, bb = rgb
        self.cell(tw, 3.4, f"RGB  {rr}  {gg}  {bb}")

        if usage:
            self.set_xy(x + pad, y + color_h + 17.4)
            self.set_font(FONT_FAMILY, "I", 7)
            self.set_text_color(120, 130, 125)
            self.cell(tw, 3.4, self._fit(usage, tw))
        self.set_xy(x, y + total_h)

    def pantone_grid(self, colors: list[tuple], cols: int = 3) -> None:
        gap = 5.0
        chip_w = (CONTENT_W - gap * (cols - 1)) / cols
        color_h = 36.0
        label_h = 22.0
        chip_h = color_h + label_h
        row_gap = 6.0
        n_rows = (len(colors) + cols - 1) // cols
        self._ensure(min(n_rows, 2) * (chip_h + row_gap) + 2)

        row_y = self.get_y()
        for i, item in enumerate(colors):
            name, hx, rgb, pantone, usage = item
            col = i % cols
            if col == 0:
                self._ensure(chip_h + row_gap)
                row_y = self.get_y()
            x = ML + col * (chip_w + gap)
            self.pantone_chip(x, row_y, chip_w, name, hx, rgb, pantone, usage, color_h, label_h)
            if col == cols - 1 or i == len(colors) - 1:
                self.set_y(row_y + chip_h + row_gap)

    def colormap_fan(
        self,
        title: str,
        subtitle: str,
        colors: list[str],
        vmin: str = "",
        vmax: str = "",
    ) -> None:
        """Escala tipo pantonera: tira de chips + faja de metadatos."""
        n = max(len(colors), 1)
        bar_h = 16.0
        meta_h = 14.0
        needed = 12 + bar_h + meta_h + 8
        self._ensure(needed)

        self.set_x(ML)
        self.set_font(FONT_FAMILY, "B", 11)
        self.set_text_color(15, 36, 24)
        self.cell(CONTENT_W, 6, title, new_x="LMARGIN", new_y="NEXT")
        if subtitle:
            self.set_x(ML)
            self.set_font(FONT_FAMILY, "", 9)
            self.set_text_color(74, 92, 78)
            self.cell(CONTENT_W, 5, subtitle, new_x="LMARGIN", new_y="NEXT")
        self.ln(1.5)

        y0 = self.get_y()
        chip_w = CONTENT_W / n
        self.set_draw_color(220, 226, 222)
        self.set_line_width(0.15)
        for i, hx in enumerate(colors):
            r, g, b = _hex_to_rgb(hx)
            self.set_fill_color(r, g, b)
            self.rect(ML + i * chip_w, y0, chip_w, bar_h, style="F")
        self.rect(ML, y0, CONTENT_W, bar_h + meta_h, style="D")

        self.set_fill_color(255, 255, 255)
        self.rect(ML, y0 + bar_h, CONTENT_W, meta_h, style="F")
        self.set_xy(ML + 3, y0 + bar_h + 1.8)
        self.set_font(FONT_FAMILY, "", 9)
        self.set_text_color(74, 92, 78)
        left = f"min  {vmin}" if vmin else ""
        self.cell(CONTENT_W / 2 - 3, 5, left)
        self.set_xy(ML + CONTENT_W / 2, y0 + bar_h + 1.8)
        right = f"max  {vmax}" if vmax else ""
        self.cell(CONTENT_W / 2 - 3, 5, right, align="R")

        # HEX de extremos
        self.set_xy(ML + 3, y0 + bar_h + 7.2)
        self.set_font(FONT_FAMILY, "", 8)
        self.set_text_color(107, 127, 114)
        self.cell(CONTENT_W / 2 - 3, 4, colors[0].upper())
        self.set_xy(ML + CONTENT_W / 2, y0 + bar_h + 7.2)
        self.cell(CONTENT_W / 2 - 3, 4, colors[-1].upper(), align="R")

        self.set_y(y0 + bar_h + meta_h + 6)

    def spec_card(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        element: str,
        family: str,
        size: str,
        weight: str,
        notes: str,
    ) -> None:
        self.set_fill_color(251, 253, 249)
        self.set_draw_color(197, 212, 192)
        self.set_line_width(0.3)
        self.rect(x, y, w, h, style="DF")
        pad = 3.2
        inner = w - pad * 2
        self.set_xy(x + pad, y + 2.2)
        self.set_font(FONT_FAMILY, "B", 9)
        self.set_text_color(15, 36, 24)
        self.cell(inner, 5, self._fit(element, inner), align="L")

        self.set_xy(x + pad, y + 8.0)
        self.set_font(FONT_FAMILY, "", 9)
        self.set_text_color(29, 107, 74)
        self.cell(inner, 4.4, self._fit(f"{family}   ·   {weight}", inner))

        self.set_xy(x + pad, y + 12.8)
        self.set_font(FONT_FAMILY, "", 8.5)
        self.set_text_color(74, 92, 78)
        self.cell(inner, 4.2, self._fit(size, inner))

        self.set_xy(x + pad, y + 17.4)
        self.set_font(FONT_FAMILY, "I", 8)
        self.set_text_color(107, 127, 114)
        self.cell(inner, 4, self._fit(notes, inner))
        self.set_xy(x, y + h)

    def spec_grid(self, rows: list[tuple[str, str, str, str, str]], cols: int = 2) -> None:
        gap = 5.0
        card_w = (CONTENT_W - gap * (cols - 1)) / cols
        card_h = 24.0
        row_gap = 4.0
        row_y = self.get_y()
        for i, row in enumerate(rows):
            col = i % cols
            if col == 0:
                self._ensure(card_h + row_gap)
                row_y = self.get_y()
            x = ML + col * (card_w + gap)
            self.spec_card(x, row_y, card_w, card_h, *row)
            if col == cols - 1 or i == len(rows) - 1:
                self.set_y(row_y + card_h + row_gap)

    def render_spec_section(self, title: str, rows: list[tuple[str, str, str, str, str]]) -> None:
        self.subsection(title)
        self.spec_grid(rows)
        self.ln(2)


# —— Datos ——

BRAND_COLORS = [
    ("Verde oscuro", "#0F2418", (15, 36, 24), "5535 C", "Títulos y hover"),
    ("Verde principal", "#1D6B4A", (29, 107, 74), "342 C", "Color institucional"),
    ("Verde acento", "#2D9D6E", (45, 157, 110), "3395 C", "Focus y pills"),
    ("Tinta", "#122018", (18, 32, 24), "5535 C", "Texto principal"),
    ("Texto secundario", "#4A5C4E", (74, 92, 78), "5605 C", "Labels y ayudas"),
    ("Verde gradiente", "#145A3A", (20, 90, 58), "3425 C", "Fin de gradiente"),
    ("Verde login", "#0D4D35", (13, 77, 53), "3435 C", "Botón login"),
]

LOGIN_COLORS = [
    ("Fondo", "#EEF4ED", (238, 244, 237), "9060 C", "Fondo de página"),
    ("Superficie", "#FFFFFF", (255, 255, 255), "White", "Tarjeta login"),
    ("Borde", "#C5D4C0", (197, 212, 192), "558 C", "Inputs y marco"),
    ("Texto", "#1A2E1F", (26, 46, 31), "560 C", "Texto principal"),
    ("Peligro", "#7A1E1E", (122, 30, 30), "7427 C", "Errores"),
]

EXPLORER_BG = [
    ("Fondo mapa", "#0D120F", (13, 18, 15), "Black 7 C", "Leaflet / split"),
    ("Panel claro A", "#FBFDF9", (251, 253, 249), "9061 C", "Gradiente panel"),
    ("Panel claro B", "#F3F8F3", (243, 248, 243), "9060 C", "Gradiente panel"),
    ("Panel claro C", "#EBF3EC", (235, 243, 236), "9041 C", "Gradiente panel"),
    ("Popup label", "#6B7F72", (107, 127, 114), "5645 C", "Etiquetas cuartel"),
]

SEMANTIC = [
    ("NDVI", "#2D8A4E", (45, 138, 78), "347 C", "Vegetación"),
    ("NDWI", "#2B6CB0", (43, 108, 176), "7689 C", "Agua"),
    ("Advertencia", "#C4A035", (196, 160, 53), "7405 C", "Avisos"),
    ("Error", "#7A1E1E", (122, 30, 30), "7427 C", "Mensajes error"),
    ("Contraste chart", "#C45035", (196, 80, 53), "7586 C", "Serie secundaria"),
]

SOURCES = [
    ("Sentinel-2 UI", "#1D6B4A", (29, 107, 74), "342 C", "Manifiesto activo"),
    ("Dron", "#22C55E", (34, 197, 94), "2270 C", "Fuente dron"),
    ("Sentinel-1", "#0F766E", (15, 118, 110), "3285 C", "Planificado"),
    ("S2 config", "#38BDF8", (56, 189, 248), "2915 C", "No usado en UI"),
]

CHART_COLORS = [
    ("Histórico", "#1D6B4A", (29, 107, 74), "342 C", "Línea actual 92%"),
    ("Relleno hist.", "#C5DCCF", (197, 220, 207), "559 C", "rgba 18% verde"),
    ("Contraste año", "#C45035", (196, 80, 53), "7586 C", "Serie secundaria"),
]

INDEX_COLORMAPS: list[dict] = [
    {"id": "NDVI", "label": "NDVI", "source": "Sentinel-2", "cmap": "RdYlGn",
     "vmin": "-1", "vmax": "1", "desc": "Índice de vegetación normalizado"},
    {"id": "NDMI", "label": "NDMI", "source": "Sentinel-2", "cmap": "RdYlBu",
     "vmin": "-1", "vmax": "1", "desc": "Índice de humedad de canopy"},
    {"id": "MNDWI", "label": "MNDWI", "source": "Sentinel-2", "cmap": "Blues",
     "vmin": "-1", "vmax": "1", "desc": "Agua en superficie modificada"},
    {"id": "REDEDGE_POSITION", "label": "Posición red edge", "source": "Sentinel-2", "cmap": "viridis",
     "vmin": "700 nm", "vmax": "750 nm", "desc": "Posición del borde rojo"},
    {"id": "MCARI", "label": "MCARI", "source": "Sentinel-2", "cmap": "YlGn",
     "vmin": "0", "vmax": "0.1", "desc": "Índice de clorofila ajustado"},
    {"id": "GNDVI", "label": "GNDVI", "source": "Sentinel-2", "cmap": "YlGn",
     "vmin": "-1", "vmax": "1", "desc": "Vegetación verde normalizada"},
    {"id": "MSAVI", "label": "MSAVI", "source": "Sentinel-2", "cmap": "YlGn",
     "vmin": "-1", "vmax": "1", "desc": "Vegetación ajustada al suelo"},
    {"id": "EVI", "label": "EVI", "source": "Sentinel-2", "cmap": "YlGn",
     "vmin": "-1", "vmax": "1", "desc": "Índice de vegetación mejorado"},
    {"id": "PSRI", "label": "PSRI", "source": "Sentinel-2", "cmap": "RdYlGn_r",
     "vmin": "-0.5", "vmax": "0.5", "desc": "Reflectancia estructural (invertido)"},
    {"id": "ndvi", "label": "NDVI", "source": "Dron", "cmap": "RdYlGn",
     "vmin": "-1", "vmax": "1", "desc": "Índice de vegetación"},
    {"id": "ndwi", "label": "NDWI", "source": "Dron", "cmap": "RdYlBu",
     "vmin": "-1", "vmax": "1", "desc": "Índice de humedad"},
    {"id": "ndci", "label": "NDCI", "source": "Dron", "cmap": "RdYlGn",
     "vmin": "-1", "vmax": "1", "desc": "Clorofila (borde rojo)"},
    {"id": "thermal", "label": "Térmica", "source": "Dron", "cmap": "Turbo",
     "vmin": "P0", "vmax": "P98", "desc": "Ortomosaico térmico por percentiles"},
]

UNIQUE_COLORMAPS: list[dict] = [
    {"name": "RdYlGn", "desc": "Rojo–amarillo–verde", "indices": "NDVI, NDCI"},
    {"name": "RdYlGn_r", "desc": "RdYlGn invertido", "indices": "PSRI"},
    {"name": "RdYlBu", "desc": "Rojo–amarillo–azul", "indices": "NDMI, NDWI dron"},
    {"name": "YlGn", "desc": "Amarillo–verde", "indices": "MCARI, GNDVI, MSAVI, EVI"},
    {"name": "Blues", "desc": "Escala de azules", "indices": "MNDWI"},
    {"name": "viridis", "desc": "Escala perceptual", "indices": "Red edge"},
    {"name": "Turbo", "desc": "Escala multicolor", "indices": "Térmica dron"},
    {"name": "RdPu", "desc": "Rojo–púrpura (reservado)", "indices": "No usado"},
]

LOGO_COLORS = [
    ("Azul marino", "#003860", (0, 56, 96), "3025 C", "Satélite y dron"),
    ("Azul petróleo", "#186870", (24, 104, 112), "316 C", "Mosaico digital"),
    ("Cian turquesa", "#90D0D8", (144, 208, 216), "2905 C", "Cielo pixelado"),
    ("Dorado montaña", "#D89820", (216, 152, 32), "7555 C", "Montañas"),
    ("Amarillo cultivo", "#E8E080", (232, 224, 128), "600 C", "Suelo iluminado"),
    ("Verde cultivo", "#70B878", (112, 184, 120), "7488 C", "Vegetación"),
]

TYPO_FAMILIES = [
    ("Source Sans 3", "Google Fonts", "400 / 600 / 700 + italic", "Regular–Bold", "Cuerpo, UI, popups, selects"),
    ("Outfit", "Google Fonts", "500 / 600 / 700 / 800", "Medium–ExtraBold", "Títulos, CTAs, eyebrows"),
    ("IBM Plex Mono", "Referenciada", "600  ·  fallback Consolas", "Mono", "Valores vmin / vmax"),
]

TYPO_WEIGHTS = [
    ("Outfit 500", "Outfit", "Medium", "500", "Disponible, uso puntual"),
    ("Outfit 600", "Outfit", "SemiBold", "600", "Títulos secundarios"),
    ("Outfit 700", "Outfit", "Bold", "700", "Eyebrows y CTAs"),
    ("Outfit 800", "Outfit", "ExtraBold", "800", "Títulos topbar y gráficos"),
    ("Source Sans 400", "Source Sans 3", "Regular", "400", "Cuerpo y párrafos"),
    ("Source Sans 600", "Source Sans 3", "SemiBold", "600", "Selects y pills"),
    ("Source Sans 700", "Source Sans 3", "Bold", "700", "Labels y strong"),
]

TYPO_LOGIN = [
    ("body", "Source Sans 3", "15–16 px  ·  lh 1.55", "400", "Texto base login"),
    (".fic-login-brand h1", "Outfit", "1.05–1.2 rem  ·  −0.02em", "800", "Título de marca"),
    (".fic-login-brand p", "Source Sans 3", "0.86 rem", "400", "Subtítulo muted"),
    (".fic-login-eyebrow", "Outfit", "0.72 rem  ·  +0.12em", "700", "UPPERCASE acento"),
    (".fic-login-lead", "Source Sans 3", "0.92 rem", "400", "Texto introductorio"),
    (".fic-field label", "Source Sans 3", "0.82 rem", "700", "Labels formulario"),
    (".fic-field input", "Source Sans 3", "inherit body", "400", "Campos de entrada"),
    (".fic-btn", "Outfit", "0.98 rem", "700", "Botón principal blanco"),
    (".fic-error", "Source Sans 3", "0.88 rem", "400", "Mensajes de error"),
    (".fic-hint", "Source Sans 3", "0.78 rem", "400", "Pie de tarjeta"),
]

TYPO_EXPLORER_BASE = [
    ("body.fic-explorer", "Source Sans 3", "15.5–17 px  ·  lh 1.5", "400", "Texto base explorador"),
    (".fic-topbar-kicker", "Outfit", "0.65 rem  ·  +0.15em", "700", "UPPERCASE kicker"),
    (".fic-topbar-title", "Outfit", "1.08–1.32 rem  ·  −0.038em", "800", "Título con gradiente"),
    (".fic-hier-badge-wrap", "Source Sans 3", "0.88 rem", "400 / 700", "Badge de jerarquía"),
    (".btn-ghost", "Source Sans 3", "0.86 rem", "600", "Botón secundario"),
    (".fic-origin-picker__label", "Source Sans 3", "0.74 rem", "700", "Selector origen"),
    (".fic-source-pills-label", "Outfit", "0.72 rem  ·  +0.06em", "700", "UPPERCASE pills"),
    (".fic-source-pill", "Source Sans 3", "0.84 rem", "600", "Pills S2 / dron"),
    (".helper", "Source Sans 3", "0.9 rem  ·  lh 1.45", "400", "Texto de ayuda"),
    (".selector-label", "Source Sans 3", "0.68 rem  ·  +0.065em", "700", "UPPERCASE select"),
    (".fic-sidebar-block select", "Source Sans 3", "0.9 rem  ·  lh 1.35", "600", "Selects del panel"),
    (".fic-panel-error", "Source Sans 3", "0.86 rem", "400", "Error del panel"),
]

TYPO_EXPLORER_INDEX = [
    (".fic-index-desc__label", "Outfit", "0.66 rem  ·  +0.09em", "700", "UPPERCASE índice"),
    (".fic-index-desc__text", "Source Sans 3", "0.78 rem  ·  lh 1.5", "400", "Descripción índice"),
    (".fic-index-desc__scale-head", "Source Sans 3", "0.62 rem  ·  +0.07em", "700", "UPPERCASE escala"),
    (".fic-index-desc__scale-bar", "IBM Plex Mono", "0.8 rem", "600", "Valores min / max"),
    (".fic-index-desc__scale-hint", "Source Sans 3", "0.64 rem", "400", "Hint bajo escala"),
    (".fic-index-desc__row dt", "Source Sans 3", "0.62 rem  ·  +0.06em", "700", "UPPERCASE fila"),
    (".fic-index-desc__row dd", "Source Sans 3", "0.78 rem  ·  lh 1.48", "400", "Detalle de fila"),
    (".fic-sidebar-group__eyebrow", "Outfit", "0.66 rem  ·  +0.08em", "700", "UPPERCASE grupo"),
    (".fic-sat-mode-pill", "Source Sans 3", "0.78 rem  ·  +0.04em", "700", "UPPERCASE modo"),
    (".fic-index-desc__footnote", "Source Sans 3", "0.7 rem  ·  lh 1.45", "400", "Nota al pie"),
]

TYPO_EXPLORER_MAP = [
    (".fic-map-title", "Source Sans 3", "1.02–1.2 rem  ·  +0.01em", "500", "Título sobre el mapa"),
    (".fic-map-title strong", "Source Sans 3", "inherit", "700", "Énfasis título mapa"),
    (".fic-sat-compare-yrs__k", "Source Sans 3", "0.72 rem  ·  +0.02em", "800", "Etiqueta año"),
    (".fic-sat-compare-yrs__v", "Outfit", "1.1 rem  ·  −0.02em", "800", "Valor de año"),
    (".map-legend", "Source Sans 3", "0.86 rem  ·  lh 1.4", "400", "Leyenda flotante"),
    (".colorbar-title", "Source Sans 3", "0.72 rem", "700", "Título barra color"),
    (".colorbar-scale span", "Source Sans 3", "0.74 rem", "700", "Extremos vmin/vmax"),
    (".fic-cuartel-popup", "Source Sans 3", "13 px  ·  lh 1.55", "400", "Popup Leaflet"),
    (".fic-cuartel-popup__title", "Source Sans 3", "14 px", "700", "Título popup"),
    (".leaflet-control-zoom a", "Source Sans 3", "18 px", "400", "Controles + / −"),
    (".fic-sat-period-custom-slider__thumb", "Source Sans 3", "0.72 rem  ·  −0.02em", "800", "Pill temporal"),
    (".fic-sat-period-step", "Source Sans 3", "inherit", "400", "Paso semana / mes"),
]

TYPO_EXPLORER_CHARTS = [
    (".fic-sat-chart-title", "Outfit", "0.88 rem  ·  −0.01em", "800", "Título gráfico"),
    (".fic-sat-chart-sub", "Source Sans 3", "0.72 rem", "400", "Subtítulo gráfico"),
    (".fic-sat-chart-series-pill", "Source Sans 3", "0.72 rem", "700", "Pills histórico / actual"),
    (".fic-sat-chart-bandlabel", "Source Sans 3", "0.7 rem  ·  +0.04em", "700", "UPPERCASE banda"),
    (".fic-sat-chart-bandsel", "Source Sans 3", "0.78 rem", "600", "Selector de banda"),
    (".fic-sat-chart-toggle", "Source Sans 3", "0.78 rem", "700", "Abrir / cerrar gráfico"),
    (".fic-drone-opacity label", "Source Sans 3", "0.68 rem", "700", "Opacidad dron"),
    (".las3d-hint", "Source Sans 3", "0.72 rem", "400", "Hint vista 3D"),
]


def sample_colormap_hex(name: str, n: int = 11) -> list[str]:
    reversed_cmap = str(name).endswith("_r")
    base = name[:-2] if reversed_cmap else str(name)
    keys = {k.lower(): k for k in mpl_colormaps}
    key = keys.get(base.lower())
    if not key:
        raise ValueError(f"Colormap desconocido: {name!r}")
    cmap = mpl_colormaps.get_cmap(key)
    if reversed_cmap:
        cmap = cmap.reversed()
    t = np.linspace(0.0, 1.0, n)
    rgba = cmap(t)
    out: list[str] = []
    for r, g, b, _a in rgba:
        out.append(f"#{int(round(r * 255)):02X}{int(round(g * 255)):02X}{int(round(b * 255)):02X}")
    return out


_CMAP_CACHE: dict[str, list[str]] = {}


def get_cmap_colors(name: str) -> list[str]:
    if name not in _CMAP_CACHE:
        _CMAP_CACHE[name] = sample_colormap_hex(name)
    return _CMAP_CACHE[name]


def build_pdf() -> Path:
    pdf = FicPaletaPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False, margin=14)
    pdf.set_left_margin(ML)
    pdf.set_right_margin(MR)

    for style, fpath in [
        ("", FONT_REGULAR),
        ("B", FONT_BOLD),
        ("I", FONT_ITALIC),
        ("BI", FONT_BOLD_ITALIC),
    ]:
        pdf.add_font(FONT_FAMILY, style, str(fpath))

    # —— Portada ——
    pdf.add_page()
    pdf.set_fill_color(15, 36, 24)
    pdf.rect(0, 0, 210, 297, style="F")

    if LOGO_PATH.is_file():
        pdf.image(str(LOGO_PATH), x=75, y=32, w=60)

    pdf.set_xy(ML, 104)
    pdf.set_font(FONT_FAMILY, "B", 32)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(CONTENT_W, 14, "FIC Agro", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "", 14)
    pdf.set_text_color(200, 230, 210)
    pdf.cell(CONTENT_W, 8, "Guía de identidad visual", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "I", 11)
    pdf.set_text_color(160, 190, 175)
    pdf.cell(CONTENT_W, 6, "Paleta  ·  tipografía  ·  colormaps", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_fill_color(29, 107, 74)
    pdf.rect(0, 142, 210, 3.5, style="F")
    pdf.set_fill_color(45, 157, 110)
    pdf.rect(0, 145.5, 210, 1.6, style="F")

    # Pantonera de logo en portada
    n = len(LOGO_COLORS)
    gap = 4.0
    chip_w = (CONTENT_W - gap * (n - 1)) / n
    y_chip = 162
    for i, (name, hx, rgb, pantone, _u) in enumerate(LOGO_COLORS):
        x = ML + i * (chip_w + gap)
        r, g, b = rgb
        pdf.set_fill_color(r, g, b)
        pdf.rect(x, y_chip, chip_w, 38, style="F")
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(x, y_chip + 38, chip_w, 18, style="F")
        ink = (18, 32, 24)
        pdf.set_xy(x + 1.4, y_chip + 39.2)
        pdf.set_font(FONT_FAMILY, "B", 6.5)
        pdf.set_text_color(*ink)
        pdf.cell(chip_w - 2.8, 4, pdf._fit(name, chip_w - 2.8))
        pdf.set_xy(x + 1.4, y_chip + 43.4)
        pdf.set_font(FONT_FAMILY, "", 6)
        pdf.set_text_color(74, 92, 78)
        pdf.cell(chip_w - 2.8, 3.6, hx.upper())
        pdf.set_xy(x + 1.4, y_chip + 47.0)
        pdf.cell(chip_w - 2.8, 3.6, pantone)

    pdf.set_xy(ML, 230)
    pdf.set_font(FONT_FAMILY, "", 10)
    pdf.set_text_color(180, 200, 190)
    pdf.multi_cell(
        CONTENT_W, 5.5,
        "Monitoreo Satelital Agrícola  —  FIC TeleAmb UPLA\n"
        "Colores del logo y de la interfaz  ·  HEX / RGB / Pantone Coated (aprox.)\n"
        "Tipografías web: Source Sans 3 + Outfit",
        align="C",
    )

    # —— Tipografía ——
    pdf.add_page()
    pdf.section_title("1. Tipografía")
    pdf.body_text(
        "El sitio carga Source Sans 3 y Outfit desde Google Fonts. "
        "Outfit se reserva para titulares, botones y etiquetas en mayúsculas. "
        "Source Sans 3 cubre el resto de la interfaz. "
        "IBM Plex Mono está referenciada en CSS pero no se importa: "
        "los valores numéricos usan Consolas o la monospace del sistema."
    )
    pdf.render_spec_section("1.1 Familias", TYPO_FAMILIES)
    pdf.render_spec_section("1.2 Escala de pesos", TYPO_WEIGHTS)

    pdf.add_page()
    pdf.render_spec_section("1.3 Login  —  index.html", TYPO_LOGIN)

    pdf.add_page()
    pdf.render_spec_section("1.4 Explorador  —  panel lateral", TYPO_EXPLORER_BASE)

    pdf.add_page()
    pdf.render_spec_section("1.5 Explorador  —  índices y modo temporal", TYPO_EXPLORER_INDEX)

    pdf.add_page()
    pdf.render_spec_section("1.6 Explorador  —  mapa, leyendas y popups", TYPO_EXPLORER_MAP)

    pdf.add_page()
    pdf.render_spec_section("1.7 Explorador  —  gráficos y vista 3D", TYPO_EXPLORER_CHARTS)
    pdf.subsection("1.8 Reglas de uso")
    pdf.bullet("Títulos display: Outfit 700–800 con tracking negativo (−0.02em a −0.038em).")
    pdf.bullet("Eyebrows y labels de sección: mayúsculas + tracking 0.06em–0.15em.")
    pdf.bullet("Cuerpo y controles: Source Sans 3 en 400 (texto) o 600–700 (UI).")
    pdf.bullet("Tamaños fluidos: clamp() en body y títulos principales.")
    pdf.bullet("Impresión: Montserrat o Gotham (titulares) y Source Sans Pro (cuerpo).")

    # —— Colores logo ——
    pdf.add_page()
    pdf.section_title("2. Paleta de colores")
    pdf.subsection("2.1 Logo FIC TeleAmb")
    pdf.body_text(
        "Colores dominantes extraídos de assets/img/Logo_FIC_Teleamb.png. "
        "El logo usa azules, teales y dorados; la interfaz web complementa "
        "con verdes institucionales. Ambas paletas conviven en la marca."
    )
    if LOGO_PATH.is_file():
        pdf._ensure(42)
        y_logo = pdf.get_y()
        pdf.image(str(LOGO_PATH), x=ML, y=y_logo, w=36)
        pdf.set_xy(ML + 42, y_logo + 8)
        pdf.set_font(FONT_FAMILY, "I", 10)
        pdf.set_text_color(74, 92, 78)
        pdf.multi_cell(CONTENT_W - 42, 5.5, "Marca pictórica oficial.\n500 × 500 px  ·  sin tipografía en el isotipo.")
        pdf.set_y(y_logo + 40)
    pdf.pantone_grid(LOGO_COLORS, cols=3)

    pdf.add_page()
    pdf.subsection("2.2 Identidad UI  —  verdes institucionales")
    pdf.pantone_grid(BRAND_COLORS, cols=3)

    pdf.add_page()
    pdf.subsection("2.3 Login")
    pdf.pantone_grid(LOGIN_COLORS, cols=3)
    pdf.ln(2)
    pdf.subsection("2.4 Fondos y paneles del explorador")
    pdf.pantone_grid(EXPLORER_BG, cols=3)

    pdf.add_page()
    pdf.subsection("2.5 Colores semánticos")
    pdf.pantone_grid(SEMANTIC, cols=3)
    pdf.ln(2)
    pdf.subsection("2.6 Fuentes de datos")
    pdf.pantone_grid(SOURCES, cols=2)

    pdf.add_page()
    pdf.subsection("2.7 Gráficos temporales (Chart.js)")
    pdf.pantone_grid(CHART_COLORS, cols=3)

    # —— Colormaps ——
    pdf.add_page()
    pdf.section_title("3. Colormaps por índice")
    pdf.body_text(
        "Paletas científicas (ColorBrewer / matplotlib) usadas en mapas y leyendas. "
        "Cada tira se lee como pantonera: vmin a la izquierda, vmax a la derecha."
    )
    pdf.subsection("3.1 Sentinel-2")
    for entry in INDEX_COLORMAPS:
        if entry["source"] != "Sentinel-2":
            continue
        pdf.colormap_fan(
            f"{entry['label']}   ·   {entry['cmap']}",
            f"{entry['desc']}   ·   {entry['source']}",
            get_cmap_colors(entry["cmap"]),
            entry["vmin"],
            entry["vmax"],
        )

    pdf.add_page()
    pdf.subsection("3.2 Dron multiespectral")
    for entry in INDEX_COLORMAPS:
        if entry["source"] != "Dron":
            continue
        pdf.colormap_fan(
            f"{entry['label']}   ·   {entry['cmap']}",
            f"{entry['desc']}   ·   {entry['source']}",
            get_cmap_colors(entry["cmap"]),
            entry["vmin"],
            entry["vmax"],
        )
    pdf.body_text(
        "RGB (dron) es ortomosaico en color verdadero y no usa colormap. "
        "CLEAR_PIXEL_COUNT es banda auxiliar y no se publica en el mapa."
    )

    pdf.add_page()
    pdf.subsection("3.3 Referencia de colormaps base")
    for uc in UNIQUE_COLORMAPS:
        pdf.colormap_fan(
            uc["name"],
            f"{uc['desc']}   ·   {uc['indices']}",
            get_cmap_colors(uc["name"]),
        )

    # —— Resumen ——
    pdf.add_page()
    pdf.section_title("4. Resumen ejecutivo")
    summary = [
        ("Primario UI", "#1D6B4A", (29, 107, 74), "342 C", "Verde institucional"),
        ("Acento UI", "#2D9D6E", (45, 157, 110), "3395 C", "Focus y hover"),
        ("Oscuro", "#0F2418", (15, 36, 24), "5535 C", "Títulos y portada"),
        ("Azul logo", "#003860", (0, 56, 96), "3025 C", "Satélite / dron"),
        ("Fondo claro", "#EEF4ED", (238, 244, 237), "9060 C", "Login y paneles"),
        ("Fondo mapa", "#0D120F", (13, 18, 15), "Black 7 C", "Leaflet"),
    ]
    pdf.pantone_grid(summary, cols=3)
    pdf.ln(2)
    pdf.subsection("Recomendaciones")
    pdf.bullet("Digital: usar siempre HEX. Pantone es referencia de impresión.")
    pdf.bullet("Marca UI: #1D6B4A + #2D9D6E sobre #EEF4ED o #0D120F.")
    pdf.bullet("Marca logo: #003860 + #D89820 + #90D0D8.")
    pdf.bullet("Tipografía: Outfit en titulares; Source Sans 3 en interfaz.")
    pdf.bullet("Validar Pantone Coated con muestrario físico antes de imprimir.")

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    tmp_pdf = OUT_PDF.with_suffix(".tmp.pdf")
    pdf.output(str(tmp_pdf))
    try:
        tmp_pdf.replace(OUT_PDF)
    except PermissionError:
        fallback = OUT_PDF.with_name(OUT_PDF.stem + "_actualizado.pdf")
        try:
            tmp_pdf.replace(fallback)
        except PermissionError:
            fallback = OUT_PDF.with_name(OUT_PDF.stem + "_v2.pdf")
            tmp_pdf.replace(fallback)
        print(f"AVISO: {OUT_PDF.name} está abierto. PDF guardado como: {fallback}")
        return fallback
    return OUT_PDF


if __name__ == "__main__":
    path = build_pdf()
    print(f"PDF generado: {path}")
