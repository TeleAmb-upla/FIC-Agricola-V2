/**
 * =============================================================================
 * MOSAICOS SEMANALES SENTINEL-2 → EARTH ENGINE ASSETS
 * =============================================================================
 *
 * ¿Qué hace este script?
 *   1. Toma UN polígono (predio) de tu tabla en Assets.
 *   2. Descarga mentalmente todas las escenas S2 SR de los años configurados.
 *   3. Por cada escena: quita nubes (Cloud Score+), calcula ~28 índices + biomasa SNAP.
 *   4. Agrupa por semana calendario y hace la MEDIANA de cada índice.
 *   5. Exporta cada semana como imagen en Assets: …/Y2024_W03, Y2024_W04, etc.
 *
 * Cómo usarlo:
 *   - Edita CONFIG (predio, años, carpeta de salida).
 *   - Ejecuta en https://code.earthengine.google.com/
 *   - Revisa la pestaña "Tasks" hasta que terminen las exportaciones.
 *
 * Equivalente Python: scripts/gee/export_s2.py (sin exportación a Google Drive).
 */

// =============================================================================
// CONFIGURACIÓN — único bloque que debes editar con frecuencia
// =============================================================================
var CONFIG = {
  // Tabla vectorial en Assets que contiene todos los predios
  featureCollection: 'projects/teleambagr/assets/vectores/Area_Agricola_Reg_Valpo_2025',
  // Identificador del polígono dentro de esa tabla (columna wetland_id)
  featureId: 'g3',
  // Ruta donde se guardarán las imágenes semanales (ImageCollection en Assets)
  exportPrefix: 'projects/teleambagr/assets/S2_weekly_custom',
  // Primer y último año civil a procesar
  startYear: 2024,
  endYear: 2025,
  // Tamaño de píxel al exportar (10 m = resolución nativa de varias bandas S2)
  scale: 10,
  // Píxeles con Cloud Score+ (cs) menor a este valor se consideran nube y se ocultan
  cloudThreshold: 0.6,
};

/**
 * Bandas que tendrá cada mosaico semanal exportado.
 * Son índices derivados, NO las bandas espectrales crudas del satélite (B1, B2, …).
 * clear_pixel_count se añade aparte en el mosaico (cuenta escenas válidas por píxel).
 */
var INDEX_BANDS = [
  'NDVI', 'NDMI', 'NDWI', 'MNDWI', 'GNDVI',           // vegetación / agua
  'EVI', 'SAVI', 'MSAVI',                             // vegetación (fórmulas)
  'ARI', 'MARI', 'ARVI', 'CHL_REDEDGE', 'REDEDGE_POSITION',  // pigmentos / red edge
  'EVI2', 'kNDVI', 'MCARI', 'MSI',                    // vegetación / humedad
  'NDMISTRESS', 'NDII', 'NDCI', 'PSSRB1', 'SIPI1', 'PSRI',   // estrés / clorofila
  'LAI', 'LEAF_CHL', 'CANOPY_CHL', 'FAPAR', 'FCOVER',  // SNAP (biofísicas)
];

// Factor para convertir ángulos de grados a radianes (usado en SNAP)
var DEG = ee.Image(Math.PI).divide(180);

// =============================================================================
// REDES NEURONALES SNAP (biofísicas)
// Portado desde export_s2.py — coeficientes fijos entrenados en SNAP/Sentinel Hub.
// Calculan LAI, clorofila foliar, FAPAR y cobertura fraccional por píxel.
// =============================================================================

/**
 * Lee un número de las propiedades de la escena S2 (metadatos).
 * Si la propiedad no existe (p. ej. ángulo), usa el valor por defecto d.
 */
function propNum(img, k, d) {
  var names = ee.List(img.propertyNames());
  var has = names.contains(k);
  return ee.Number(ee.Algorithms.If(has, img.get(k), ee.Number(d)));
}

/** Crea una imagen constante con la misma proyección/tamaño que ref. */
function constLike(ref, n) {
  return ref.multiply(0).add(n);
}

/** Función de activación tangente hiperbólica de la red neuronal. */
function tansig(x) {
  return ee.Image(2).divide(ee.Image(1).add(x.multiply(-2).exp())).subtract(1);
}

/** Normaliza una banda de reflectancia al rango [-1, 1] (entrada de la red). */
function normRefl(b, lo, hi) {
  return b.subtract(lo).multiply(2).divide(ee.Image.constant(hi - lo)).subtract(1);
}

