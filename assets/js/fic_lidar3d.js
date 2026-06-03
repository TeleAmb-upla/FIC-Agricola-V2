/**
 * Visualización LiDAR 3D (Three.js) — adaptado desde fondecyt_puc/explorador.html
 */
(function (global) {
  'use strict';

  const TERRAIN_CMAP = [
    [0.0, [0.2, 0.2, 0.5]],
    [0.25, [0.1, 0.5, 0.3]],
    [0.5, [0.4, 0.75, 0.2]],
    [0.75, [0.85, 0.7, 0.15]],
    [1.0, [0.95, 0.95, 0.9]]
  ];

  function elevationToRgb(t) {
    const x = Math.max(0, Math.min(1, t));
    for (let i = 1; i < TERRAIN_CMAP.length; i++) {
      if (x <= TERRAIN_CMAP[i][0]) {
        const t0 = TERRAIN_CMAP[i - 1][0];
        const t1 = TERRAIN_CMAP[i][0];
        const c0 = TERRAIN_CMAP[i - 1][1];
        const c1 = TERRAIN_CMAP[i][1];
        const u = (x - t0) / (t1 - t0 + 1e-9);
        return [
          c0[0] + (c1[0] - c0[0]) * u,
          c0[1] + (c1[1] - c0[1]) * u,
          c0[2] + (c1[2] - c0[2]) * u
        ];
      }
    }
    return TERRAIN_CMAP[TERRAIN_CMAP.length - 1][1];
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

  const api = {
    state: null,
    baseUrl: '',
    las3d: { inited: false, scene: null, camera: null, controls: null, points: [], animId: null, lastViewKey: null, cameraReady: false, pcdCache: null, _resize: null },
    loadGen: 0,

    bind: function (appState, dataStaticBase) {
      api.state = appState;
      api.baseUrl = dataStaticBase;
    },

    isLidarLayer: function () {
      return api.state && api.state.mapLayerKind === 'lidar';
    },

    findPointcloudEntry: function (wid, periodKey) {
      const meta = (api.state && api.state.metadata && api.state.metadata.drone) || api.state.metadata || {};
      const pcs = meta.pointclouds || {};
      const stem = String(wid).toLowerCase() + '_' + String(periodKey || '');
      return pcs[stem] || null;
    },

    stretchForAttr: function (attrId) {
      const meta = api.state.metadata.drone || api.state.metadata || {};
      const stretch = meta.lidar_stretch || {};
      const glim = stretch[attrId];
      if (glim && glim.vmin != null) return { vmin: glim.vmin, vmax: glim.vmax };
      const attrs = meta.lidar_attributes || {};
      const a = attrs[attrId] || {};
      return { vmin: a.fallback_vmin, vmax: a.fallback_vmax };
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
      const renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: false });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0d120f);
      const camera = new THREE.PerspectiveCamera(52, 1, 0.05, 8000);
      const controls = new THREE.OrbitControls(camera, canvas);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      api.las3d = {
        inited: true,
        scene: scene,
        camera: camera,
        controls: controls,
        renderer: renderer,
        points: [],
        animId: null,
        lastViewKey: null,
        cameraReady: false,
        pcdCache: null,
        boundary: null,
        _resize: null
      };
      function resize() {
        const box = document.getElementById('las3dView');
        if (!box || box.hidden) return;
        const w = box.clientWidth || 1;
        const h = box.clientHeight || 1;
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
      const hud = document.querySelector('.fic-map-hud');
      if (view) view.hidden = !show;
      if (mapEl) mapEl.style.visibility = show ? 'hidden' : 'visible';
      if (hud) hud.style.visibility = show ? 'hidden' : 'visible';
      document.body.classList.toggle('fic-mode-lidar-3d', !!show);
      if (!show) {
        api.loadGen++;
        api.clearPoints();
      } else if (api.las3d._resize) {
        requestAnimationFrame(api.las3d._resize);
      }
    },

    populateLidarAttrSelect: function () {
      const sel = document.getElementById('ficLidarAttr');
      const wrap = document.getElementById('ficLidarAttrFields');
      if (!sel || !wrap) return;
      const meta = api.state.metadata.drone || api.state.metadata || {};
      const attrs = meta.lidar_attributes || {};
      const ids = Object.keys(attrs);
      if (!ids.length) {
        wrap.hidden = true;
        return;
      }
      wrap.hidden = !api.isLidarLayer();
      sel.innerHTML = '';
      ids.forEach(function (id) {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = (attrs[id] && attrs[id].label) || id;
        sel.appendChild(opt);
      });
      const def = meta.lidar_default_attribute || 'canopy';
      if (ids.indexOf(api.state.lidarAttribute) < 0) api.state.lidarAttribute = def;
      sel.value = api.state.lidarAttribute || def;
      if (!sel.dataset.ficBound) {
        sel.dataset.ficBound = '1';
        sel.addEventListener('change', function () {
          api.state.lidarAttribute = sel.value;
          api.updateView();
        });
      }
    },

    loadPointcloud: async function (wid, periodKey, colorHex, cachedData) {
      let data = cachedData;
      if (!data) {
        const entry = api.findPointcloudEntry(wid, periodKey);
        if (!entry || !entry.p) return null;
        const url = api.baseUrl + '/' + entry.p;
        const r = await fetch(url);
        if (!r.ok) return null;
        data = await r.json();
      }
      if (!data || !data.count || !data.positions || !data.positions.length) return null;
      const attrId = api.state.lidarAttribute || 'canopy';
      const attr = (data.attributes && data.attributes[attrId]) || null;
      const lim = api.stretchForAttr(attrId);
      const pos = data.positions;
      let n = pos.length / 3;
      const maxRender = 120000;
      const step = n > maxRender ? Math.ceil(n / maxRender) : 1;
      const nOut = Math.ceil(n / step);
      const verts = new Float32Array(nOut * 3);
      const cols = new Float32Array(nOut * 3);
      let oi = 0;
      for (let i = 0; i < n; i += step) {
        verts[oi * 3] = pos[i * 3];
        verts[oi * 3 + 1] = pos[i * 3 + 1];
        verts[oi * 3 + 2] = pos[i * 3 + 2];
        let r, g, b;
        if (attr && attr.type === 'categorical') {
          const cls = String(attr.values[i]);
          const rgb = hexColorTo01((attr.colors && attr.colors[cls]) || '#888888');
          r = rgb[0]; g = rgb[1]; b = rgb[2];
        } else {
          let val = pos[i * 3 + 2];
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
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(verts.slice(0, oi * 3), 3));
      geom.setAttribute('color', new THREE.BufferAttribute(cols.slice(0, oi * 3), 3));
      const pointSize = Math.max(0.03, Math.min(0.08, 380 / Math.sqrt(oi)));
      const mat = new THREE.PointsMaterial({
        size: pointSize,
        vertexColors: true,
        sizeAttenuation: true,
        transparent: true,
        opacity: 0.88
      });
      return new THREE.Points(geom, mat);
    },

    updateView: async function () {
      if (!api.isLidarLayer() || api.state.selectedSource !== 'drone') {
        api.showView(false);
        return;
      }
      const wid = api.state.selectedWetland;
      const pk = api.state.mapPeriodKey;
      if (!wid || !pk) {
        api.showView(false);
        return;
      }
      api.showView(true);
      api.init();
      if (!api.las3d.inited) return;
      if (api.las3d._resize) api.las3d._resize();
      const loadGen = ++api.loadGen;
      api.clearPoints();
      const viewKey = wid + '|' + pk;
      const entry = api.findPointcloudEntry(wid, pk);
      if (!entry) {
        const note = document.getElementById('mapNote');
        if (note) note.textContent = 'No hay nube LiDAR para este predio y fecha.';
        return;
      }
      const col = (api.state.metadata.drone && api.state.metadata.drone.wetlands && api.state.metadata.drone.wetlands[wid] && api.state.metadata.drone.wetlands[wid].color) || '#1d6b4a';
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
        const pts = await api.loadPointcloud(wid, pk, col, pcdData);
        if (loadGen !== api.loadGen) return;
        if (pts) {
          api.las3d.scene.add(pts);
          api.las3d.points.push(pts);
          const box3 = new THREE.Box3().setFromObject(pts);
          if (!box3.isEmpty()) {
            const center = box3.getCenter(new THREE.Vector3());
            const size = box3.getSize(new THREE.Vector3());
            const spanXY = Math.max(size.x, size.y, 1);
            const spanZ = Math.max(size.z, 0.5);
            const dist = spanXY * 1.05;
            api.las3d.camera.position.set(center.x, center.y - dist, center.z + spanZ * 0.45 + spanXY * 0.12);
            api.las3d.controls.target.copy(center);
            api.las3d.controls.minDistance = spanXY * 0.25;
            api.las3d.controls.maxDistance = spanXY * 6;
            api.las3d.controls.update();
          }
          api.las3d.lastViewKey = viewKey;
          api.las3d.cameraReady = true;
        }
        const note = document.getElementById('mapNote');
        if (note) note.textContent = 'LiDAR 3D · arrastra para rotar · rueda para zoom';
      } catch (e) {
        console.warn('LiDAR 3D:', e);
      }
    }
  };

  global.FicLidar3d = api;
})(typeof window !== 'undefined' ? window : globalThis);
