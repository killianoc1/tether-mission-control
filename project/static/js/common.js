/* Shared helpers for the TETHER front-end (no build step, plain browser JS). */
(function () {
  'use strict';

  function getCookie(name) {
    const match = document.cookie.split('; ').find((row) => row.startsWith(name + '='));
    return match ? decodeURIComponent(match.split('=')[1]) : '';
  }

  /** Escape text before inserting it into HTML strings (popups, tables). */
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  /** Flatten the API's error envelope into a readable message. */
  function errorMessage(body) {
    const err = body && body.error;
    if (!err) return 'Something went wrong.';
    const parts = [];
    const walk = (node, label) => {
      if (Array.isArray(node)) node.forEach((n) => walk(n, label));
      else if (node && typeof node === 'object') Object.entries(node).forEach(([k, v]) => walk(v, k === 'non_field_errors' ? '' : k));
      else if (node) parts.push(label ? `${label}: ${node}` : String(node));
    };
    walk(err.details, '');
    return parts.length ? parts.join(' ') : (err.message || 'Something went wrong.');
  }

  /** fetch() wrapper: JSON in/out, CSRF header, and errors thrown as Error(message). */
  async function api(path, options = {}) {
    const opts = { credentials: 'same-origin', headers: { Accept: 'application/json' }, ...options };
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
    }
    if (opts.method && opts.method !== 'GET') opts.headers['X-CSRFToken'] = getCookie('csrftoken');
    const response = await fetch(path, opts);
    if (response.status === 204) return null;
    let body = null;
    try { body = await response.json(); } catch (_) { /* non-JSON body */ }
    if (!response.ok) {
      const error = new Error(errorMessage(body) || response.statusText);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  /** Create the base Leaflet map with OpenStreetMap tiles. */
  function createMap(elementId, options = {}) {
    const map = L.map(elementId, { zoomSnap: 0.25, ...options });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    return map;
  }

  const TYPE_META = {
    earthquake: { label: 'Earthquake', letter: 'E', colour: '#6c4f9c' },
    wildfire: { label: 'Wildfire', letter: 'W', colour: '#d55e00' },
    flood: { label: 'Flood', letter: 'F', colour: '#0072b2' },
    industrial: { label: 'Industrial accident', letter: 'I', colour: '#8a6d00' },
  };

  /** Round-number formatting that never prints "null". */
  function fmt(value, digits = 1, suffix = '') {
    return value === null || value === undefined ? '-' : `${Number(value).toFixed(digits)}${suffix}`;
  }

  window.Tether = { api, esc, createMap, TYPE_META, fmt, getCookie, currentUser: document.body.dataset.user || '' };
})();