/** Normaliza un escalar espacial (p. ej. coseno del ángulo zenital). */
function normSc(x, lo, hi) {
  return x.subtract(lo).multiply(2).divide(ee.Image.constant(hi - lo)).subtract(1);
}

/** Convierte salida de la red [-1,1] de vuelta a unidades físicas (LAI, etc.). */
function denorm(x, lo, hi) {
  return x.add(1).multiply(0.5).multiply(hi - lo).add(lo);
}

/**
 * Una neurona de la capa oculta: suma ponderada de entradas + bias, luego tansig.
 * c[0] = bias; c[1..] = pesos de cada entrada en z.
 */
function neuron(c, z) {
  var s = ee.Image.constant(c[0]);
  for (var i = 1; i < c.length; i++) {
    s = s.add(z[i - 1].multiply(c[i]));
  }
  return tansig(s);
}

/**
 * Capa de salida lineal: combina las neuronas ocultas sin activación final
 * (la desnormalización va después con denorm).
 */
function layer(c, n) {
  var s = ee.Image.constant(c[0]);
  for (var j = 1; j < c.length; j++) {
    s = s.add(n[j - 1].multiply(c[j]));
  }
  return s;
}

// Pesos de las redes (no editar): capa oculta (*N) y capa salida (*L2) por variable
var SNAP = {
  laiN: [[4.96238030555279,-0.023406878966470,0.921655164636366,0.135576544080099,-1.938331472397950,-3.342495816122680,0.902277648009576,0.205363538258614,-0.040607844721716,-0.083196409727092,0.260029270773809,0.284761567218845],[1.416008443981500,-0.132555480856684,-0.139574837333540,-1.014606016898920,-1.330890038649270,0.031730624503341,-1.433583541317050,-0.959637898574699,1.133115706551000,0.216603876541632,0.410652303762839,0.064760155543506],[1.075897047213310,0.086015977724868,0.616648776881434,0.678003876446556,0.141102398644968,-0.096682206883546,-1.128832638862200,0.302189102741375,0.434494937299725,-0.021903699490589,-0.228492476802263,-0.039460537589826],[1.533988264655420,-0.109366593670404,-0.071046262972729,0.064582411478320,2.906325236823160,-0.673873108979163,-3.838051868280840,1.695979344531530,0.046950296081713,-0.049709652688365,0.021829545430994,0.057483827104091],[3.024115930757230,-0.089939416159969,0.175395483106147,-0.081847329172620,2.219895367487790,1.713873975136850,0.713069186099534,0.138970813499201,-0.060771761518025,0.124263341255473,0.210086140404351,-0.183878138700341]],
  laiL2: [1.096963107077220,-1.500135489728730,-0.096283269121503,-0.194935930577094,-0.352305895755591,0.075107415847473],
  cabN: [[4.242299670155190,0.400396555256580,0.607936279259404,0.137468650780226,-2.955866573461640,-3.186746687729570,2.206800751246430,-0.313784336139636,0.256063547510639,-0.071613219805105,0.510113504210111,0.142813982138661],[-0.259569088225796,-0.250781102414872,0.439086302920381,-1.160590937522300,-1.861935250269610,0.981359868451638,1.634230834254840,-0.872527934645577,0.448240475035072,0.037078083501217,0.030044189670404,0.005956686619403],[3.130392627338360,0.552080132568747,-0.502919673166901,6.105041924966230,-1.294386119140800,-1.059956388352800,-1.394092902418820,0.324752732710706,-1.758871822827680,-0.036663679860328,-0.183105291400739,-0.038145312117381],[0.774423577181620,0.211591184882422,-0.248788896074327,0.887151598039092,1.143675895571410,-0.753968830338323,-1.185456953076760,0.541897860471577,-0.252685834607768,-0.023414901078143,-0.046022503549557,-0.006570284080657],[2.584276648534610,0.254790234231378,-0.724968611431065,0.731872806026834,2.303453821021270,-0.849907966921912,-6.425315500537270,2.238844558459030,-0.199937574297990,0.097303331714567,0.334528254938326,0.113075306591838]],
  cabL2: [0.463426463933822,-0.352760040599190,-0.603407399151276,0.135099379384275,-1.735673123851930,-0.147546813318256],
  fapN: [[-0.887068364040280,0.268714454733421,-0.205473108029835,0.281765694196018,1.337443412255980,0.390319212938497,-3.612714342203350,0.222530960987244,0.821790549667255,-0.093664567310731,0.019290146147447,0.037364446377188],[0.320126471197199,-0.248998054599707,-0.571461305473124,-0.369957603466673,0.246031694650909,0.332536215252841,0.438269896208887,0.819000551890450,-0.934931499059310,0.082716247651866,-0.286978634108328,-0.035890968351662],[0.610523702500117,-0.164063575315880,-0.126303285737763,-0.253670784366822,-0.321162835049381,0.067082287973580,2.029832288655260,-0.023141228827722,-0.553176625657559,0.059285451897783,-0.034334454541432,-0.031776704097009],[-0.379156190833946,0.130240753003835,0.236781035723321,0.131811664093253,-0.250181799267664,-0.011364149953286,-1.857573214633520,-0.146860751013916,0.528008831372352,-0.046230769098303,-0.034509608392235,0.031884395036004],[1.353023396690570,-0.029929946166941,0.795804414040809,0.348025317624568,0.943567007518504,-0.276341670431501,-2.946594180142590,0.289483073507500,1.044006950440180,-0.000413031960419,0.403331114840215,0.068427130526696]],
  fapL2: [-0.336431283973339,2.126038811064490,-0.632044932794919,5.598995787206250,1.770444140578970,-0.267879583604849],
  fcvN: [[-1.45261652206,-0.156854264841,0.124234528462,0.235625516229,-1.8323910258,-0.217188969888,5.06933958064,-0.887578008155,-1.0808468167,-0.0323167041864,-0.224476137359,-0.195523962947],[-1.70417477557,-0.220824927842,1.28595395487,0.703139486363,-1.34481216665,-1.96881267559,-1.45444681639,1.02737560043,-0.12494641532,0.0802762437265,-0.198705918577,0.108527100527],[1.02168965849,-0.409688743281,1.08858884766,0.36284522554,0.0369390509705,-0.348012590003,-2.0035261881,0.0410357601757,1.22373853174,-0.0124082778287,-0.282223364524,0.0994993117557],[-0.498002810205,-0.188970957866,-0.0358621840833,0.00551248528107,1.35391570802,-0.739689896116,-2.21719530107,0.313216124198,1.5020168915,1.21530490195,-0.421938358618,1.48852484547],[-3.88922154789,2.49293993709,-4.40511331388,-1.91062012624,-0.703174115575,-0.215104721138,-0.972151494818,-0.930752241278,1.2143441876,-0.521665460192,-0.445755955598,0.344111873777]],
  fcvL2: [-0.0967998147811,0.23080586765,-0.333655484884,-0.499418292325,0.0472484396749,-0.0798516540739],
};

