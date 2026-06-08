/**
 * Descripciones de índices (adaptado del frontend oficial FIC-Agricola / map.js).
 */
(function (global) {
  'use strict';

  var INDEX_INFO = {
    NDVI: 'Cuantifica el verdor y la densidad de vegetación. Valores cercanos a 1 (0.6–0.9) indican vegetación densa y saludable; valores bajos o negativos corresponden a suelo desnudo o zonas sin vegetación.',
    NDWI: 'Detecta humedad en vegetación y cuerpos de agua. Valores altos señalan mayor contenido de agua; valores negativos zonas secas.',
    NDMI: 'Mide humedad foliar (estrés hídrico). Valores altos: plantas bien hidratadas; valores negativos: sequía o estrés severo.',
    EVI: 'Índice de vegetación mejorado para áreas densas o húmedas. Valores altos indican vegetación vigorosa.',
    SAVI: 'Vegetación en suelos expuestos, corrigiendo el brillo del suelo.',
    MSAVI: 'Versión mejorada del SAVI con corrección automática del suelo.',
    GNDVI: 'Sensible al contenido de clorofila usando la banda verde.',
    ndvi: 'Cuantifica el verdor y la densidad de vegetación (dron).',
    ndwi: 'Detecta humedad en vegetación (dron).',
    rgb: 'Ortomosaico a color del vuelo de dron.',
    thermal: 'Ortomosaico térmico del vuelo de dron.',
    lidar: 'Nube de puntos 3D del vuelo LiDAR.'
  };

  global.FicIndexInfo = {
    text: function (bandOrIndex) {
      var k = String(bandOrIndex || '').trim();
      if (!k) return '';
      return INDEX_INFO[k] || INDEX_INFO[k.toUpperCase()] || '';
    },
    updatePanel: function (bandOrIndex, sourceKey) {
      var el = document.getElementById('ficIndexInfo');
      if (!el) return;
      var txt = global.FicIndexInfo.text(bandOrIndex);
      if (sourceKey === 'sentinel2' && txt) {
        el.hidden = false;
        el.textContent = txt;
      } else if (sourceKey === 'drone') {
        var ik = String(bandOrIndex || '').toLowerCase();
        var dt = global.FicIndexInfo.text(ik);
        if (dt) {
          el.hidden = false;
          el.textContent = dt;
        } else {
          el.hidden = true;
          el.textContent = '';
        }
      } else {
        el.hidden = true;
        el.textContent = '';
      }
    }
  };
})(typeof window !== 'undefined' ? window : globalThis);
