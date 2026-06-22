/**
 * Visualización LiDAR 3D (Three.js) — portado desde fondecyt_puc/explorador.html
 */
(function (global) {
  'use strict';

  const LIDAR_CLASS_LEGEND_DEFAULT = {
    '2': { label: 'Suelo', color: '#8B4513' },
    '1': { label: 'No suelo', color: '#808080' }
  };

  /** Colormap escalar (misma paleta que fondecyt_puc). */
  function elevationToRgb(t) {
    const x = Math.max(0, Math.min(1, t));
    const stops = [
      [0, [51, 51, 153]], [0.25, [42, 122, 176]], [0.5, [143, 216, 211]],
      [0.75, [200, 232, 142]], [1, [220, 140, 51]]
    ];
    for (let i = 0; i < stops.length - 1; i++) {
      const [p0, c0] = stops[i];
      const [p1, c1] = stops[i + 1];
      if (x >= p0 && x <= p1) {
        const f = (x - p0) / (p1 - p0 + 1e-9);
        return [
          (c0[0] + (c1[0] - c0[0]) * f) / 255,
          (c0[1] + (c1[1] - c0[1]) * f) / 255,
          (c0[2] + (c1[2] - c0[2]) * f) / 255
        ];
      }
    }
    return [0.86, 0.55, 0.2];
  }

  function hexColorTo01(hex) {
    const h = String(hex || '#888').replace('#', '');
    if (h.length < 6) return [0.5, 0.5, 0.5];
    return [
      parseInt(h.slice(0, 2), 16) / 255,
      parseInt(h.slice(2, 4), 16) / 255,
      parseInt(h.slice(4, 6), 16) / 255
    ];
  }

  /** WGS84 → UTM (hemisferio sur). Zona por defecto 19 (Los Andes). */
  function wgs84ToUtm(lng, lat, zone) {
    const z = zone || 19;
    const a = 6378137.0;
    const k0 = 0.9996;
    const e = 0.081819191784;
    const latR = lat * Math.PI / 180;
    const lngR = lng * Math.PI / 180;
    const lng0 = ((z - 1) * 6 - 180 + 3) * Math.PI / 180;
    const sinLat = Math.sin(latR);
    const cosLat = Math.cos(latR);
    const tanLat = Math.tan(latR);
    const e2 = e * e;
    const ep2 = e2 / (1 - e2);
    const N = a / Math.sqrt(1 - e2 * sinLat * sinLat);
    const T = tanLat * tanLat;
    const C = ep2 * cosLat * cosLat;
    const A = cosLat * (lngR - lng0);
    const M = a * (
      (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256) * latR
      - (3 * e2 / 8 + 3 * e2 * e2 / 32 + 45 * e2 * e2 * e2 / 1024) * Math.sin(2 * latR)
      + (15 * e2 * e2 / 256 + 45 * e2 * e2 * e2 / 1024) * Math.sin(4 * latR)
      - (35 * e2 * e2 * e2 / 3072) * Math.sin(6 * latR)
    );
    const easting = k0 * N * (
      A + (1 - T + C) * A * A * A / 6
      + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A * A * A * A * A / 120
    ) + 500000;
    const northing = k0 * (
      M + N * tanLat * (
        A * A / 2
        + (5 - T + 9 * C + 4 * C * C) * A * A * A * A / 24
        + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A * A * A * A * A * A / 720
      )
    );
    return [easting, northing + (lat < 0 ? 10000000 : 0)];
  }

  function pointInRing(x, y, ring) {
    if (!ring || ring.length < 3) return true;
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0];
      const yi = ring[i][1];
      const xj = ring[j][0];
      const yj = ring[j][1];
      const intersect = ((yi > y) !== (yj > y))
        && (x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-12) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function exteriorRingFromGeometry(geom) {
    if (!geom) return null;
    if (geom.type === 'Polygon') return geom.coordinates && geom.coordinates[0];
    if (geom.type === 'MultiPolygon') {
      let best = null;
      let bestArea = 0;
      (geom.coordinates || []).forEach(function (poly) {
        const ring = poly && poly[0];
        if (!ring || ring.length < 4) return;
        let area = 0;
        for (let i = 0; i < ring.length - 1; i++) {
          area += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
        }
        area = Math.abs(area);
        if (area > bestArea) {
          bestArea = area;
          best = ring;
        }
      });
      return best;
    }
    return null;
  }

  function cuartelLocalRing(pcdData) {
    const cid = api.state && api.state.selectedCuartel;
    if (!cid || typeof global.ficGetCuartelFeature !== 'function') return null;
    const feat = global.ficGetCuartelFeature(cid);
    const ring = exteriorRingFromGeometry(feat && feat.geometry);
    const origin = pcdData && pcdData.origin;
    if (!ring || !origin || origin.length < 2) return null;
    const zone = 19;
    const local = [];
    ring.forEach(function (pt) {
      const utm = wgs84ToUtm(Number(pt[0]), Number(pt[1]), zone);
      local.push([
        Math.round((utm[0] - origin[0]) * 1000) / 1000,
        Math.round((utm[1] - origin[1]) * 1000) / 1000
      ]);
    });
    return local.length >= 3 ? local : null;
  }

  const api = {
    state: null,
    baseUrl: '',
    lidarClassColors: null,
    las3d: {
      inited: false, scene: null, camera: null, controls: null, renderer: null,
      points: [], boundary: null, animId: null, lastViewKey: null,
      cameraReady: false, pcdCache: null, _resize: null
    },
    loadGen: 0,

    bind: function (appState, dataStaticBase) {
      api.state = appState;
      api.baseUrl = dataStaticBase;
    },

    isLidarLayer: function () {
      return api.state && String(api.state.mapLayerKind || '').toLowerCase() === 'lidar';
    },

    droneMeta: function () {
      return (api.state && api.state.metadata && api.state.metadata.drone) || api.state.metadata || {};
    },

    findPointcloudEntry: function (wid, periodKey) {
      const pcs = api.droneMeta().pointclouds || {};
      const prefix = String(wid).toLowerCase() + '_';
      const stem = prefix + String(periodKey || '');
      if (pcs[stem]) return pcs[stem];
      var keys = Object.keys(pcs).filter(function (k) { return k.indexOf(prefix) === 0; });
      if (!keys.length) return null;
      keys.sort(function (a, b) { return String(b).localeCompare(String(a)); });
      return pcs[keys[0]] || null;
    },

    lidarAttrMeta: function (attrId) {
      const attrs = api.droneMeta().lidar_attributes || {};
      return attrs[attrId] || null;
    },

    stretchForAttr: function (attrId, pcdData) {
      const data = pcdData || (api.las3d.pcdCache && api.las3d.pcdCache.data);
      const fromPcd = data && data.attributes && data.attributes[attrId];
      if (fromPcd && fromPcd.type === 'scalar' &&
          fromPcd.vmin != null && fromPcd.vmax != null &&
          isFinite(Number(fromPcd.vmin)) && isFinite(Number(fromPcd.vmax))) {
        return { vmin: Number(fromPcd.vmin), vmax: Number(fromPcd.vmax), source: 'pointcloud' };
      }
      const meta = api.droneMeta();
      const stretch = meta.lidar_stretch || {};
      const glim = stretch[attrId];
      if (glim && glim.vmin != null && glim.vmax != null) {
        return { vmin: glim.vmin, vmax: glim.vmax, source: 'metadata' };
      }
      const a = api.lidarAttrMeta(attrId) || {};
      return {
        vmin: a.fallback_vmin != null ? a.fallback_vmin : a.vmin,
        vmax: a.fallback_vmax != null ? a.fallback_vmax : a.vmax,
        source: 'catalog'
      };
    },

    /** Gradiente CSS alineado con ``elevationToRgb`` (coloreado escalar LiDAR). */
    scalarLegendGradientCss: function () {
      const stops = [0, 0.25, 0.5, 0.75, 1];
      const parts = stops.map(function (t) {
        const rgb = elevationToRgb(t);
        const r = Math.round(rgb[0] * 255);
        const g = Math.round(rgb[1] * 255);
        const b = Math.round(rgb[2] * 255);
        return 'rgb(' + r + ',' + g + ',' + b + ') ' + (t * 100).toFixed(1) + '%';
      });
      return 'linear-gradient(to top, ' + parts.join(', ') + ')';
    },

    viewKey: function (wid, pk) {
      const cid = api.state && api.state.selectedCuartel;
      return String(wid || '') + '|' + String(pk || '') + '|' + String(cid || '');
    },

    clearPoints: function () {
      const L3 = api.las3d;
      if (!L3.scene) return;
      L3.points.forEach(function (p) {
        L3.scene.remove(p);
        if (p.geometry) p.geometry.dispose();
        if (p.material) p.material.dispose();
      });
      L3.points = [];
      if (L3.boundary) {
        L3.scene.remove(L3.boundary);
        if (L3.boundary.geometry) L3.boundary.geometry.dispose();
        if (L3.boundary.material) L3.boundary.material.dispose();
        L3.boundary = null;
      }
    },

    init: function () {
      if (api.las3d.inited || typeof THREE === 'undefined') return;
      const canvas = document.getElementById('las3dCanvas');
      if (!canvas) return;
      const prev = api.las3d || {};
      const renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: false });
      renderer.setClearColor(0x0c1210, 1);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0c1210);
      const camera = new THREE.PerspectiveCamera(52, 1, 0.05, 8000);
      const controls = new THREE.OrbitControls(camera, canvas);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.screenSpacePanning = true;
      api.las3d = {
        inited: true,
        scene: scene,
        camera: camera,
        controls: controls,
        renderer: renderer,
        points: [],
        animId: null,
        lastViewKey: prev.lastViewKey || null,
        cameraReady: prev.cameraReady || false,
        pcdCache: prev.pcdCache || null,
        boundary: null,
        _resize: null
      };
      function resize() {
        const box = document.getElementById('las3dView');
        if (!box || box.hidden) return;
        const w = box.clientWidth || 1;
        const h = box.clientHeight || 1;
        if (w < 1 || h < 1) return;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
      api.las3d._resize = resize;
      window.addEventListener('resize', resize);
      function tick() {
        api.las3d.animId = requestAnimationFrame(tick);
        controls.update();
        renderer.render(scene, camera);
      }
      tick();
      resize();
    },

    showView: function (show) {
      const view = document.getElementById('las3dView');
      const mapEl = document.getElementById('mapa');
      if (view) view.hidden = !show;
      if (mapEl) {
        mapEl.style.visibility = show ? 'hidden' : 'visible';
        mapEl.style.opacity = show ? '0' : '1';
        mapEl.style.pointerEvents = show ? 'none' : '';
      }
      document.body.classList.toggle('fic-mode-lidar-3d', !!show);
      if (!show) {
        api.loadGen++;
        api.clearPoints();
        api.applyClassLegend(false);
      } else if (api.las3d._resize) {
        requestAnimationFrame(api.las3d._resize);
      }
    },

    classificationLegendItems: function () {
      const colors = api.lidarClassColors || {};
      const keys = Object.keys(colors).length
        ? Object.keys(colors)
        : Object.keys(LIDAR_CLASS_LEGEND_DEFAULT);
      return keys.sort(function (a, b) { return String(b).localeCompare(String(a)); }).map(function (key) {
        const def = LIDAR_CLASS_LEGEND_DEFAULT[key] || { label: 'Clase ' + key, color: '#888888' };
        return { label: def.label, color: colors[key] || def.color };
      });
    },

    applyClassLegend: function (show) {
      const box = document.getElementById('lidarClassLegend');
      const list = document.getElementById('lidarClassLegendItems');
      if (!box || !list) return;
      const visible = show !== false && api.isLidarLayer() &&
        api.state.selectedSource === 'drone' &&
        String(api.state.lidarAttribute || '') === 'classification';
      if (!visible) {
        box.hidden = true;
        box.style.display = 'none';
        box.setAttribute('aria-hidden', 'true');
        return;
      }
      list.innerHTML = api.classificationLegendItems().map(function (it) {
        return '<div class="lidar-class-legend-item">' +
          '<span class="lidar-class-legend-swatch" style="background:' + it.color + '"></span>' +
          '<span>' + it.label + '</span></div>';
      }).join('');
      box.hidden = false;
      box.style.display = 'flex';
      box.setAttribute('aria-hidden', 'false');
    },

    addBoundary: function (data, colorHex, ringOverride) {
      const ring = ringOverride || (data && data.boundary);
      if (!ring || ring.length < 2 || !api.las3d.scene) return;
      const flat = [];
      ring.forEach(function (p) {
        flat.push(p[0], p[1], p[2] != null ? p[2] : 0);
      });
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.Float32BufferAttribute(flat, 3));
      const mat = new THREE.LineBasicMaterial({
        color: new THREE.Color(colorHex || '#ffffff'),
        transparent: true,
        opacity: 0.95
      });
      const line = new THREE.LineLoop(geom, mat);
      api.las3d.scene.add(line);
      api.las3d.boundary = line;
    },

    populateLidarAttrSelect: function () {
      const sel = document.getElementById('ficLidarAttr');
      if (!sel) return;
      if (!api.isLidarLayer() || api.state.selectedSource !== 'drone') {
        return;
      }
      if (typeof global.ficIsLidarAttrPanelActive === 'function' && !global.ficIsLidarAttrPanelActive()) {
        return;
      }
      const attrs = api.droneMeta().lidar_attributes || {};
      const ids = Object.keys(attrs).sort(function (a, b) {
        if (a === 'rgb') return -1;
        if (b === 'rgb') return 1;
        const la = (attrs[a] && attrs[a].label) || a;
        const lb = (attrs[b] && attrs[b].label) || b;
        return la.localeCompare(lb, 'es', { sensitivity: 'base' });
      });
      if (!ids.length) return;
      sel.innerHTML = '';
      ids.forEach(function (id) {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = (attrs[id] && attrs[id].label) || id;
        sel.appendChild(opt);
      });
      const def = api.droneMeta().lidar_default_attribute || 'rgb';
      if (ids.indexOf(api.state.lidarAttribute) < 0) api.state.lidarAttribute = def;
      sel.value = api.state.lidarAttribute || def;
      if (!sel.dataset.ficBound) {
        sel.dataset.ficBound = '1';
        sel.addEventListener('change', function () {
          api.state.lidarAttribute = sel.value;
          api.updateView({ preserveCamera: true });
          if (typeof global.ficApplyLidarLegends === 'function') global.ficApplyLidarLegends();
          if (typeof global.ficUpdateLidarAttrDescription === 'function') global.ficUpdateLidarAttrDescription();
          if (typeof global.ficSyncDroneMapTitle === 'function') global.ficSyncDroneMapTitle();
        });
      }
    },

    loadPointcloud: function (wid, periodKey, colorHex, cachedData, opts) {
      opts = opts || {};
      let data = cachedData;
      if (!data) return null;
      if (!data.count || !data.positions || !data.positions.length) return null;
      const attrId = api.state.lidarAttribute || 'rgb';
      const attr = (data.attributes && data.attributes[attrId]) || null;
      const lim = api.stretchForAttr(attrId, data);
      const pos = data.positions;
      const clipRing = opts.skipCuartelClip ? null : cuartelLocalRing(data);
      let n = pos.length / 3;
      const maxRender = 750000;
      const step = n > maxRender ? Math.ceil(n / maxRender) : 1;
      const nOut = Math.ceil(n / step);
      const verts = new Float32Array(nOut * 3);
      const cols = new Float32Array(nOut * 3);
      let oi = 0;
      for (let i = 0; i < n; i += step) {
        const pi = i * 3;
        const px = pos[pi];
        const py = pos[pi + 1];
        if (clipRing && !pointInRing(px, py, clipRing)) continue;
        verts[oi * 3] = pos[pi];
        verts[oi * 3 + 1] = pos[pi + 1];
        verts[oi * 3 + 2] = pos[pi + 2];
        let r, g, b;
        if (attr && attr.type === 'rgb') {
          const rv = Number(attr.red[i] || 0);
          const scale = rv > 255 ? (1 / 65535) : (1 / 255);
          r = rv * scale;
          g = Number(attr.green[i] || 0) * scale;
          b = Number(attr.blue[i] || 0) * scale;
        } else if (attr && attr.type === 'categorical') {
          const cls = String(attr.values[i]);
          if (attrId === 'classification' && attr.colors) {
            api.lidarClassColors = attr.colors;
          }
          const rgb = hexColorTo01((attr.colors && attr.colors[cls]) || '#888888');
          r = rgb[0]; g = rgb[1]; b = rgb[2];
        } else {
          let val = pos[pi + 2];
          if (attr && attr.type === 'scalar' && attr.values && attr.values.length) {
            val = Number(attr.values[i]);
          }
          const vmin = Number.isFinite(lim.vmin) ? lim.vmin : Number(attr && attr.vmin);
          const vmax = Number.isFinite(lim.vmax) ? lim.vmax : Number(attr && attr.vmax);
          const span = Math.max((vmax - vmin) || 1, 1e-6);
          const t = Math.max(0, Math.min(1, (val - vmin) / span));
          const rgb = elevationToRgb(t);
          r = rgb[0]; g = rgb[1]; b = rgb[2];
        }
        cols[oi * 3] = r;
        cols[oi * 3 + 1] = g;
        cols[oi * 3 + 2] = b;
        oi++;
      }
      if (!oi && clipRing && !opts.skipCuartelClip) {
        return api.loadPointcloud(wid, periodKey, colorHex, cachedData, { skipCuartelClip: true });
      }
      if (!oi) return null;
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(verts.subarray(0, oi * 3), 3));
      geom.setAttribute('color', new THREE.BufferAttribute(cols.subarray(0, oi * 3), 3));
      geom.computeBoundingSphere();
      const pointSize = Math.max(0.1, Math.min(0.24, 820 / Math.sqrt(Math.max(oi, 1))));
      const mat = new THREE.PointsMaterial({
        size: pointSize,
        vertexColors: true,
        sizeAttenuation: true,
        transparent: true,
        opacity: 0.92
      });
      return new THREE.Points(geom, mat);
    },

  fitCameraToBoundary: function (data) {
      const ring = data && data.boundary;
      if (!ring || ring.length < 2 || !api.las3d.camera) return false;
      const box3 = new THREE.Box3();
      ring.forEach(function (p) {
        box3.expandByPoint(new THREE.Vector3(p[0], p[1], p[2] != null ? p[2] : 0));
      });
      if (box3.isEmpty()) return false;
      api.fitCamera(box3, false, null);
      return true;
    },

    fitCamera: function (box3, preserveCamera, viewKey) {
      const L3 = api.las3d;
      if (preserveCamera) {
        L3.lastViewKey = viewKey;
        L3.cameraReady = true;
        return;
      }
      if (box3.isEmpty()) return;
      const center = box3.getCenter(new THREE.Vector3());
      const size = box3.getSize(new THREE.Vector3());
      const spanXY = Math.max(size.x, size.y, 1);
      const spanZ = Math.max(size.z, 0.5);
      const dist = spanXY * 1.05;
      L3.camera.position.set(
        center.x,
        center.y - dist,
        center.z + spanZ * 0.45 + spanXY * 0.12
      );
      L3.controls.target.copy(center);
      L3.controls.minDistance = spanXY * 0.25;
      L3.controls.maxDistance = spanXY * 6;
      L3.controls.update();
      L3.lastViewKey = viewKey;
      L3.cameraReady = true;
    },

    updateView: async function (opts) {
      if (!api.isLidarLayer() || api.state.selectedSource !== 'drone') {
        api.showView(false);
        return;
      }
      const wid = api.state.selectedWetland;
      let pk = api.state.mapPeriodKey;
      if (!wid) {
        api.showView(false);
        return;
      }
      const entry = api.findPointcloudEntry(wid, pk);
      if (entry && entry.period_key) pk = entry.period_key;
      if (!pk) {
        api.showView(false);
        return;
      }
      if (!entry) {
        api.showView(false);
        if (typeof global.ficSyncDroneMapTitle === 'function') {
          global.ficSyncDroneMapTitle(
            typeof global.ficDroneUnavailableMessage === 'function'
              ? global.ficDroneUnavailableMessage('lidar', pk)
              : 'No hay datos LiDAR para este vuelo.'
          );
        }
        api.applyClassLegend(false);
        return;
      }
      api.showView(true);
      api.init();
      if (!api.las3d.inited) return;
      if (api.las3d._resize) api.las3d._resize();
      const loadGen = ++api.loadGen;
      api.clearPoints();
      const viewKey = api.viewKey(wid, pk);
      const preserveCamera = !!(opts && opts.preserveCamera) &&
        api.las3d.cameraReady && api.las3d.lastViewKey === viewKey;
      const col = (api.droneMeta().wetlands && api.droneMeta().wetlands[wid] && api.droneMeta().wetlands[wid].color) || '#1d6b4a';
      try {
        let pcdData = api.las3d.pcdCache && api.las3d.pcdCache.key === viewKey ? api.las3d.pcdCache.data : null;
        if (!pcdData) {
          const url = api.baseUrl + '/' + entry.p;
          const r = await fetch(url);
          if (!r.ok) throw new Error('fetch');
          pcdData = await r.json();
          if (loadGen !== api.loadGen) return;
          api.las3d.pcdCache = { key: viewKey, data: pcdData };
        }
        const pts = api.loadPointcloud(wid, pk, col, pcdData);
        if (loadGen !== api.loadGen) return;
        const box3 = new THREE.Box3();
        if (pts) {
          api.las3d.scene.add(pts);
          api.las3d.points.push(pts);
          box3.expandByObject(pts);
        } else if (typeof global.ficSyncDroneMapTitle === 'function') {
          global.ficSyncDroneMapTitle('LiDAR — no se pudieron dibujar puntos para este cuartel. Prueba otra capa o fecha.');
        }
        const cuRing = cuartelLocalRing(pcdData);
        if (cuRing) {
          const ring3d = cuRing.map(function (p) { return [p[0], p[1], 0]; });
          api.addBoundary(pcdData, col, ring3d);
        } else if (pcdData) {
          api.addBoundary(pcdData, col);
        }
        if (!pts || box3.isEmpty()) {
          api.fitCameraToBoundary(pcdData);
        } else {
          api.fitCamera(box3, preserveCamera && pts, viewKey);
        }
        if (api.las3d._resize) {
          requestAnimationFrame(function () {
            if (api.las3d._resize) api.las3d._resize();
          });
        }
        if (typeof global.ficApplyLidarLegends === 'function') global.ficApplyLidarLegends();
        if (typeof global.ficSyncDroneMapTitle === 'function') global.ficSyncDroneMapTitle();
      } catch (e) {
        console.warn('LiDAR 3D:', e);
        if (typeof global.ficSyncDroneMapTitle === 'function') {
          global.ficSyncDroneMapTitle('LiDAR — error al cargar');
        }
      }
    }
  };

  global.FicLidar3d = api;
})(typeof window !== 'undefined' ? window : globalThis);