/**
 * Prepara el vector de 11 entradas que alimentan todas las redes SNAP:
 * - 8 bandas S2 normalizadas (B3, B4, B5, B6, B7, B8A, B11, B12)
 * - 3 variables de geometría solar / de observación (cosenos de ángulos)
 */
function snapInputs(img) {
  var ref = img.select('B4');  // banda de referencia solo para dimensiones del raster
  // Ángulo zenital de incidencia del sensor (grados → radianes)
  var vz = constLike(ref, propNum(img, 'MEAN_INCIDENCE_ZENITH_ANGLE_B8', 10)).multiply(DEG);
  // Ángulo zenital solar
  var sz = constLike(ref, propNum(img, 'MEAN_SOLAR_ZENITH_ANGLE', 45)).multiply(DEG);
  // Ángulo relativo azimut sol–sensor
  var rel = constLike(ref, propNum(img, 'MEAN_SOLAR_AZIMUTH_ANGLE', 135)
    .subtract(propNum(img, 'MEAN_INCIDENCE_AZIMUTH_ANGLE_B8', 180))).multiply(DEG);
  return [
    normRefl(img.select('B3'), 0, 0.253061520471542),       // verde
    normRefl(img.select('B4'), 0, 0.290393577911328),       // rojo
    normRefl(img.select('B5'), 0, 0.305398915248555),       // red edge 1
    normRefl(img.select('B6'), 0.006637972542253, 0.608900395797889),
    normRefl(img.select('B7'), 0.013972727018939, 0.753827384322927),
    normRefl(img.select('B8A'), 0.026690138082061, 0.782011770669178),  // NIR estrecho
    normRefl(img.select('B11'), 0.016388074192258, 0.493761397883092), // SWIR1
    normRefl(img.select('B12'), 0, 0.493025984460231),                 // SWIR2
    normSc(vz.cos(), 0.918595400582046, 1),   // geometría de vista
    normSc(sz.cos(), 0.342022871159208, 0.936206429175402),  // geometría solar
    rel.cos(),
  ];
}

