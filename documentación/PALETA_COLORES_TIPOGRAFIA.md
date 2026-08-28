# Guía de identidad visual — Monitoreo Agrícola FIC Agro

**Fuentes en código:** `index.html`, `explorador.html`, `config.yaml`, `data_static/sources_manifest.json`  
**Versión:** según implementación actual del sitio web

> **PDF con muestras de color:** [`PALETA_COLORES_TIPOGRAFIA.pdf`](PALETA_COLORES_TIPOGRAFIA.pdf)  
> Regenerar con: `python scripts/data_prep/build_paleta_pdf.py`

> **Pantone:** las equivalencias son aproximadas (conversión HEX/RGB → Pantone Coated). En impresión conviene validar con muestrario físico; en pantalla usar siempre los códigos HEX.  
> **Tipografía web:** cargada desde [Google Fonts](https://fonts.google.com).

---

## 1. Tipografía

### 1.1 Familias tipográficas

| Rol | Familia | Pesos cargados | Fallback | Uso principal |
|-----|---------|----------------|----------|---------------|
| **Texto / UI** | **Source Sans 3** | 400, 600, 700 + *cursiva* 400 | `system-ui`, `sans-serif` | Cuerpo, formularios, popups, selects, ayudas |
| **Display / títulos** | **Outfit** | 500, 600, 700, 800 | `sans-serif` | Títulos, botones, etiquetas en mayúsculas, gráficos |
| **Monoespaciada** | **IBM Plex Mono** *(referenciada, no cargada)* | — | `Consolas`, `monospace` | Valores numéricos en escalas de índices |

**Carga en HTML** (ambas páginas):

```
https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&family=Outfit:wght@500;600;700;800&display=swap
```

**Nota:** `IBM Plex Mono` aparece en el CSS del explorador pero **no** se importa en el `<head>`. En la práctica se usa `Consolas` o la monospace del sistema. Si se requiere esa fuente de forma consistente, habría que añadirla a Google Fonts.

---

### 1.2 Jerarquía tipográfica

#### Login (`index.html`)

| Elemento | Fuente | Tamaño | Peso | Interletraje | Otros |
|----------|--------|--------|------|--------------|-------|
| **Body** | Source Sans 3 | `clamp(15px, calc(0.25vw + 14px), 16px)` | 400 | normal | `line-height: 1.55` |
| **Título marca** (h1) | Outfit | `clamp(1.05rem, 2.5vw, 1.2rem)` | **800** | `-0.02em` | — |
| **Subtítulo marca** | Source Sans 3 | `0.86rem` (~13.8 px) | 400 | normal | color muted |
| **Eyebrow** (“Acceso”) | Outfit | `0.72rem` (~11.5 px) | **700** | `0.12em` | `uppercase`, color acento |
| **Lead / intro** | Source Sans 3 | `0.92rem` (~14.7 px) | 400 | normal | color muted |
| **Labels campos** | Source Sans 3 | `0.82rem` (~13.1 px) | **700** | normal | color muted |
| **Inputs** | Source Sans 3 (inherit) | inherit del body | 400 | normal | — |
| **Botón principal** | Outfit | `0.98rem` (~15.7 px) | **700** | normal | color blanco |
| **Error** | Source Sans 3 | `0.88rem` | 400 | normal | color peligro |
| **Hint pie** | Source Sans 3 | `0.78rem` | 400 | normal | centrado, muted |

#### Explorador — panel, índices y controles

| Elemento / clase | Fuente | Tamaño | Peso | Interletraje | Otros |
|------------------|--------|--------|------|--------------|-------|
| `.fic-hier-badge-wrap` | Source Sans 3 | `0.88rem` | 400 / **700** strong | normal | color muted / ink |
| `.btn-ghost` | Source Sans 3 | `0.86rem` | **600** | normal | botón secundario |
| `.fic-origin-picker__label` | Source Sans 3 | `0.74rem` | **700** | `0.01em` | selector origen |
| `.fic-source-pills-label` | Outfit | `0.72rem` | **700** | `0.06em` | `uppercase` |
| `.fic-source-pill` | Source Sans 3 | `0.84rem` | **600** | normal | pills S2 / dron |
| `.selector-label` | Source Sans 3 | `0.68rem` | **700** | `0.065em` | `uppercase` |
| `.fic-index-desc__label` | Outfit | `0.66rem` | **700** | `0.09em` | `uppercase` |
| `.fic-index-desc__scale-head` | Source Sans 3 | `0.62rem` | **700** | `0.07em` | `uppercase` |
| `.fic-index-desc__row dt` | Source Sans 3 | `0.62rem` | **700** | `0.06em` | `uppercase` |
| `.fic-sidebar-group__eyebrow` | Outfit | `0.66rem` | **700** | `0.08em` | `uppercase` |
| `.fic-sat-mode-pill` | Source Sans 3 | `0.78rem` | **700** | `0.04em` | `uppercase` |

#### Explorador — mapa, leyendas y gráficos

| Elemento / clase | Fuente | Tamaño | Peso | Interletraje | Otros |
|------------------|--------|--------|------|--------------|-------|
| `.fic-sat-compare-yrs__k` | Source Sans 3 | `0.72rem` | **800** | `0.02em` | etiqueta año |
| `.colorbar-title` | Source Sans 3 | `0.72rem` | **700** | normal | leyenda índice |
| `.colorbar-scale span` | Source Sans 3 | `0.74rem` | **700** | normal | vmin / vmax |
| `.fic-sat-chart-bandlabel` | Source Sans 3 | `0.7rem` | **700** | `0.04em` | `uppercase` |
| `.fic-sat-chart-bandsel` | Source Sans 3 | `0.78rem` | **600** | normal | selector banda |
| `.fic-sat-chart-toggle` | Source Sans 3 | `0.78rem` | **700** | normal | abrir gráfico |
| `.fic-sat-period-custom-slider__thumb` | Source Sans 3 | `0.72rem` | **800** | `-0.02em` | pill temporal |
| `.fic-drone-opacity .selector-label` | Source Sans 3 | `0.68rem` | **700** | `0.065em` | opacidad dron |
| `.las3d-hint` | Source Sans 3 | `0.72rem` | 400 | normal | hint vista 3D |

#### Explorador (`explorador.html`) — resumen principal

| Elemento | Fuente | Tamaño | Peso | Interletraje | Otros |
|----------|--------|--------|------|--------------|-------|
| **Body** | Source Sans 3 | `clamp(15.5px, calc(0.38vw + 14px), 17px)` | 400 | normal | `line-height: 1.5` |
| **Kicker topbar** | Outfit | `0.65rem` (~10.4 px) | **700** | `0.15em` | `uppercase` |
| **Título topbar** | Outfit | `clamp(1.08rem … 1.32rem)` | **800** | `-0.038em` | gradiente verde |
| **Título mapa** (`.fic-map-title`) | Source Sans 3 | `clamp(1.02rem … 1.2rem)` | **500** | `0.01em` | `line-height: 1.38` |
| **Año comparador** (valor) | Outfit | `1.1rem` | **800** | `-0.02em` | — |
| **Eyebrows sidebar** | Outfit | `0.66–0.72rem` | **700** | `0.06–0.08em` | `uppercase` |
| **Labels selectores** | Source Sans 3 | `0.68rem` | **700** | `0.065em` | `uppercase` |
| **Pills fuente / origen** | Source Sans 3 | `0.74–0.84rem` | **600–700** | `0.01em` | — |
| **Selects / inputs panel** | Source Sans 3 | `0.9rem` | **600** | normal | `line-height: 1.35` |
| **Texto ayuda** (`.helper`) | Source Sans 3 | `0.9rem` | 400 | normal | `line-height: 1.45` |
| **Descripción índice** | Source Sans 3 | `0.78rem` | 400 | normal | `line-height: 1.5` |
| **Escala numérica índice** | IBM Plex Mono → Consolas | `0.8rem` | **600** | normal | solo valores min/max |
| **Título gráfico** | Outfit | `0.88rem` | **800** | `-0.01em` | — |
| **Subtítulo gráfico** | Source Sans 3 | `0.72rem` | 400 | normal | color muted |
| **Pills gráfico** | Source Sans 3 | `0.72rem` | **700** | normal | — |
| **Popup cuartel** | Source Sans 3 | `13px` / título `14px` | 400 / **700** | normal | `line-height: 1.55` |
| **Controles zoom mapa** | Source Sans 3 (inherit) | `18px` | normal | — | Leaflet |
| **Leyenda mapa** | Source Sans 3 | `0.86rem` | 400 | normal | `line-height: 1.4` |

---

### 1.3 Reglas de uso tipográfico

| Regla | Detalle |
|-------|---------|
| **Títulos y marca** | Siempre **Outfit** en pesos 700–800 |
| **Todo lo demás** | **Source Sans 3** (legibilidad en UI densa) |
| **Etiquetas de sección** | Outfit o Source Sans en **mayúsculas** + `letter-spacing` amplio (0.06–0.15em) |
| **Títulos display** | Tracking negativo (`-0.02em` a `-0.038em`) para look moderno |
| **Números técnicos** | Monoespaciada en escalas de índices (vmin/vmax) |
| **Responsive** | Tamaños base con `clamp()`; unidad `rem` relativa al body |
| **Idioma** | `lang="es"`; contenido en español |

---

### 1.4 Especificación para diseño / impresión

| Uso | Fuente recomendada | Alternativa impresión |
|-----|-------------------|------------------------|
| Titulares | **Outfit** Bold / ExtraBold | **Montserrat** o **Gotham** (similar geométrica) |
| Cuerpo | **Source Sans 3** Regular / Semibold | **Source Sans Pro** o **Open Sans** |
| Datos / código | **IBM Plex Mono** Medium | **Consolas** o **Roboto Mono** |

**Licencia:** ambas familias en Google Fonts son de uso libre (SIL Open Font License).

---

## 2. Paleta de colores

### 2.1 Logo FIC TeleAmb (`assets/img/Logo_FIC_Teleamb.png`)

Colores dominantes extraídos del logo oficial (500×500 px). El logo usa **azules/teales y dorados**; la UI web complementa con **verdes institucionales**. Ambas paletas conviven en la marca.

| Nombre | HEX | RGB | Pantone aprox. | Uso en el logo |
|--------|-----|-----|----------------|----------------|
| Azul marino | `#003860` | 0, 56, 96 | **3025 C** | Satélite, dron, árboles, surcos |
| Azul petróleo | `#186870` | 24, 104, 112 | **316 C** | Mosaico digital, sombras |
| Cian / turquesa | `#90D0D8` | 144, 208, 216 | **2905 C** | Cielo pixelado (datos) |
| Dorado montaña | `#D89820` | 216, 152, 32 | **7555 C** | Montañas, acentos cálidos |
| Amarillo cultivo | `#E8E080` | 232, 224, 128 | **600 C** | Suelo / cultivo iluminado |
| Verde cultivo | `#70B878` | 112, 184, 120 | **7488 C** | Vegetación en el campo |

### 2.2 Identidad UI (explorador / login)

Variables CSS del explorador (`explorador.html`):

| Nombre | Token | HEX | RGB | Pantone aprox. | Uso |
|--------|-------|-----|-----|----------------|-----|
| Verde oscuro | `--fic-ag-dark` | `#0F2418` | 15, 36, 24 | **5535 C** | Títulos, gradientes, hover |
| Verde principal | `--fic-ag-mid` | `#1D6B4A` | 29, 107, 74 | **342 C** | Botones, acentos, borde activo |
| Verde acento | `--fic-ag-accent` | `#2D9D6E` | 45, 157, 110 | **3395 C** | Focus, hover, pills activas |
| Tinta | `--fic-ag-ink` | `#122018` | 18, 32, 24 | **5535 C** | Texto principal |
| Texto secundario | `--fic-ag-muted` | `#4A5C4E` | 74, 92, 78 | **5605 C** | Labels, ayudas |
| Verde gradiente | — | `#145A3A` | 20, 90, 58 | **3425 C** | Fin de gradiente título |
| Verde login (oscuro) | — | `#0D4D35` | 13, 77, 53 | **3435 C** | Botón login (`index.html`) |

**Referencia visual:**

```
█ #0F2418  Verde bosque oscuro
█ #122018  Tinta
█ #1D6B4A  Verde principal  ← color institucional
█ #2D9D6E  Verde acento
█ #145A3A  Verde medio-oscuro
```

---

### 2.3 Pantalla de login (`index.html`)

| Nombre | Token | HEX | RGB | Pantone aprox. |
|--------|-------|-----|-----|----------------|
| Fondo | `--fic-bg` | `#EEF4ED` | 238, 244, 237 | **9060 C** |
| Superficie | `--fic-surface` | `#FFFFFF` | 255, 255, 255 | White |
| Borde | `--fic-border` | `#C5D4C0` | 197, 212, 192 | **558 C** |
| Texto | `--fic-text` | `#1A2E1F` | 26, 46, 31 | **560 C** |
| Acento | `--fic-accent` | `#1D6B4A` | 29, 107, 74 | **342 C** |
| Peligro | `--fic-danger` | `#7A1E1E` | 122, 30, 30 | **7427 C** |

---

### 2.4 Fondos y paneles (explorador)

| Nombre | HEX | RGB | Pantone aprox. | Uso |
|--------|-----|-----|----------------|-----|
| Fondo body | `#0F1512` | 15, 21, 18 | **Black 7 C** | Detrás del mapa |
| Fondo mapa | `#0D120F` | 13, 18, 15 | **Black 7 C** | Leaflet, split view |
| Vista 3D LAS | `#0C1210` | 12, 18, 16 | **Black 7 C** | Contenedor nube de puntos |
| Panel claro (inicio) | `#FBFDF9` | 251, 253, 249 | **9061 C** | Gradiente panel |
| Panel claro (medio) | `#F3F8F3` | 243, 248, 243 | **9060 C** | Gradiente panel |
| Panel claro (fin) | `#EBF3EC` | 235, 243, 236 | **9041 C** | Gradiente panel |
| Texto popup | `#1A2E24` | 26, 46, 36 | **560 C** | Título popup cuartel |
| Etiqueta popup | `#6B7F72` | 107, 127, 114 | **5645 C** | Labels cuartel |
| Texto énfasis panel | `#1A3D2C` | 26, 61, 44 | **343 C** | Valores escala índice |

---

### 2.5 Colores semánticos (índices y alertas)

| Nombre | Token | HEX | RGB | Pantone aprox. | Uso |
|--------|-------|-----|-----|----------------|-----|
| NDVI | `--fic-ndvi` | `#2D8A4E` | 45, 138, 78 | **347 C** | Íconos / acentos vegetación |
| NDWI | `--fic-ndwi` | `#2B6CB0` | 43, 108, 176 | **7689 C** | Íconos / acentos agua |
| Advertencia | `--fic-warn` | `#C4A035` | 196, 160, 53 | **7405 C** | Avisos |
| Error panel | — | `#7A1E1E` | 122, 30, 30 | **7427 C** | Mensajes de error |
| Serie gráfico (contraste) | — | `#C45035` | 196, 80, 53 | **7586 C** | Línea secundaria en charts |

---

### 2.6 Colores por fuente de datos

| Fuente | Dónde se define | HEX en UI activa | RGB | Pantone aprox. |
|--------|-----------------|------------------|-----|----------------|
| **Sentinel-2** | `sources_manifest.json` / metadata | `#1D6B4A` | 29, 107, 74 | **342 C** |
| **Dron** | `sources_manifest.json` / `config.yaml` | `#22C55E` | 34, 197, 94 | **2270 C** |
| **Sentinel-2 (config)** | `config.yaml` (no usado en manifiesto actual) | `#38BDF8` | 56, 189, 248 | **2915 C** |
| **Sentinel-1 (config)** | `config.yaml` (planificado) | `#0F766E` | 15, 118, 110 | **3285 C** |

> En la UI publicada, Sentinel-2 usa el verde `#1D6B4A` (alineado con la marca), no el azul `#38BDF8` del `config.yaml`.

---

### 2.7 Gráficos temporales (Chart.js)

| Elemento | Color | HEX / RGBA |
|----------|-------|------------|
| Serie histórica (relleno) | Verde semitransparente | `rgba(29, 107, 74, 0.18)` |
| Serie actual (línea) | Verde sólido | `rgba(29, 107, 74, 0.92)` ≈ `#1D6B4A` |
| Serie contraste / año actual | Naranja terracota | `#C45035` |
| Borde serie dinámica | Color de la fuente activa | p. ej. `#1D6B4A` o `#22C55E` |

---

### 2.8 Colormaps por índice (mapas y leyendas)

Paletas científicas (ColorBrewer / matplotlib) usadas en el explorador. La leyenda va de **vmin (abajo)** a **vmax (arriba)**.

#### Sentinel-2 (`build_sentinel2_local.py` → `BAND_VIZ`)

| Índice | Colormap | vmin | vmax | Descripción |
|--------|----------|------|------|-------------|
| **NDVI** | RdYlGn | -1 | 1 | Índice de vegetación |
| **NDMI** | RdYlBu | -1 | 1 | Humedad de canopy |
| **MNDWI** | Blues | -1 | 1 | Agua en superficie |
| **REDEDGE_POSITION** | viridis | 700 | 750 | Posición red edge (nm) |
| **MCARI** | YlGn | 0 | 0.1 | Clorofila ajustada |
| **GNDVI** | YlGn | -1 | 1 | Vegetación verde |
| **MSAVI** | YlGn | -1 | 1 | Vegetación ajustada al suelo |
| **EVI** | YlGn | -1 | 1 | Vegetación mejorada |
| **PSRI** | RdYlGn_r | -0.5 | 0.5 | Reflectancia estructural (invertido) |

#### Dron (`config.yaml` / `metadata.json`)

| Índice | Colormap | vmin | vmax | Descripción |
|--------|----------|------|------|-------------|
| **NDVI** | RdYlGn | -1 | 1 | Índice de vegetación |
| **NDWI** | RdYlBu | -1 | 1 | Índice de humedad |
| **NDCI** | RdYlGn | -1 | 1 | Clorofila (borde rojo) |
| **Térmica** | Turbo | P0 | P98 | Ortomosaico térmico (percentiles) |
| **RGB** | — | — | — | Color verdadero (sin colormap) |

#### Gradientes de referencia (explorador.html → `FIC_CMAP`)

**RdYlGn** (NDVI, NDCI):

`#A50026` → `#D73027` → `#F46D43` → `#FDAE61` → `#FEE08B` → `#FFFFBF` → `#D9EF8B` → `#A6D96A` → `#66BD63` → `#1A9850` → `#006837`

**RdYlBu** (NDMI, NDWI dron):

`#A50026` … → `#E0F3F8` → `#ABD9E9` → `#74ADD1` → `#4575B4` → `#313695`

**YlGn** (MCARI, GNDVI, MSAVI, EVI):

`#FFFFCC` → `#D9F0A3` → `#ADDD8E` → `#78C679` → `#41AB5D` → `#238443` → `#005A32`

**Blues** (MNDWI):

`#F7FBFF` → `#DEEBF7` → `#C6DBEF` → `#9ECAE1` → `#6BAED6` → `#3182BD` → `#08519C` → `#08306B`

**viridis** (red edge):

`#440154` → `#482575` → `#414487` → `#355F8D` → `#2A788E` → `#21908D` → `#22A884` → `#44BF70` → `#7AD151` → `#BDD26C` → `#FDE725`

**Turbo** (térmica dron):

`#300B3B` → `#462A7A` → `#365B8F` → `#277F8E` → `#1FA187` → `#4AC16D` → `#AADC32` → `#FDE725` → `#FCA636` → `#D4522A` → `#7A0403`

**RdYlGn_r** (PSRI): mismo gradiente que RdYlGn pero **invertido** (verde abajo, rojo arriba).

**RdPu** (definido en UI, no usado actualmente):

`#FFF7F3` … `#49006A`

---

## 3. Resumen ejecutivo

| Elemento | Especificación |
|----------|----------------|
| **Color primario** | `#1D6B4A` · Pantone **342 C** |
| **Color acento** | `#2D9D6E` · Pantone **3395 C** |
| **Color oscuro institucional** | `#0F2418` · Pantone **5535 C** |
| **Tipografía títulos** | **Outfit** 700–800 |
| **Tipografía cuerpo** | **Source Sans 3** 400–700 |
| **Estilo labels** | Mayúsculas + tracking amplio |
| **Fondo claro** | `#EEF4ED` / `#FBFDF9` |
| **Fondo mapa** | `#0D120F` |
| **Dron (fuente)** | `#22C55E` · Pantone **2270 C** |
| **Agua (NDWI UI)** | `#2B6CB0` · Pantone **7689 C** |
| **Error** | `#7A1E1E` · Pantone **7427 C** |
| **Advertencia** | `#C4A035` · Pantone **7405 C** |

---

## 4. Recomendaciones de uso

1. **Marca:** priorizar `#1D6B4A` + `#2D9D6E` sobre fondos claros (`#EEF4ED` / `#FBFDF9`) o oscuros (`#0D120F`).
2. **Contraste:** texto principal `#122018` o `#1A2E1F`; secundario `#4A5C4E`.
3. **Tipografía:** Outfit solo para titulares y CTAs; Source Sans 3 para el resto de la interfaz.
4. **Impresión:** pedir **Pantone Coated (C)**; si es papel no couché, revisar equivalente **U**.
5. **Digital:** usar siempre HEX; no confiar solo en Pantone para web.

---

*Documento generado a partir del código fuente del repositorio FIC Agro.*
