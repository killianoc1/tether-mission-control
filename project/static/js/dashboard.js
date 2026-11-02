/* Dashboard: scenario map + list, filters, proximity search, create form. */
(function () {
  'use strict';
  const { api, esc, createMap, TYPE_META } = window.Tether;

  const map = createMap('map').setView([53.15, -6.3], 9);
  const markerLayer = L.layerGroup().addTo(map);
  const searchLayer = L.layerGroup().addTo(map);
  const listEl = document.getElementById('scenario-list');
  const statusEl = document.getElementById('list-status');
  const pickedEl = document.getElementById('picked');

  let scenarios = [];           // GeoJSON features from the API
  let nearMap = null;           // id -> distance (m) when a proximity search is active
  let picked = null;            // {lat, lng}
  let pickedMarker = null;
  const markers = new Map();    // scenario id -> Leaflet marker

  function pinIcon(type) {
    const meta = TYPE_META[type] || { letter: '?', colour: '#333' };
    return L.divIcon({
      className: '',
      html: `<div class="pin" style="background:${meta.colour}" aria-hidden="true">${meta.letter}</div>`,
      iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -14],
    });
  }

  // GeoJSON puts the primary key on the Feature itself (f.id), not inside f.properties.
  function popupHtml(f) {
    const p = f.properties;
    const best = nearMap && nearMap.has(f.id) ? `<br>${(nearMap.get(f.id) / 1000).toFixed(1)} km from picked point` : '';
    return `<strong>${esc(p.name)}</strong><br>${esc(p.scenario_type_label)} &middot; seed ${p.seed}${best}<br>` +
           `<a href="/scenarios/${f.id}/">Open mission map</a>`;
  }

  // With a proximity search active only scenarios inside the radius are shown; otherwise all of them.
  function visible() {
    return nearMap ? scenarios.filter((f) => nearMap.has(f.id)) : scenarios;
  }

  function render() {
    markerLayer.clearLayers();
    markers.clear();
    listEl.innerHTML = '';
    const rows = visible();
    if (nearMap) rows.sort((a, b) => nearMap.get(a.id) - nearMap.get(b.id));

    rows.forEach((f) => {
      const p = f.properties;
      const [lng, lat] = f.geometry.coordinates;
      const marker = L.marker([lat, lng], { icon: pinIcon(p.scenario_type), title: `${p.name} (${p.scenario_type_label})`, keyboard: false })
        .bindPopup(popupHtml(f)).addTo(markerLayer);
      markers.set(f.id, marker);

      const li = document.createElement('li');
      li.className = 'list-group-item scenario-item';
      li.innerHTML = `
        <div class="d-flex justify-content-between align-items-start gap-2">
          <div>
            <a class="fw-semibold stretched-link" href="/scenarios/${f.id}/">${esc(p.name)}</a>
            <div class="small text-secondary">
              <span class="badge type-badge type-${esc(p.scenario_type)}">${esc(p.scenario_type_label)}</span>
              seed ${p.seed} &middot; ${p.hazard_count} hazard / ${p.obstacle_count} obstacle cells &middot; ${p.run_count} runs
              ${nearMap ? ` &middot; <strong>${(nearMap.get(f.id) / 1000).toFixed(1)} km</strong>` : ''}
            </div>
          </div>
        </div>`;
      li.addEventListener('mouseenter', () => marker.openPopup());
      listEl.appendChild(li);
    });

    statusEl.textContent = rows.length
      ? `${rows.length} scenario${rows.length === 1 ? '' : 's'}${nearMap ? ' near the picked point' : ''}.`
      : 'No scenarios match.';

    if (rows.length && !nearMap) {
      const bounds = L.latLngBounds(rows.map((f) => [f.geometry.coordinates[1], f.geometry.coordinates[0]]));
      map.fitBounds(bounds.pad(0.25), { maxZoom: 12 });
    }
  }

  // Fetch scenarios from the API (applying the type/name filters) and redraw the map and list.
  async function load() {
    const params = new URLSearchParams();
    const type = document.getElementById('filter-type').value;
    const q = document.getElementById('filter-q').value.trim();
    if (type) params.set('type', type);
    if (q) params.set('q', q);
    try {
      const data = await api(`/api/scenarios/?${params}`);
      scenarios = data.features || [];
      render();
    } catch (err) {
      statusEl.textContent = `Could not load scenarios: ${err.message}`;
    }
  }

  /* ----- pick a point on the map (used for proximity search and new scenarios) ----- */
  map.on('click', (e) => {
    picked = { lat: e.latlng.lat, lng: e.latlng.lng };
    if (pickedMarker) pickedMarker.setLatLng(e.latlng);
    else pickedMarker = L.circleMarker(e.latlng, { radius: 7, color: '#0b1f33', weight: 3, fillColor: '#fff', fillOpacity: 1 }).addTo(map);
    pickedEl.textContent = `Picked point: ${picked.lat.toFixed(5)}, ${picked.lng.toFixed(5)}`;
  });

  /* ----- Q5: scenarios near the picked point (ST_DWithin) ----- */
  document.getElementById('near-btn').addEventListener('click', async () => {
    if (!picked) { pickedEl.textContent = 'Click the map first to pick a search centre.'; return; }
    const km = Number(document.getElementById('near-radius').value) || 25;
    try {
      const rows = await api(`/api/scenarios/nearby/?lng=${picked.lng}&lat=${picked.lat}&radius_km=${km}`);
      nearMap = new Map(rows.map((r) => [r.id, Number(r.distance_m)]));
      searchLayer.clearLayers();
      L.circle([picked.lat, picked.lng], { radius: km * 1000, color: '#0072b2', weight: 2, fillOpacity: 0.05 }).addTo(searchLayer);
      render();
      map.fitBounds(searchLayer.getLayers()[0].getBounds());
    } catch (err) {
      statusEl.textContent = `Proximity search failed: ${err.message}`;
    }
  });
  document.getElementById('near-clear').addEventListener('click', () => {
    nearMap = null; searchLayer.clearLayers(); render();
  });

  document.getElementById('filter-type').addEventListener('change', load);
  let debounce;
  document.getElementById('filter-q').addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(load, 250); });

  /* ----- create-scenario form (signed-in users only) ----- */
  // The form only exists in the page when the user is signed in (see the {% if user.is_authenticated %} in the template).
  const form = document.getElementById('new-scenario-form');
  if (form) {
    const errBox = document.getElementById('form-error');
    document.getElementById('use-picked').addEventListener('click', () => {
      if (!picked) { errBox.textContent = 'Click the map first to pick a location.'; errBox.classList.remove('d-none'); return; }
      errBox.classList.add('d-none');
      form.elements.lat.value = picked.lat.toFixed(6);
      form.elements.lng.value = picked.lng.toFixed(6);
    });
    document.getElementById('newScenarioModal').addEventListener('show.bs.modal', () => {
      if (picked && !form.elements.lat.value) { form.elements.lat.value = picked.lat.toFixed(6); form.elements.lng.value = picked.lng.toFixed(6); }
      else if (!form.elements.lat.value) { const c = map.getCenter(); form.elements.lat.value = c.lat.toFixed(6); form.elements.lng.value = c.lng.toFixed(6); }
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      errBox.classList.add('d-none');
      const data = Object.fromEntries(new FormData(form).entries());
      const body = {
        name: data.name, description: data.description, scenario_type: data.scenario_type,
        lat: Number(data.lat), lng: Number(data.lng),
        initial_battery: Number(data.initial_battery), cell_size_m: Number(data.cell_size_m),
        comm_radius_cells: Number(data.comm_radius_cells),
        run_baselines: document.getElementById('f-run').checked,
      };
      if (data.seed !== '') body.seed = Number(data.seed);
      const btn = document.getElementById('create-btn');
      btn.disabled = true; btn.textContent = 'Generating...';
      try {
        const created = await api('/api/scenarios/', { method: 'POST', body });
        window.location.href = `/scenarios/${created.id}/`;
      } catch (err) {
        errBox.textContent = err.message; errBox.classList.remove('d-none');
        btn.disabled = false; btn.textContent = 'Generate scenario';
      }
    });
  }

  load();
})();