/**
 * Ejecuta las 4 redes SNAP y devuelve las 5 bandas biofísicas por escena.
 */
function snapBands(img) {
  var z = snapInputs(img);
  // LAI = índice de área foliar
  var lai = denorm(layer(SNAP.laiL2, SNAP.laiN.map(function(c) { return neuron(c, z); })),
    0.000319182538301, 14.4675094548151).rename('LAI');
  // Clorofila en la hoja (Cab)
  var cab = denorm(layer(SNAP.cabL2, SNAP.cabN.map(function(c) { return neuron(c, z); })),
    0.007426692959872, 873.908222110306).rename('LEAF_CHL');
  // FAPAR = fracción de radiación fotosintética absorbida
  var fapar = denorm(layer(SNAP.fapL2, SNAP.fapN.map(function(c) { return neuron(c, z); })),
    0.000153013463222, 0.977135096979553).rename('FAPAR');
  // FCOVER = cobertura del suelo por vegetación
  var fcover = denorm(layer(SNAP.fcvL2, SNAP.fcvN.map(function(c) { return neuron(c, z); })),
    0.000181230723879, 0.999638214715).rename('FCOVER');
  return {
    lai: lai,
    cab: cab,
    canopy: lai.multiply(cab).rename('CANOPY_CHL'),  // clorofila del dosel = LAI × Cab
    fapar: fapar,
    fcover: fcover,
  };
}

// =============================================================================
// PROCESAMIENTO POR ESCENA SENTINEL-2 (una fecha de adquisición)
// =============================================================================

/**
 * Paso 1 por escena:
 * - Enlaza la banda "cs" (probabilidad de cielo claro de Cloud Score+).
 * - Enmascara píxeles nublados (cs < umbral).
 * - Convierte reflectancia de escala 0–10000 a 0–1 (divide por 10000).
 * - Añade banda clear_pixel_count (= 1 donde el píxel es válido, para contar luego).
 */
function maskAndScale(img) {
  var mask = img.select('cs').gte(CONFIG.cloudThreshold);
  var clear = mask.rename('clear_pixel_count');
  return img
    .updateMask(mask)   // oculta nubes en todas las bandas
    .divide(10000)      // reflectancia surface reflectance
    .addBands(clear)
    .copyProperties(img, ['system:time_start', 'system:index']);
}

/**
 * Paso 2 por escena: calcula todos los índices espectrales y llama a SNAP.
 * normalizedDifference([A,B]) = (A-B)/(A+B).
 */
