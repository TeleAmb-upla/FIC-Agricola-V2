/**
 * Sentinel-2 en explorador FIC — vista semanal/mensual (build_sentinel2_local metadata).
 */
(function (global) {
  'use strict';

  var CMAP = {
    RdYlGn: 'linear-gradient(to top,#a50026,#d73027,#f46d43,#fdae61,#fee08b,#ffffbf,#d9ef8b,#a6d96a,#66bd63,#1a9850,#006837)',
    RdYlBu: 'linear-gradient(to top,#a50026,#d73027,#f46d43,#fdae61,#fee090,#ffffbf,#e0f3f8,#abd9e9,#74add1,#4575b4,#313695)',
    YlGn: 'linear-gradient(to top,#ffffcc,#d9f0a3,#addd8e,#78c679,#41ab5d,#238443,#005a32)',
    RdYlBu_r: 'linear-gradient(to top,#313695,#4575b4,#74add1,#abd9e9,#e0f3f8,#ffffbf,#fee090,#fdae61,#f46d43,#d73027,#a50026)',
    RdYlGn_r: 'linear-gradient(to top,#006837,#1a9850,#66bd63,#a6d96a,#d9ef8b,#ffffbf,#fee08b,#fdae61,#f46d43,#d73027,#a50026)',
    viridis: 'linear-gradient(to top,#440154,#482575,#414487,#355f8d,#2a788e,#21908d,#22a884,#44bf70,#7ad151,#bdd26c,#fde725)',
    terrain: 'linear-gradient(to top,#333399,#2a7ab0,#4eb0d3,#8fd594,#c8e98e,#f2f0a0,#e8c66d,#d29b52,#b87333,#8f5631,#6b3f2a)'
  };

  var api = {
    state: null,
    baseUrl: '',
    chart: null,

    bind: function (appState, dataStaticBase) {
      api.state = appState;
      api.baseUrl = dataStaticBase;
    },

    meta: function () {
      return (api.state && api.state.metadata && api.state.metadata.sentinel2) || {};
    },

    ts: function () {
      return (api.state && api.state.datasets && api.state.datasets.sentinel2) || {};
    },

    isActive: function () {
      return api.state && api.state.selectedSource === 'sentinel2';
    },

    compositeKey: function () {
      return api.compositeKeyForSide(api.state.s2CompareSide === 'left' ? 'left' : 'right');
    },

    compositeKeyForSide: function (side) {
      var m = api.meta();
      var vm = (m.view_modes || {})[api.state.s2ViewMode || 'weekly'];
      if (!vm) return null;
      var tpl = side === 'left' ? vm.left_composite_template : vm.right_composite_template;
      var period = Number(api.state.s2Period);
      if (!tpl || !Number.isFinite(period)) return null;
      if (api.state.s2ViewMode === 'monthly') {
        return tpl.replace('{month:02d}', String(period).padStart(2, '0'));
      }
      return tpl.replace('{week:02d}', String(period).padStart(2, '0'));
    },

    selectedPredioId: function () {
      var s = api.state;
      if (!s) return null;
      return s.selectedPredio || s.selectedWetland || null;
    },

    resolveRasterForSide: function (wid, side) {
      var ck = api.compositeKeyForSide(side);
      if (!ck) return null;
      return (api.meta().rasters || {})[api.rasterKey(wid, ck, api.state.s2Band || 'NDVI')] || null;
    },

    rasterKey: function (wid, compositeKey, band) {
      return String(wid || '').toLowerCase() + '_' + String(compositeKey || '').toLowerCase() + '_' + String(band || 'NDVI').toLowerCase();
    },

    resolveRaster: function (wid) {
      var m = api.meta();
      var ck = api.compositeKey();
      if (!ck) return null;
      return (m.rasters || {})[api.rasterKey(wid, ck, api.state.s2Band || 'NDVI')] || null;
    },

    wetlandBounds: function (wid) {
      var block = api.meta().predios || api.meta().wetlands || {};
      var w = block[String(wid || '').toLowerCase()];
      return w && w.leaflet_bounds ? w.leaflet_bounds : null;
    },

    defaultsFromMeta: function () {
      var m = api.meta();
      api.state.s2Band = m.default_chart_band || 'NDVI';
      api.state.s2ViewMode = 'weekly';
      api.state.s2CompareSide = 'right';
      var wvm = (m.view_modes || {}).weekly || {};
      var lw = m.last_complete_week || {};
      api.state.s2Period = wvm.default_week || lw.week || 1;
      if (api.state.s2ViewMode === 'monthly') {
        var lm = m.last_complete_month || {};
        api.state.s2Period = lm.month || 1;
      }
    },

    toggleFields: function (show) {
      var box = document.getElementById('ficS2Fields');
      var droneWrap = document.getElementById('ficDroneFields');
      if (box) box.hidden = !show;
      if (droneWrap) droneWrap.hidden = !!show;
      var dock = document.getElementById('ficS2ChartDock');
      if (dock) {
        dock.hidden = !show;
        dock.setAttribute('aria-hidden', show ? 'false' : 'true');
      }
      if (show) api.syncSideBySideUi();
      if (!show) api.destroyChart();
    },

    syncSideBySideUi: function () {
      var cmpBlock = document.getElementById('ficS2CompareBlock');
      var side = document.getElementById('ficS2SideBySide');
      if (side && api.state.s2SideBySide == null) api.state.s2SideBySide = side.checked;
      if (cmpBlock) cmpBlock.hidden = !!api.state.s2SideBySide;
    },

    bindControlsOnce: function () {
      if (api._controlsBound) return;
      api._controlsBound = true;
      var vm = document.getElementById('ficS2ViewMode');
      var cmp = document.getElementById('ficS2Compare');
      var side = document.getElementById('ficS2SideBySide');
      var per = document.getElementById('ficS2Period');
      var band = document.getElementById('ficS2Band');
      if (vm) vm.addEventListener('change', function () {
        api.state.s2ViewMode = vm.value;
        api.syncPeriodDefault();
        api.fillPeriodSelect();
        api.refresh();
      });
      if (cmp) cmp.addEventListener('change', function () {
        api.state.s2CompareSide = cmp.value;
        api.refresh();
      });
      if (side) {
        api.state.s2SideBySide = side.checked;
        side.addEventListener('change', function () {
          api.state.s2SideBySide = side.checked;
          api.syncSideBySideUi();
          api.refresh();
        });
      }
      if (per) per.addEventListener('change', function () {
        api.state.s2Period = Number(per.value);
        api.refresh();
      });
      if (band) band.addEventListener('change', function () {
        api.state.s2Band = band.value;
        if (global.FicIndexInfo) global.FicIndexInfo.updatePanel(api.state.s2Band, 'sentinel2');
        api.refresh();
      });
      var toggle = document.getElementById('ficS2ChartToggle');
      if (toggle && !toggle.dataset.ficBound) {
        toggle.dataset.ficBound = '1';
        toggle.addEventListener('click', function () {
          var card = document.getElementById('ficS2ChartCard');
          var expanded = card && card.classList.toggle('fic-asesor-chart-card--collapsed') === false;
          toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
          toggle.textContent = expanded ? 'Ocultar gráfico' : 'Mostrar gráfico';
          if (expanded) api.buildChart();
          else api.destroyChart();
        });
      }
    },

    syncPeriodDefault: function () {
      var m = api.meta();
      if (api.state.s2ViewMode === 'monthly') {
        var lm = m.last_complete_month || {};
        api.state.s2Period = lm.month || 1;
      } else {
        var wvm = (m.view_modes || {}).weekly || {};
        var lw = m.last_complete_week || {};
        api.state.s2Period = wvm.default_week || lw.week || 1;
      }
    },

    fillBandSelect: function () {
      var sel = document.getElementById('ficS2Band');
      if (!sel) return;
      var indices = api.meta().indices || {};
      var preferred = ['NDVI', 'NDWI', 'NDMI', 'EVI', 'SAVI'];
      var keys = preferred.filter(function (k) { return indices[k]; });
      Object.keys(indices).sort().forEach(function (k) {
        if (keys.indexOf(k) < 0) keys.push(k);
      });
      sel.innerHTML = '';
      keys.forEach(function (k) {
        var opt = document.createElement('option');
        opt.value = k;
        opt.textContent = (indices[k] && indices[k].label) || k;
        sel.appendChild(opt);
      });
      if (keys.indexOf(api.state.s2Band) < 0) api.state.s2Band = keys[0] || 'NDVI';
      sel.value = api.state.s2Band;
    },

    fillPeriodSelect: function () {
      var sel = document.getElementById('ficS2Period');
      if (!sel) return;
      var m = api.meta();
      var vm = (m.view_modes || {})[api.state.s2ViewMode || 'weekly'] || {};
      sel.innerHTML = '';
      if (api.state.s2ViewMode === 'monthly') {
        var labels = vm.month_labels || ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
        (vm.months || [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]).forEach(function (mo, i) {
          var opt = document.createElement('option');
          opt.value = String(mo);
          opt.textContent = (labels[i] || ('Mes ' + mo)) + ' (' + mo + ')';
          sel.appendChild(opt);
        });
      } else {
        (vm.weeks || []).forEach(function (w) {
          var opt = document.createElement('option');
          opt.value = String(w);
          opt.textContent = 'Semana ' + w;
          sel.appendChild(opt);
        });
      }
      sel.value = String(api.state.s2Period);
      var cmp = document.getElementById('ficS2Compare');
      if (cmp) {
        cmp.options[0].text = vm.right_label || 'Año actual';
        cmp.options[1].text = vm.left_label || 'Histórico';
      }
    },

    buildControls: function () {
      api.bindControlsOnce();
      var vm = document.getElementById('ficS2ViewMode');
      if (vm) vm.value = api.state.s2ViewMode || 'weekly';
      var cmp = document.getElementById('ficS2Compare');
      if (cmp) cmp.value = api.state.s2CompareSide === 'left' ? 'left' : 'right';
      api.fillBandSelect();
      api.fillPeriodSelect();
    },

    applyColorbar: function () {
      var colorbar = document.getElementById('colorbarBox');
      if (!colorbar || !api.isActive()) return;
      var band = api.state.s2Band || 'NDVI';
      var idx = (api.meta().indices || {})[band] || {};
      var grad = CMAP[idx.colormap] || CMAP.RdYlGn;
      document.getElementById('cbGradient').style.background = grad;
      document.getElementById('cbTitle').textContent = idx.label || band;
      document.getElementById('cbMin').textContent = idx.vmin != null ? idx.vmin : '';
      document.getElementById('cbMax').textContent = idx.vmax != null ? idx.vmax : '';
      colorbar.style.display = 'flex';
    },

    overlayNote: function () {
      var m = api.meta();
      var vm = (m.view_modes || {})[api.state.s2ViewMode || 'weekly'] || {};
      if (api.state.s2SideBySide) {
        var wid = api.selectedPredioId();
        var lh = api.resolveRasterForSide(wid, 'left');
        var rh = api.resolveRasterForSide(wid, 'right');
        return String(api.state.s2Band) + ' · ' + (vm.left_label || 'Histórico') + ' | ' + (vm.right_label || 'Actual')
          + (lh && rh ? ' (desliza el control central)' : '');
      }
      var r = api.resolveRaster(api.selectedPredioId());
      var ck = api.compositeKey();
      var sideLab = api.state.s2CompareSide === 'left' ? (vm.left_label || 'Histórico') : (vm.right_label || 'Año actual');
      if (r && r.l) return String(api.state.s2Band) + ' · ' + sideLab + ' · ' + r.l;
      return String(api.state.s2Band) + ' · ' + sideLab + ' · ' + (ck || '—');
    },

    clearSideBySide: function (mapa) {
      if (api.state.s2SideBySideControl && mapa) {
        try { mapa.removeControl(api.state.s2SideBySideControl); } catch (e) { /* noop */ }
        api.state.s2SideBySideControl = null;
      }
    },

    makeImageLayer: function (r, bounds, opacity) {
      if (!r || !r.p || !global.L) return null;
      var lb = global.L.latLngBounds(bounds[0], bounds[1]);
      if (!lb.isValid()) return null;
      var defs = api.meta().raster_defaults || {};
      var ly = global.L.imageOverlay(api.baseUrl + '/' + r.p, lb, {
        opacity: opacity != null ? opacity : (defs.opacity != null ? defs.opacity : 0.88),
        className: defs.render_mode === 'smooth' ? '' : 'preview-raster',
        interactive: false
      });
      if (!ly.getContainer) {
        ly.getContainer = function () { return this._image; };
      }
      return ly;
    },

    createOverlays: function (mapa) {
      var out = [];
      api.clearSideBySide(mapa);
      if (!mapa || !api.isActive() || !api.selectedPredioId()) return out;
      var wid = api.selectedPredioId();
      var bounds = api.wetlandBounds(wid);
      if (!bounds || bounds.length !== 2) return out;

      if (api.state.s2SideBySide !== false && global.L && global.L.control && global.L.control.sideBySide) {
        var leftR = api.resolveRasterForSide(wid, 'left');
        var rightR = api.resolveRasterForSide(wid, 'right');
        var leftLy = leftR ? api.makeImageLayer(leftR, bounds, 0.92) : null;
        var rightLy = rightR ? api.makeImageLayer(rightR, bounds, 0.92) : null;
        if (leftLy && rightLy) {
          leftLy.addTo(mapa);
          rightLy.addTo(mapa);
          out.push(leftLy, rightLy);
          api.state.s2SideBySideControl = global.L.control.sideBySide(leftLy, rightLy).addTo(mapa);
          return out;
        }
      }

      var r = api.resolveRaster(wid);
      if (!r || !r.p) return out;
      var ly = api.makeImageLayer(r, bounds);
      if (ly) {
        ly.addTo(mapa);
        out.push(ly);
      }
      return out;
    },

    destroyChart: function () {
      var canvas = document.getElementById('ficS2ChartCanvas');
      if (canvas && global.Chart && global.Chart.getChart) {
        var ch = global.Chart.getChart(canvas);
        if (ch) ch.destroy();
      }
      api.chart = null;
    },

    buildChart: function () {
      if (!api.isActive() || typeof global.Chart === 'undefined') return;
      var canvas = document.getElementById('ficS2ChartCanvas');
      var card = document.getElementById('ficS2ChartCard');
      if (!canvas || !card || card.classList.contains('fic-asesor-chart-card--collapsed')) return;
      var wid = api.selectedPredioId();
      if (!wid) return;
      var band = api.state.s2Band || 'NDVI';
      var tsBlock = api.ts().predios || api.ts().wetlands || {};
      var wl = tsBlock[String(wid).toLowerCase()] || {};
      var series = wl[band];
      if (!series || !series.weeks || !series.weeks.length) return;
      api.destroyChart();
      var labels = series.weeks.map(function (w) { return 'S' + w; });
      var hist = series.historical_median || [];
      var cur = (series.current_year && series.current_year.values_by_week) || [];
      var ctx = canvas.getContext('2d');
      if (!ctx) return;
      api.chart = new global.Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [
            {
              label: 'Mediana histórica',
              data: hist,
              borderColor: 'rgba(107, 127, 114, 0.95)',
              backgroundColor: 'rgba(107, 127, 114, 0.12)',
              borderWidth: 2,
              pointRadius: 0,
              tension: 0.25
            },
            {
              label: String(series.current_year && series.current_year.year || api.meta().current_year || ''),
              data: cur,
              borderColor: 'rgba(29, 107, 74, 0.95)',
              backgroundColor: 'rgba(29, 107, 74, 0.15)',
              borderWidth: 2.5,
              pointRadius: 2,
              tension: 0.2
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'top', align: 'end' }
          },
          scales: {
            y: { suggestedMin: -1, suggestedMax: 1 }
          }
        }
      });
    },

    refresh: function () {
      if (!api.isActive()) return;
      if (typeof global.ficRefreshMapOverlay === 'function') global.ficRefreshMapOverlay();
      api.buildChart();
    },

    onSourceActivated: function () {
      if (!api.state.metadata.sentinel2) return;
      api.defaultsFromMeta();
      if (api.state.s2SideBySide == null) api.state.s2SideBySide = true;
      api.toggleFields(true);
      api.buildControls();
      api.syncSideBySideUi();
      api.refresh();
      if (global.FicIndexInfo) global.FicIndexInfo.updatePanel(api.state.s2Band, 'sentinel2');
    },

    onSourceDeactivated: function () {
      api.clearSideBySide(api.state.mapInstance);
      api.toggleFields(false);
    }
  };

  global.FicS2 = api;
})(typeof window !== 'undefined' ? window : globalThis);
