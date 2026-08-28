#!/usr/bin/env python3
"""PDF de normas gráficas del proyecto FIC (base TELEAMB-NG), sin reemplazar PALETA_COLORES_TIPOGRAFIA.pdf."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

REPO = Path(__file__).resolve().parents[2]
OUT_PDF = REPO / "documentación" / "FIC_NORMAS_GRAFICAS.pdf"
LOGO_PATH = REPO / "assets" / "img" / "Logo_FIC_Teleamb.png"
NG_SOURCE = REPO / "documentación" / "TELEAMB-NG.pdf"

WIN_FONTS = Path("C:/Windows/Fonts")
# Bahnschrift ≈ DIN 1451 (misma familia geométrica que DIN Pro).
if (WIN_FONTS / "bahnschrift.ttf").is_file():
    FONT_REGULAR = WIN_FONTS / "bahnschrift.ttf"
    FONT_BOLD = WIN_FONTS / "bahnschrift.ttf"
else:
    FONT_REGULAR = WIN_FONTS / "calibri.ttf"
    FONT_BOLD = WIN_FONTS / "calibrib.ttf"
    if not FONT_REGULAR.is_file():
        FONT_REGULAR = WIN_FONTS / "arial.ttf"
        FONT_BOLD = WIN_FONTS / "arialbd.ttf"
FONT_ITALIC = WIN_FONTS / "calibrii.ttf"
if not FONT_ITALIC.is_file():
    FONT_ITALIC = WIN_FONTS / "ariali.ttf"
FONT_FAMILY = "FicNgSans"

NAVY = (3, 62, 96)
TEAL = (44, 184, 199)
TEAL_LIGHT = (173, 219, 227)
INK = (3, 62, 96)
MUTED = (90, 110, 120)

ML = 14.0
MR = 14.0
PAGE_W = 210.0
CONTENT_W = PAGE_W - ML - MR
BOTTOM_SAFE = 272.0

# Paleta oficial del proyecto FIC (manual de normas gráficas, PAG 2).
NG_COLORS = [
    ("Azul marino", "#033E60", (3, 62, 96), (100, 71, 37, 29), "FIC, iconos, fondos oscuros"),
    ("Cian FIC", "#2CB8C7", (44, 184, 199), (70, 0, 24, 0), "Agro, títulos, barras de sección"),
    ("Cian claro", "#ADDBE3", (173, 219, 227), (37, 0, 13, 0), "Acentos claros, agua / datos"),
    ("Verde bosque", "#2E9967", (46, 153, 103), (78, 15, 72, 2), "Vegetación, medio ambiente"),
    ("Verde medio", "#5CB77D", (92, 183, 125), (65, 0, 64, 0), "Cultivo, predios y campos"),
    ("Amarillo pálido", "#EEEB90", (238, 235, 144), (11, 0, 55, 0), "Suelo / luz suave"),
    ("Amarillo", "#EEDF55", (238, 223, 85), (11, 5, 76, 0), "Píxeles, acento solar"),
    ("Oro", "#E5B341", (229, 179, 65), (11, 30, 82, 2), "Montañas, cielo cálido"),
    ("Naranja", "#DD9217", (221, 146, 23), (12, 47, 96, 2), "Montañas, borde de grilla"),
    ("Terracota", "#CF653D", (207, 101, 61), (15, 69, 79, 4), "Acento cálido del isotipo"),
]


class FicNgPDF(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_fill_color(*NAVY)
        self.rect(0, 0, PAGE_W, 16, style="F")
        self.set_xy(ML, 4.2)
        self.set_font(FONT_FAMILY, "B", 12)
        self.set_text_color(255, 255, 255)
        self.cell(18, 8, "FIC")
        self.set_text_color(*TEAL)
        self.cell(50, 8, "Agro")
        self.set_xy(PAGE_W - MR - 70, 4.2)
        self.set_font(FONT_FAMILY, "", 9)
        self.set_text_color(*TEAL_LIGHT)
        self.cell(50, 8, "NORMAS GRÁFICAS", align="R")
        self.set_fill_color(*TEAL)
        self.rect(PAGE_W - MR - 16, 4.5, 16, 7.2, style="F")
        self.set_xy(PAGE_W - MR - 16, 4.7)
        self.set_font(FONT_FAMILY, "B", 8)
        self.set_text_color(255, 255, 255)
        self.cell(16, 7, f"P{self.page_no()}", align="C")
        self.zigzag(16.2, 3.6)
        self.set_y(24)

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        self.set_y(-10)
        self.set_font(FONT_FAMILY, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, "FIC Agro  ·  Monitoreo Satelital Agrícola  ·  Universidad de Playa Ancha", align="C")

    def zigzag(self, y: float, h: float = 3.4) -> None:
        n = 24
        w = PAGE_W / n
        palette = [c[2] for c in NG_COLORS]
        for i in range(n):
            r, g, b = palette[i % len(palette)]
            self.set_fill_color(r, g, b)
            x = i * w
            self.polygon(((x, y + h), (x + w / 2, y), (x + w, y + h)), style="F")

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

    def section_bar(self, title: str) -> None:
        self._ensure(18)
        y = self.get_y()
        self.set_fill_color(*TEAL)
        self.rect(0, y, PAGE_W, 9, style="F")
        self.set_xy(ML, y + 1)
        self.set_font(FONT_FAMILY, "B", 11)
        self.set_text_color(255, 255, 255)
        self.cell(CONTENT_W, 7, title.upper())
        self.set_y(y + 13)

    def subsection(self, title: str) -> None:
        self._ensure(16)
        self.set_x(ML)
        self.set_font(FONT_FAMILY, "B", 12)
        self.set_text_color(*TEAL)
        self.cell(CONTENT_W, 7, title.upper(), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str) -> None:
        self.set_x(ML)
        self.set_font(FONT_FAMILY, "", 11)
        self.set_text_color(*INK)
        self.multi_cell(CONTENT_W, 6, text)
        self.ln(2.5)

    def bullet(self, text: str) -> None:
        self._ensure(10)
        y = self.get_y()
        self.set_xy(ML, y)
        self.set_font(FONT_FAMILY, "B", 11)
        self.set_text_color(*TEAL)
        self.cell(6, 6, "·")
        self.set_xy(ML + 6, y)
        self.set_font(FONT_FAMILY, "", 11)
        self.set_text_color(*INK)
        self.multi_cell(CONTENT_W - 6, 6, text)
        self.ln(1.2)

    def pantone_chip(
        self,
        x: float,
        y: float,
        w: float,
        name: str,
        hex_code: str,
        rgb: tuple[int, int, int],
        cmyk: tuple[int, int, int, int],
        usage: str,
        color_h: float = 34.0,
        label_h: float = 28.0,
    ) -> None:
        r, g, b = rgb
        total_h = color_h + label_h
        self.set_draw_color(210, 220, 224)
        self.set_line_width(0.25)
        self.set_fill_color(r, g, b)
        self.rect(x, y, w, color_h, style="F")
        self.set_fill_color(255, 255, 255)
        self.rect(x, y + color_h, w, label_h, style="F")
        self.rect(x, y, w, total_h, style="D")
        pad = 2.6
        tw = w - pad * 2
        self.set_xy(x + pad, y + color_h + 1.4)
        self.set_font(FONT_FAMILY, "B", 9)
        self.set_text_color(*NAVY)
        self.cell(tw, 4.2, self._fit(name, tw))
        self.set_xy(x + pad, y + color_h + 6.0)
        self.set_font(FONT_FAMILY, "B", 8)
        self.set_text_color(*TEAL)
        self.cell(tw, 3.8, hex_code.upper())
        self.set_xy(x + pad, y + color_h + 10.0)
        self.set_font(FONT_FAMILY, "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(tw, 3.4, f"RGB  {rgb[0]}  {rgb[1]}  {rgb[2]}")
        self.set_xy(x + pad, y + color_h + 13.6)
        c, m, yel, k = cmyk
        self.cell(tw, 3.4, f"CMYK  {c}  {m}  {yel}  {k}")
        self.set_xy(x + pad, y + color_h + 17.4)
        self.set_font(FONT_FAMILY, "I", 7)
        self.cell(tw, 3.4, self._fit(usage, tw))
        self.set_xy(x, y + total_h)

    def pantone_grid(self, colors: list, cols: int = 3) -> None:
        gap = 5.0
        chip_w = (CONTENT_W - gap * (cols - 1)) / cols
        color_h, label_h = 34.0, 26.0
        chip_h = color_h + label_h
        row_gap = 6.0
        n_rows = (len(colors) + cols - 1) // cols
        self._ensure(min(n_rows, 2) * (chip_h + row_gap) + 2)
        row_y = self.get_y()
        for i, item in enumerate(colors):
            name, hx, rgb, cmyk, usage = item
            col = i % cols
            if col == 0:
                self._ensure(chip_h + row_gap)
                row_y = self.get_y()
            x = ML + col * (chip_w + gap)
            self.pantone_chip(x, row_y, chip_w, name, hx, rgb, cmyk, usage, color_h, label_h)
            if col == cols - 1 or i == len(colors) - 1:
                self.set_y(row_y + chip_h + row_gap)

    def info_card(self, x: float, y: float, w: float, h: float, title: str, body: str) -> None:
        self.set_fill_color(245, 250, 251)
        self.set_draw_color(*TEAL_LIGHT)
        self.rect(x, y, w, h, style="DF")
        self.set_xy(x + 4, y + 3)
        self.set_font(FONT_FAMILY, "B", 10)
        self.set_text_color(*NAVY)
        self.cell(w - 8, 5, title)
        self.set_xy(x + 4, y + 10)
        self.set_font(FONT_FAMILY, "", 9)
        self.set_text_color(*INK)
        self.multi_cell(w - 8, 4.6, body)


def build_pdf() -> Path:
    pdf = FicNgPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False, margin=12)
    pdf.set_left_margin(ML)
    pdf.set_right_margin(MR)
    pdf.add_font(FONT_FAMILY, "", str(FONT_REGULAR))
    pdf.add_font(FONT_FAMILY, "B", str(FONT_BOLD))
    pdf.add_font(FONT_FAMILY, "I", str(FONT_ITALIC))

    # —— Portada ——
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, PAGE_W, 297, style="F")
    if LOGO_PATH.is_file():
        pdf.image(str(LOGO_PATH), x=75, y=28, w=60)
    pdf.set_xy(ML, 96)
    pdf.set_font(FONT_FAMILY, "B", 36)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(CONTENT_W / 2 - 4, 16, "FIC", align="R")
    pdf.set_text_color(*TEAL)
    pdf.cell(CONTENT_W / 2 - 4, 16, "Agro", align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "", 13)
    pdf.set_text_color(230, 240, 242)
    pdf.cell(CONTENT_W, 7, "Monitoreo Satelital Agrícola", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "", 11)
    pdf.set_text_color(*TEAL_LIGHT)
    pdf.cell(CONTENT_W, 6, "Adaptación al cambio climático", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "B", 16)
    pdf.set_text_color(*TEAL)
    pdf.cell(CONTENT_W, 8, "NORMAS GRÁFICAS", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "", 11)
    pdf.set_text_color(*TEAL_LIGHT)
    pdf.cell(CONTENT_W, 7, "Paleta oficial  ·  tipografía  ·  uso de marca", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.zigzag(152, 4.2)

    n = len(NG_COLORS)
    gap = 2.2
    chip_w = (CONTENT_W - gap * (n - 1)) / n
    y_chip = 162
    for i, (name, hx, rgb, _cmyk, _u) in enumerate(NG_COLORS):
        x = ML + i * (chip_w + gap)
        pdf.set_fill_color(*rgb)
        pdf.rect(x, y_chip, chip_w, 28, style="F")
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(x, y_chip + 28, chip_w, 16, style="F")
        pdf.set_xy(x + 0.8, y_chip + 29)
        pdf.set_font(FONT_FAMILY, "B", 5.5)
        pdf.set_text_color(*NAVY)
        pdf.cell(chip_w - 1.6, 4, pdf._fit(name, chip_w - 1.6))
        pdf.set_xy(x + 0.8, y_chip + 33.2)
        pdf.set_font(FONT_FAMILY, "", 5.2)
        pdf.set_text_color(*MUTED)
        pdf.cell(chip_w - 1.6, 4, hx.upper())
        pdf.set_xy(x + 0.8, y_chip + 37.2)
        pdf.cell(chip_w - 1.6, 4, f"{rgb[0]},{rgb[1]},{rgb[2]}")

    pdf.set_xy(ML, 226)
    pdf.set_font(FONT_FAMILY, "", 10)
    pdf.set_text_color(*TEAL_LIGHT)
    pdf.multi_cell(
        CONTENT_W,
        5.4,
        "Normas gráficas del Fondo de Innovación para la Competitividad (FIC).\n"
        "Colores oficiales RGB / CMYK / HEX  ·  tipografía DIN Pro.\n"
        "Universidad de Playa Ancha",
        align="C",
    )
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(0, 278, PAGE_W, 19, style="F")
    pdf.set_xy(ML, 283)
    pdf.set_font(FONT_FAMILY, "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(CONTENT_W, 8, "FIC Agro  ·  Monitoreo Satelital Agrícola  ·  UPLA")

    # —— Paleta ——
    pdf.add_page()
    pdf.section_bar("Paleta de colores")
    pdf.body_text(
        "Paleta oficial del proyecto FIC Agro. "
        "Cada ficha incluye HEX, RGB y CMYK. "
        "Usar estos valores en gráfica, papelería y piezas del FIC; "
        "no sustituirlos por aproximaciones de pantalla."
    )
    pdf.pantone_grid(NG_COLORS[:6], cols=3)

    pdf.add_page()
    pdf.subsection("Paleta oficial  ·  continuación")
    pdf.pantone_grid(NG_COLORS[6:], cols=2)
    pdf.ln(2)
    pdf.subsection("Uso")
    pdf.bullet("Azul marino + cian FIC: marca, titulares y fondos institucionales.")
    pdf.bullet("Verdes: medio ambiente, vegetación y campos.")
    pdf.bullet("Amarillos / oro / naranja / terracota: montañas, píxeles y acentos del isotipo.")
    pdf.bullet("Cian claro: agua, datos y fondos suaves.")

    # —— Tipografía ——
    pdf.add_page()
    pdf.section_bar("Tipografía")
    pdf.subsection("Tipografía usada: DIN Pro Regular + Bold")
    pdf.body_text(
        "El proyecto FIC usa DIN Pro Regular + Bold, alineada a la tipografía institucional UPLA. "
        "Referencia: https://www.upla.cl/normasgraficas/tipografia/"
    )
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "", 16)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(CONTENT_W, 8, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    pdf.set_x(ML)
    pdf.multi_cell(CONTENT_W, 8, "abcdefghijklmnopqrstuvwxyz")
    pdf.set_x(ML)
    pdf.multi_cell(CONTENT_W, 8, "1234567890")
    pdf.ln(2)
    pdf.set_x(ML)
    pdf.set_font(FONT_FAMILY, "B", 16)
    pdf.multi_cell(CONTENT_W, 8, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    pdf.set_x(ML)
    pdf.multi_cell(CONTENT_W, 8, "abcdefghijklmnopqrstuvwxyz")
    pdf.ln(4)

    gap = 5.0
    card_w = (CONTENT_W - gap) / 2
    y0 = pdf.get_y()
    pdf.info_card(
        ML, y0, card_w, 42,
        "DIN Pro Regular",
        "Texto general, altas y bajas.\nCuerpo, pies de foto, descripciones.",
    )
    pdf.info_card(
        ML + card_w + gap, y0, card_w, 42,
        "DIN Pro Bold",
        "Títulos y subtítulos.\nAltas o mayúsculas en barras de sección.",
    )
    pdf.set_y(y0 + 48)
    pdf.info_card(
        ML, pdf.get_y(), CONTENT_W, 38,
        "Otras fuentes UPLA (apoyo institucional)",
        "Gráfica: DIN Pro Light / Medium / Bold / Black.\n"
        "Papelería estándar: Verdana.\n"
        "Sitios web institucionales: Lato (todas las versiones).",
    )
    pdf.ln(4)
    pdf.body_text(
        "El explorador web del FIC hoy carga Source Sans 3 y Outfit (Google Fonts). "
        "Para piezas oficiales del FIC (informes, láminas, correos institucionales, material impreso) "
        "usar DIN Pro. En web institucional UPLA, Lato."
    )

    # —— Construcción ——
    pdf.add_page()
    pdf.section_bar("Construcción")
    pdf.subsection("Elementos de composición")
    pdf.body_text(
        "El isotipo se construye sobre una grilla circular. Desde esa grilla se desprenden "
        "la separación y los bordes de seguridad del logotipo."
    )
    elements = [
        ("Satélite", "Observación remota y captura de imágenes."),
        ("Dron", "Vuelos de campo y ortomosaicos de alta resolución."),
        ("Nube", "Almacenamiento de datos."),
        ("Píxeles", "Procesamiento de datos de imágenes."),
        ("Medio ambiente", "Montaña, río, araucaria, árbol, humedal."),
    ]
    gap = 4.0
    card_w = (CONTENT_W - 2 * gap) / 3
    row_y = pdf.get_y()
    for i, (title, body) in enumerate(elements):
        col = i % 3
        if col == 0 and i:
            row_y += 40
            pdf._ensure(42)
            row_y = pdf.get_y()
        x = ML + col * (card_w + gap)
        pdf.info_card(x, row_y, card_w, 36, title, body)
        if col == 2 or i == len(elements) - 1:
            pdf.set_y(row_y + 40)

    # —— Variaciones color ——
    pdf.add_page()
    pdf.section_bar("Variaciones")
    pdf.subsection("Variaciones de color")
    pdf.body_text(
        "Según el fondo, se elige la versión que mejor conserve legibilidad y visibilidad."
    )
    versions = [
        ("Color · positivo", "Fondos claros. FIC en azul marino, Agro en cian."),
        ("Color · negativo", "Fondos oscuros. FIC en blanco, Agro en cian."),
        ("Gris · positivo", "Fondos claros, una tinta (impresión B/N)."),
        ("Gris · negativo", "Fondos oscuros, una tinta (impresión B/N)."),
    ]
    gap = 5.0
    card_w = (CONTENT_W - gap) / 2
    card_h = 52.0
    for i, (title, body) in enumerate(versions):
        col = i % 2
        row = i // 2
        if col == 0:
            pdf._ensure(card_h + 8)
            row_y = pdf.get_y()
        x = ML + col * (card_w + gap)
        dark = "negativo" in title.lower()
        bg = NAVY if dark else (255, 255, 255)
        pdf.set_fill_color(*bg)
        pdf.set_draw_color(*TEAL)
        pdf.rect(x, row_y, card_w, card_h, style="DF")
        if LOGO_PATH.is_file():
            pdf.image(str(LOGO_PATH), x=x + 6, y=row_y + 6, w=22)
        pdf.set_xy(x + 32, row_y + 8)
        pdf.set_font(FONT_FAMILY, "B", 10)
        pdf.set_text_color(*TEAL if dark else NAVY)
        pdf.cell(card_w - 38, 5, title.upper())
        pdf.set_xy(x + 32, row_y + 16)
        pdf.set_font(FONT_FAMILY, "", 9)
        pdf.set_text_color(*(TEAL_LIGHT if dark else INK))
        pdf.multi_cell(card_w - 38, 4.6, body)
        if col == 1:
            pdf.set_y(row_y + card_h + 6)

    pdf.ln(2)
    pdf.subsection("Variaciones de orientación")
    pdf.bullet("Logo vertical: isotipo centrado sobre FIC Agro + Monitoreo Satelital Agrícola.")
    pdf.bullet("Logo horizontal: isotipo a la izquierda del wordmark. Elegir según el espacio.")

    # —— Aplicación FIC ——
    pdf.add_page()
    pdf.section_bar("Aplicación en el FIC")
    pdf.body_text(
        "Estas normas rigen la identidad visual del proyecto FIC Agro "
        "(Monitoreo Satelital Agrícola). El explorador web es una de las piezas; "
        "la paleta de 10 colores y DIN Pro aplican a toda la gráfica del FIC."
    )
    pdf.subsection("Qué usar en materiales del FIC")
    pdf.bullet("Colores: la paleta oficial de 10 valores (HEX + RGB + CMYK de este PDF).")
    pdf.bullet("Tipografía gráfica: DIN Pro Regular y Bold.")
    pdf.bullet("Wordmark: FIC en azul marino (o blanco sobre fondo oscuro) y Agro en cian.")
    pdf.bullet("Logo: versiones positivo / negativo y vertical / horizontal.")
    pdf.ln(2)
    pdf.subsection("Registro del sitio actual")
    pdf.bullet("El PDF PALETA_COLORES_TIPOGRAFIA.pdf documenta los verdes y fuentes que hoy usa el explorador.")
    pdf.bullet("Source Sans 3 y Outfit en index.html y explorador.html.")
    pdf.bullet("Colormaps científicos de índices (RdYlGn, Turbo, viridis, etc.).")
    pdf.ln(3)
    pdf.body_text("Proyecto FIC  ·  Universidad de Playa Ancha.")

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PDF.with_suffix(".tmp.pdf")
    pdf.output(str(tmp))
    try:
        tmp.replace(OUT_PDF)
        return OUT_PDF
    except PermissionError:
        fallback = OUT_PDF.with_name("FIC_NORMAS_GRAFICAS_v2.pdf")
        tmp.replace(fallback)
        print(f"AVISO: archivo abierto. Guardado como {fallback.name}")
        return fallback


if __name__ == "__main__":
    path = build_pdf()
    print(f"PDF generado: {path}")
    print(f"Original intacto: {REPO / 'documentación' / 'PALETA_COLORES_TIPOGRAFIA.pdf'}")