function addIndices(img) {
  var eps = ee.Image.constant(1e-6);   // evita división por cero
  var y = ee.Image.constant(0.106);    // coeficiente de suelo para ARVI
  var b4 = img.select('B4');  // rojo
  var b5 = img.select('B5');  // red edge
  var b6 = img.select('B6');
  var b7 = img.select('B7');
  var snap = snapBands(img);

  return img.addBands([
    // --- Vegetación / agua (ND*) ---
    img.normalizedDifference(['B8', 'B4']).rename('NDVI'),    // vigor vegetal
    img.normalizedDifference(['B8', 'B11']).rename('NDMI'),   // humedad en vegetación
    img.normalizedDifference(['B3', 'B8']).rename('NDWI'),    // agua en superficie
    img.normalizedDifference(['B3', 'B11']).rename('MNDWI'), // agua, menos confusión con construcción
    img.normalizedDifference(['B8', 'B3']).rename('GNDVI'),

    // --- Índices con fórmula explícita ---
    img.expression('2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))',
      {NIR: img.select('B8'), RED: b4, BLUE: img.select('B2')}).rename('EVI'),
    img.expression('((NIR - RED) / (NIR + RED + L)) * (1.0 + L)',
      {NIR: img.select('B8'), RED: b4, L: ee.Image.constant(0.5)}).rename('SAVI'),
    img.expression('(2 * NIR + 1 - sqrt(pow((2 * NIR + 1), 2) - 8 * (NIR - RED))) / 2',
      {NIR: img.select('B8'), RED: b4}).rename('MSAVI'),
    img.expression('2.4 * (NIR - RED) / (NIR + RED + 1.0)',
      {NIR: img.select('B8'), RED: b4}).rename('EVI2'),

    // --- Pigmentos y borde rojo ---
    img.expression('1.0 / (G + eps) - 1.0 / (RE1 + eps)',
      {G: img.select('B3'), RE1: b5, eps: eps}).rename('ARI'),
    img.expression('(1.0 / (G + eps) - 1.0 / (RE1 + eps)) * NIR',
      {G: img.select('B3'), RE1: b5, NIR: b7, eps: eps}).rename('MARI'),
    img.expression('(N - R - y * (R - B)) / (N + R - y * (R - B))',
      {N: img.select('B8A'), R: b4, B: img.select('B2'), y: y}).rename('ARVI'),
    img.expression('NIR / RE1 - 1.0', {NIR: b7, RE1: b5}).rename('CHL_REDEDGE'),
    // Posición del borde rojo (nm), fórmula basada en bandas 4–7
    b4.add(b7).multiply(0.5).subtract(b5).multiply(40)
      .divide(b6.subtract(b5).max(1e-6)).add(700).rename('REDEDGE_POSITION'),

    // --- Otros índices de estrés / estructura ---
    img.select('B8').subtract(b4).divide(img.select('B8').add(b4).max(1e-6))
      .pow(2).tanh().rename('kNDVI'),  // NDVI kernelizado
    img.expression('((RE1 - R) - 0.2 * (RE1 - G)) * (RE1 / (R + eps))',
      {RE1: b5, R: b4, G: img.select('B3'), eps: eps}).rename('MCARI'),
    img.select('B11').divide(img.select('B8').max(1e-6)).rename('MSI'),
    img.normalizedDifference(['B8A', 'B11']).rename('NDMISTRESS'),
    img.normalizedDifference(['B8', 'B11']).rename('NDII'),
    img.normalizedDifference(['B5', 'B4']).rename('NDCI'),
    img.select('B8').divide(b4.max(1e-6)).rename('PSSRB1'),
    img.expression('(NIR - A) / (NIR - R)',
      {NIR: img.select('B8'), A: img.select('B1'), R: b4}).rename('SIPI1'),
    b4.subtract(img.select('B2')).divide(b6.max(1e-6)).rename('PSRI'),

    // --- Biofísicas SNAP ---
    snap.lai, snap.cab, snap.canopy, snap.fapar, snap.fcover,
  ]);
}

/**
 * Carga todas las escenas S2 del predio entre start y end (fechas EE),
 * les une Cloud Score+ y aplica maskAndScale + addIndices a cada una.
 */
function processedCollection(aoi, start, end) {
  return ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(aoi)                    // solo escenas que intersectan el polígono
    .linkCollection(
      ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'),
      ['cs']                              // añade banda cs a cada imagen por system:index
    )
    .filterDate(start, end)               // rango temporal (fin exclusivo en EE)
    .map(maskAndScale)
    .map(addIndices);
}

// =============================================================================
// MOSAICOS SEMANALES (combinar varias escenas en una imagen por semana)
// =============================================================================

/**
 * A partir de las escenas de UNA semana, construye un único mosaico:
 * - mediana por banda de índice (robusta a valores atípicos),
 * - suma de clear_pixel_count (cuántas escenas aportaron cada píxel),
 * - recorte al polígono,
 * - metadatos: semana, año, ruta asset_id para exportar.
 *
 * Si no hay escenas (n=0), devuelve null y luego se filtra.
 */
function mosaicFromWeekCol(weekCol, aoi, year, week) {
  year = ee.Number(year);
  week = ee.Number(week);
  var n = weekCol.size();  // número de escenas en esa semana

  // Mediana de todos los índices → mosaico representativo de la semana
  var idx = weekCol.select(INDEX_BANDS).median()
    .multiply(100).round().toInt16();  // compactar: ×100 y guardar como entero
  // Cuántas observaciones claras hubo por píxel (suma de 0/1 por escena)
  var clear = weekCol.select('clear_pixel_count').sum();

  // Fecha de la primera escena de la semana (para calcular el lunes ISO)
  var t0 = ee.Date(ee.Algorithms.If(
    n.gt(0),
    weekCol.first().get('system:time_start'),
    ee.Date.fromYMD(year, 1, 1).millis()
  ));
  // Retroceder al lunes 00:00 UTC de esa semana (day_of_week: 1=lunes en EE)
  var monday = t0.advance(ee.Number(1).subtract(t0.get('day_of_week')), 'day');
  var isoYear = monday.get('year');

  // Nombre del asset hijo: exportPrefix/Y2024_W03
  var assetId = ee.String(CONFIG.exportPrefix.replace(/\/$/, ''))
    .cat('/Y').cat(isoYear.format())
    .cat('_W').cat(week.format('%02d'));

  return ee.Image(ee.Algorithms.If(
    n.gt(0),
    idx
      .addBands(clear.rename('clear_pixel_count'))
      .clip(aoi)
      .set({
        'system:time_start': monday.millis(),
        week: week,
        year: year,
        asset_id: assetId,
        n_images: n,
      }),
    null  // semana sin datos → no se exporta
  ));
}

