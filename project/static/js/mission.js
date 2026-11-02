/* Mission map: layers, agent trajectories, replay slider, spatial metrics, click-to-query. */
(function () {
  'use strict';
  const { api, esc, createMap, fmt } = window.Tether;

  // The template puts the scenario id and edit permission in data-* attributes on #mission.
  // data-can-edit only decides which buttons to SHOW; the API enforces permissions on the server.
  const root = document.getElementById('mission');
  const scenarioId = root.dataset.scenarioId;
  const canEdit = root.dataset.canEdit === '1';

  // Okabe-Ito colours + distinct dash patterns so lines stay distinguishable without relying on colour alone.
  const AGENT_STYLES = {
    astar: { colour: '#0072B2', dash: null, weight: 4, label: 'A*' },
  };
  const FALLBACK = ['#56B4E9', '#D55E00', '#F0E442', '#999999'];
  const styleFor = (agent, i) => AGENT_STYLES[agent] || { colour: FALLBACK[i % FALLBACK.length], dash: '6 3 1 3', weight: 4, label: agent };

  const showError = (msg) => { const el = document.getElementById('page-error'); el.textContent = msg; el.classList.remove('d-none'); };

  const map = createMap('map');
  const layerGroups = { grid: L.layerGroup(), obstacles: L.layerGroup(), hazards: L.layerGroup(), coverage: L.layerGroup() };
  const queryLayer = L.layerGroup().addTo(map);
  Object.values(layerGroups).forEach((g) => g.addTo(map));

  let runsState = [];   // [{id, agent, style, positions, marker, trail, full, metrics}]
  let maxStep = 0;
  let playing = false;
  let timer = null;

  // Small helper: a round/square labelled marker (B, T, !, agent initials) built as a Leaflet divIcon.
  const pin = (cls, text, size = 28) => L.divIcon({
    className: '', html: `<div class="pin ${cls}" aria-hidden="true">${text}</div>`,
    iconSize: [size, size], iconAnchor: [size / 2, size / 2], popupAnchor: [0, -size / 2],
  });

  /* --------------------------------------------------------------------- layers */
  // Draw the static parts of the scenario (grid, obstacles, hazards, coverage zone, base and target).
  // Each kind goes in its own layer group so the checkboxes can show or hide it.
  function drawLayers(layers) {
    L.geoJSON(layers.extent, { style: { color: '#0b1f33', weight: 2, fill: false } }).addTo(layerGroups.grid);
    L.geoJSON(layers.obstacles, {
      style: { color: '#2b3239', weight: 1, fillColor: '#3d4752', fillOpacity: 0.85 },
      onEachFeature: (f, l) => l.bindTooltip(`Obstacle (cell ${f.properties.cell_x}, ${f.properties.cell_y}) - impassable`),
    }).addTo(layerGroups.obstacles);
    L.geoJSON(layers.hazards, {
      style: { color: '#d55e00', weight: 2, dashArray: '4 3', fillColor: '#d55e00', fillOpacity: 0.4 },
      onEachFeature: (f, l) => l.bindTooltip(`Hazard (cell ${f.properties.cell_x}, ${f.properties.cell_y}) - ${f.properties.cost_multiplier}x battery cost`),
    }).addTo(layerGroups.hazards);
    if (layers.coverage) {
      L.geoJSON(layers.coverage, { style: { color: '#009e73', weight: 2, fillColor: '#009e73', fillOpacity: 0.08 }, interactive: false })
        .addTo(layerGroups.coverage);
    }
    // GeoJSON coordinates are [lng, lat]; Leaflet markers want [lat, lng], hence [by, bx] below.
    const [bx, by] = layers.base.coordinates, [tx, ty] = layers.target.coordinates;
    L.marker([by, bx], { icon: pin('pin-base', 'B'), title: 'Base station', zIndexOffset: 500 }).bindPopup('<strong>Base station</strong><br>Mission starts and must end here.').addTo(map);
    L.marker([ty, tx], { icon: pin('pin-target', 'T'), title: 'Target', zIndexOffset: 500 }).bindPopup('<strong>Target</strong><br>The rover must find this location.').addTo(map);
    map.fitBounds(L.geoJSON(layers.extent).getBounds().pad(0.1));
  }

  /* ----------------------------------------------------------------- rover route + replay */
  function buildAgents(replay, metrics) {
    const byRun = new Map(metrics.map((m) => [m.run_id, m]));
    runsState = replay.map((r, i) => {
      const style = styleFor(r.agent, i);
      // The replay API sends each step as [lng, lat, battery, cell_x, cell_y]; swap to [lat, lng] for Leaflet.
      const latlngs = r.positions.map((p) => [p[1], p[0]]);
      const full = L.polyline(latlngs, { color: style.colour, weight: style.weight, dashArray: style.dash, opacity: 0.35 }).addTo(map);
      const trail = L.polyline([latlngs[0]], { color: style.colour, weight: style.weight + 1, dashArray: style.dash, opacity: 0.95 }).addTo(map);
      const marker = L.marker(latlngs[0], {
        icon: pin(`pin-agent`, esc(style.label.charAt(0)), 24), zIndexOffset: 1000 + i, title: style.label,
      }).addTo(map);
      marker.bindTooltip(style.label, { className: 'agent-tip', direction: 'top', offset: [0, -10] });
      return { id: r.run_id, agent: r.agent, style, positions: r.positions, latlngs, full, trail, marker, metrics: byRun.get(r.run_id), visible: true };
    });
    maxStep = Math.max(0, ...runsState.map((r) => r.positions.length - 1));
    const slider = document.getElementById('step-slider');
    slider.max = String(maxStep);

    // Colour the agent pins (inline style on the inner element).
    runsState.forEach((r) => { const el = r.marker.getElement(); if (el) el.firstChild.style.background = r.style.colour; });

    const ul = document.getElementById('agent-list');
    ul.innerHTML = '';
    runsState.forEach((r) => {
      const li = document.createElement('li');
      li.className = 'form-check mb-1';
      const dash = r.style.dash ? `border-top-style:dashed;` : '';
      li.innerHTML = `<input class="form-check-input" type="checkbox" id="a-${r.id}" checked>
        <label class="form-check-label" for="a-${r.id}">
          <span class="swatch" style="border-top-color:${r.style.colour};${dash}border-top-width:${r.style.weight}px"></span>${esc(r.style.label)}
          <span class="small text-secondary">${r.metrics && r.metrics.success ? '&check; success' : '&cross; ' + esc(r.metrics ? r.metrics.outcome.replace('_', ' ') : '')}</span>
        </label>`;
      li.querySelector('input').addEventListener('change', (e) => toggleAgent(r, e.target.checked));
      ul.appendChild(li);
    });
    setStep(0, false);
  }

  function toggleAgent(r, on) {
    r.visible = on;
    [r.full, r.trail, r.marker].forEach((l) => (on ? l.addTo(map) : map.removeLayer(l)));
    if (on) setStep(Number(document.getElementById('step-slider').value), false);
  }

  // The core of the replay: move every agent to the same step number, grow its trail and update its battery.
  // The slider, the Play timer and the reset all go through this one function.
  function setStep(step, announce) {
    const lines = [];
    runsState.forEach((r) => {
      const idx = Math.min(step, r.positions.length - 1);
      r.marker.setLatLng(r.latlngs[idx]);
      r.trail.setLatLngs(r.latlngs.slice(0, idx + 1));
      const battery = r.positions[idx][2];
      r.marker.setTooltipContent(`${r.style.label}: battery ${battery.toFixed(1)}`);
      const done = idx === r.positions.length - 1 && step >= r.positions.length - 1;
      lines.push(`${r.style.label} battery ${battery.toFixed(1)}${done ? (r.metrics && r.metrics.success ? ' (home)' : ' (ended)') : ''}`);
    });
    document.getElementById('step-label').textContent = `Step ${step} / ${maxStep}`;
    document.getElementById('step-slider').value = String(step);
    if (announce !== false) document.getElementById('step-status').textContent = lines.join(' | ');
  }

  // Play/pause: a timer advances the slider one step every 180 ms until the longest route ends.
  function setPlaying(on) {
    playing = on;
    const btn = document.getElementById('play-btn');
    btn.textContent = on ? 'Pause' : 'Play';
    btn.setAttribute('aria-pressed', String(on));
    clearInterval(timer);
    if (on) {
      timer = setInterval(() => {
        const slider = document.getElementById('step-slider');
        const next = Number(slider.value) + 1;
        if (next > maxStep) { setPlaying(false); document.getElementById('step-status').textContent = 'Replay finished.'; return; }
        setStep(next, false);
      }, 180);
    }
  }

  document.getElementById('play-btn').addEventListener('click', () => {
    const slider = document.getElementById('step-slider');
    if (!playing && Number(slider.value) >= maxStep) setStep(0, false);
    setPlaying(!playing);
  });
  document.getElementById('step-slider').addEventListener('input', (e) => { setPlaying(false); setStep(Number(e.target.value), true); });

  /* --------------------------------------------------------------- metrics + PNR */
  function outcomeCell(m) {
    return m.success
      ? '<span class="outcome-ok">&check; Success</span>'
      : `<span class="outcome-bad">&cross; ${esc(m.outcome.replace('_', ' '))}</span>`;
  }

  function renderMetrics(metrics, pnrByRun) {
    const body = document.getElementById('metrics-body');
    if (!metrics.length) { body.innerHTML = '<tr><td colspan="8" class="text-secondary">No runs yet. Use "Re-run route" to generate them.</td></tr>'; return; }
    body.innerHTML = metrics.map((m, i) => {
      const style = styleFor(m.agent, i);
      const pnr = pnrByRun.get(m.run_id);
      return `<tr>
        <th scope="row"><span class="swatch" style="border-top-color:${style.colour};border-top-width:${style.weight}px;${style.dash ? 'border-top-style:dashed' : ''}"></span>${esc(style.label)}</th>
        <td>${outcomeCell(m)}</td>
        <td class="text-end">${m.steps}</td>
        <td class="text-end">${fmt(m.battery_remaining)}</td>
        <td class="text-end">${fmt(m.path_length_m, 0)}</td>
        <td class="text-end">${fmt(m.hazard_exposure_m, 0)}</td>
        <td class="text-end">${fmt(m.comm_uptime_pct, 0, '%')}</td>
        <td class="text-end">${pnr ? `step ${pnr.step_index}` : 'never'}</td>
      </tr>`;
    }).join('');
  }

  // Q4: ask the API for each run's point of no return (one request per run); it is shown in the results table.
  // A failure for one run is caught so it cannot break the whole page.
  async function loadPointsOfNoReturn(metrics) {
    const pnrByRun = new Map();
    await Promise.all(metrics.map(async (m) => {
      try {
        const data = await api(`/api/runs/${m.run_id}/return-range/`);
        if (data.point_of_no_return) {
          pnrByRun.set(m.run_id, data.point_of_no_return);
        }
      } catch (_) { /* a single run failing must not break the page */ }
    }));
    return pnrByRun;
  }

  /* ------------------------------------------------------------------- Q1: click to query */
  // Q1: clicking anywhere asks PostGIS (KNN query) for the 3 nearest hazards/obstacles, in metres,
  // and shows them in a popup with dotted lines drawn to each one.
  map.on('click', async (e) => {
    queryLayer.clearLayers();
    try {
      const rows = await api(`/api/scenarios/${scenarioId}/nearest/?lng=${e.latlng.lng}&lat=${e.latlng.lat}&limit=3`);
      if (!rows.length) return;
      const items = rows.map((r) => `<li>${esc(r.kind)} at cell (${r.cell_x}, ${r.cell_y}): <strong>${r.distance_m} m</strong></li>`).join('');
      rows.forEach((r) => L.polyline([e.latlng, [r.lat, r.lng]], { color: '#444', weight: 2, dashArray: '2 5', interactive: false }).addTo(queryLayer));
      L.popup().setLatLng(e.latlng).setContent(`<strong>Nearest hazards / obstacles</strong><ol class="ps-3 mb-0">${items}</ol>`).openOn(map);
    } catch (err) { showError(err.message); }
  });

  /* ------------------------------------------------------------------------ owner actions */
  if (canEdit) {
    // Edit mode: rename / re-describe the scenario via PATCH (geometry is derived from the seed, so not editable).
    const editBtn = document.getElementById('edit-btn');
    const editForm = document.getElementById('edit-scenario-form');
    const editError = document.getElementById('edit-error');
    const editModal = new bootstrap.Modal(document.getElementById('editScenarioModal'));
    editBtn.classList.remove('d-none');
    editBtn.addEventListener('click', () => {
      document.getElementById('e-name').value = editBtn.dataset.name;
      document.getElementById('e-desc').value = editBtn.dataset.description;
      editError.classList.add('d-none');
      editModal.show();
    });
    editForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const save = document.getElementById('edit-save-btn');
      const name = document.getElementById('e-name').value.trim();
      const description = document.getElementById('e-desc').value.trim();
      editError.classList.add('d-none');
      if (!name) { editError.textContent = 'Name is required.'; editError.classList.remove('d-none'); return; }
      save.disabled = true; save.textContent = 'Saving...';
      try {
        await api(`/api/scenarios/${scenarioId}/`, { method: 'PATCH', body: { name, description } });
        window.location.reload();
      } catch (err) {
        editError.textContent = err.message; editError.classList.remove('d-none');
        save.disabled = false; save.textContent = 'Save changes';
      }
    });

    const rerun = document.getElementById('rerun-btn'), del = document.getElementById('delete-btn');
    rerun.classList.remove('d-none'); del.classList.remove('d-none');
    rerun.addEventListener('click', async () => {
      rerun.disabled = true; rerun.textContent = 'Running...';
      try { await api(`/api/scenarios/${scenarioId}/simulate/`, { method: 'POST', body: { replace: true } }); window.location.reload(); }
      catch (err) { showError(err.message); rerun.disabled = false; rerun.textContent = 'Re-run route'; }
    });
    del.addEventListener('click', async () => {
      if (!window.confirm('Delete this scenario and all of its runs?')) return;
      try { await api(`/api/scenarios/${scenarioId}/`, { method: 'DELETE' }); window.location.href = '/'; }
      catch (err) { showError(err.message); }
    });
  }

  /* ----------------------------------------------------------------------------- start */
  (async function init() {
    try {
      // Fetch the three endpoints in parallel (faster than one after another), then draw once all arrive.
      const [layers, metrics, replay] = await Promise.all([
        api(`/api/scenarios/${scenarioId}/layers/`),
        api(`/api/scenarios/${scenarioId}/metrics/`),
        api(`/api/scenarios/${scenarioId}/replay/`),
      ]);
      drawLayers(layers);
      buildAgents(replay, metrics);
      const pnrByRun = await loadPointsOfNoReturn(metrics);
      renderMetrics(metrics, pnrByRun);
    } catch (err) {
      showError(`Could not load this scenario: ${err.message}`);
    }
  })();
})();