/**
 * Para un año civil completo (hasta endExclusive):
 * - procesa todas las escenas del año,
 * - genera hasta 53 mosaicos (semanas 1–53 del calendario EE),
 * - elimina semanas vacías.
 */
function weeklyCollectionForYear(aoi, year, endExclusive) {
  year = ee.Number(year);
  var yStart = ee.Date.fromYMD(year, 1, 1);
  var yEnd = ee.Date.fromYMD(year, 12, 31).advance(1, 'day');
  var cap = ee.Date.min(yEnd, endExclusive);  // no usar escenas de la semana en curso

  var processed = processedCollection(aoi, yStart, cap);

  return ee.ImageCollection(
    ee.List.sequence(1, 53).map(function(w) {
      var weekCol = processed
        .filter(ee.Filter.calendarRange(w, w, 'week'))   // semana del año (1–53)
        .filter(ee.Filter.calendarRange(year, year, 'year'));
      return mosaicFromWeekCol(weekCol, aoi, year, w);
    })
  ).filter(ee.Filter.notNull(['asset_id']));
}

// =============================================================================
// EJECUCIÓN PRINCIPAL
// =============================================================================

// --- 1. Área de interés: un solo polígono de la tabla ---
var aoi = ee.FeatureCollection(CONFIG.featureCollection)
  .filter(ee.Filter.eq('wetland_id', CONFIG.featureId))
  .first()
  .geometry();

// --- 2. Fecha tope: lunes 00:00 UTC de la semana actual (excluye semana incompleta) ---
var now = ee.Date.now();
var endExclusive = now
  .advance(ee.Number(1).subtract(now.get('day_of_week')), 'day')
  .update(null, null, null, 0, 0, 0);

// --- 3. Lista de años a procesar (no supera el año en curso) ---
var endYear = ee.Number(CONFIG.endYear).min(now.get('year'));
var years = ee.List.sequence(CONFIG.startYear, endYear);

// --- 4. Colección final: todos los mosaicos semanales de todos los años ---
var weekly = ee.ImageCollection(years.map(function(y) {
  return weeklyCollectionForYear(aoi, y, endExclusive);
})).flatten();  // une las colecciones de cada año en una sola

// --- 5. Vista previa en el mapa del Code Editor ---
Map.centerObject(aoi, 12);
Map.addLayer(aoi, {color: 'yellow'}, 'AOI');
// Mediana del NDVI de todas las semanas (valores ×100, por eso max 10000)
Map.addLayer(
  weekly.select('NDVI').median(),
  {min: 0, max: 10000, palette: ['brown', 'yellow', 'green']},
  'NDVI (preview)'
);

// --- 6. Exportación a Assets ---
// Earth Engine no puede llamar Export.* dentro de un .map() del servidor.
// Por eso leemos la lista de asset_id con evaluate() y encolamos tareas en el cliente.
weekly.aggregate_array('asset_id').evaluate(function(assetIds) {
  var list = weekly.toList(assetIds.length);

  assetIds.forEach(function(assetId, i) {
    Export.image.toAsset({
      image: ee.Image(list.get(i)),       // mosaico de la semana i
      description: assetId.split('/').pop(),  // ej. Y2024_W03 (nombre corto de la tarea)
      assetId: assetId,                   // ruta completa en Assets
      region: aoi,                        // recorte al polígono
      scale: CONFIG.scale,
      maxPixels: 1e13,                    // límite alto para polígonos grandes
    });
  });

  print('Encoladas', assetIds.length, 'exportaciones.');
  print('Bandas por imagen:', INDEX_BANDS.length, '+ clear_pixel_count');
  print('Seguimiento: https://code.earthengine.google.com/tasks');
});
