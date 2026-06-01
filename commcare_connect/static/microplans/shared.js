/*
 * Microplans shared front-end helpers.
 *
 * Plain ES (no build step) — loaded as a classic <script> before each page's
 * inline script (and before service_delivery_layer.js), exposing one global
 * namespace `window.Microplans`. Everything here was previously copy-pasted
 * inline across review.html, setup.html, program_workspace.html, compare.html
 * and service_delivery_layer.js; this is the single home for it.
 *
 * Design notes:
 *  - `esc`, `colorFor`, `walkCoords`, `fitTo`, `upsertSource`, `chip` are pure
 *    drop-ins for the old inline copies (identical behaviour).
 *  - `post` is a drop-in for the old `(url, body) => fetch(...)` lambda but also
 *    accepts `{ csrf, signal }`. Pages bind their template CSRF token once via a
 *    thin local wrapper, so existing `post(url, body)` call sites are unchanged.
 *  - `apiCall` is the higher-level wrapper for new/cleaned call sites: it does
 *    the fetch, checks `resp.ok`, guards JSON parsing, and rejects with the
 *    server-supplied `detail` so every caller doesn't re-implement that.
 */
(function () {
  'use strict';

  // ---- HTML escape (user-controlled values before innerHTML interpolation) ---
  const esc = (s) =>
    String(s == null ? '' : s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[c],
    );

  // ---- CSRF -----------------------------------------------------------------
  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? decodeURIComponent(m.pop()) : '';
  }
  // Page binds its template-rendered token via setCsrf(); fall back to the cookie
  // (Django sets `csrftoken` wherever ensure_csrf_cookie is applied).
  let _csrf = '';
  function setCsrf(token) {
    _csrf = token || '';
  }
  function getCsrf() {
    return _csrf || getCookie('csrftoken');
  }

  // ---- fetch ----------------------------------------------------------------
  // Drop-in for the old inline `post`: returns the raw fetch Promise so existing
  // `.then((r) => r.json())` chains keep working. `opts.csrf` overrides the
  // module token; `opts.signal` wires an AbortController.
  function post(url, body, opts) {
    opts = opts || {};
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': opts.csrf || getCsrf(),
      },
      body: JSON.stringify(body == null ? {} : body),
      signal: opts.signal,
    });
  }

  // DELETE counterpart of `post`: returns the raw fetch Promise. Body optional
  // (most DELETE endpoints key off the URL); CSRF + signal handled like `post`.
  function del(url, opts) {
    opts = opts || {};
    return fetch(url, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': opts.csrf || getCsrf(),
      },
      body: opts.body != null ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  }

  // Higher-level: POST and resolve to parsed JSON, or reject with a useful
  // Error. Use for new call sites so status/parse handling lives in one place.
  // Throws Error with `.aborted === true` when the request was cancelled, so
  // callers can ignore those silently.
  function _abortError() {
    const err = new Error('aborted');
    err.aborted = true;
    return err;
  }
  // Shared status/parse handling for apiCall + apiGet: guard JSON parsing and
  // reject with the server-supplied detail on a non-2xx or {status:"error"}.
  async function _parseJsonResponse(resp) {
    let data = null;
    try {
      data = await resp.json();
    } catch (e) {
      data = null; // non-JSON body (e.g. an HTML 500 page)
    }
    if (!resp.ok || (data && data.status === 'error')) {
      const detail =
        (data && (data.detail || data.error || data.message)) ||
        `Request failed (${resp.status}).`;
      throw new Error(detail);
    }
    return data;
  }

  async function apiCall(url, body, opts) {
    let resp;
    try {
      resp = await post(url, body, opts);
    } catch (e) {
      if (e && e.name === 'AbortError') throw _abortError();
      throw new Error('Network error — check your connection and try again.');
    }
    return _parseJsonResponse(resp);
  }

  // GET + parse JSON, same handling as apiCall. Pass {signal} for an
  // AbortController; rejects with `.aborted === true` when cancelled.
  async function apiGet(url, opts) {
    opts = opts || {};
    let resp;
    try {
      resp = await fetch(url, { signal: opts.signal });
    } catch (e) {
      if (e && e.name === 'AbortError') throw _abortError();
      throw new Error('Network error — check your connection and try again.');
    }
    return _parseJsonResponse(resp);
  }

  // Enqueue a server-side generation job and poll it to completion.
  // The endpoint returns 202 {task_id, poll_url}; we poll poll_url until the
  // task's `state` is completed (resolve with its result envelope, which carries
  // its own {status:'ok'|'error', ...}) or failed (reject). Cold map generation
  // runs on the Celery worker so it no longer blocks a web worker — the tradeoff
  // is the client waits across a few polls. opts: {csrf, signal, interval,
  // timeoutMs, onProgress(message)}.
  async function enqueueAndPoll(url, body, opts) {
    opts = opts || {};
    const enq = await apiCall(url, body, {
      csrf: opts.csrf,
      signal: opts.signal,
    });
    const pollUrl = enq && enq.poll_url;
    if (!pollUrl) throw new Error('Server did not return a poll URL.');
    const interval = opts.interval || 1200;
    const deadline = Date.now() + (opts.timeoutMs || 180000);
    for (;;) {
      await new Promise((res, rej) => {
        const t = setTimeout(res, interval);
        if (opts.signal) {
          if (opts.signal.aborted) {
            clearTimeout(t);
            rej(_abortError());
            return;
          }
          opts.signal.addEventListener(
            'abort',
            () => {
              clearTimeout(t);
              rej(_abortError());
            },
            { once: true },
          );
        }
      });
      const st = await apiGet(pollUrl, { signal: opts.signal });
      if (st.state === 'completed') return st.result || {};
      if (st.state === 'failed')
        throw new Error(st.detail || 'Generation failed.');
      if (opts.onProgress && st.message) opts.onProgress(st.message);
      if (Date.now() > deadline)
        throw new Error(
          'Timed out waiting for the server. Try a smaller area.',
        );
    }
  }

  // ---- color ----------------------------------------------------------------
  // Golden-angle HSL hash → adjacent territories visually distinct without a
  // palette lookup. Connect-gis uses the same trick on group_id.
  function colorFor(key) {
    if (!key) return '#cbd5e1'; // unassigned = neutral
    let h = 0;
    for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
    const hue = (Math.abs(h) * 137.508) % 360;
    return `hsl(${Math.round(hue)}, 65%, 55%)`;
  }

  // Categorical palette (service-delivery per-opportunity overlay): stable,
  // high-contrast colors indexed by selection order.
  const OPP_COLORS = [
    '#2563eb',
    '#dc2626',
    '#16a34a',
    '#9333ea',
    '#ea580c',
    '#0891b2',
    '#ca8a04',
    '#db2777',
    '#4f46e5',
    '#65a30d',
  ];
  const oppColorFor = (i) =>
    OPP_COLORS[
      ((i % OPP_COLORS.length) + OPP_COLORS.length) % OPP_COLORS.length
    ];

  // ---- geometry / map helpers ----------------------------------------------
  // Walk a GeoJSON coordinate array to its leaf [lng, lat] pairs.
  function walkCoords(coords, fn) {
    if (!Array.isArray(coords)) return;
    if (typeof coords[0] === 'number') {
      fn(coords);
      return;
    }
    coords.forEach((c) => walkCoords(c, fn));
  }

  // Compute a mapboxgl.LngLatBounds over a geometry, Feature, or
  // FeatureCollection (or an array of any of those). Returns null if empty.
  function boundsOf(input) {
    if (!input || typeof mapboxgl === 'undefined') return null;
    const b = new mapboxgl.LngLatBounds();
    let any = false;
    const addGeom = (g) => {
      if (g && g.coordinates)
        walkCoords(g.coordinates, (c) => {
          b.extend(c);
          any = true;
        });
    };
    const addFeature = (f) => {
      if (f) addGeom(f.geometry || f);
    };
    const items = Array.isArray(input) ? input : [input];
    items.forEach((it) => {
      if (!it) return;
      if (it.type === 'FeatureCollection')
        (it.features || []).forEach(addFeature);
      else if (it.type === 'Feature') addFeature(it);
      else addGeom(it);
    });
    return any ? b : null;
  }

  // Fit the map to a geometry/feature(s); no-op on empty input.
  function fitTo(map, input, opts) {
    if (!map) return;
    const b = boundsOf(input);
    if (b)
      map.fitBounds(
        b,
        Object.assign({ padding: 40, duration: 600 }, opts || {}),
      );
  }

  // Set a geojson source's data, creating the source if absent.
  function upsertSource(map, id, data, sourceOpts) {
    if (!map) return;
    const s = map.getSource(id);
    if (s) s.setData(data);
    else
      map.addSource(
        id,
        Object.assign({ type: 'geojson', data }, sourceOpts || {}),
      );
  }

  // Remove layers (by id) and optionally their source(s) — used to actually free
  // map resources on mode/dimension switches instead of just hiding layers.
  function removeLayers(map, layerIds) {
    if (!map) return;
    (layerIds || []).forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
    });
  }
  function removeSourceAndLayers(map, sourceId, layerIds) {
    if (!map) return;
    removeLayers(map, layerIds);
    if (map.getSource(sourceId)) map.removeSource(sourceId);
  }

  // ---- misc UI --------------------------------------------------------------
  function chip(label, value, hint) {
    const title = hint ? ` title="${esc(hint)}"` : '';
    return `<span class="text-xs px-1.5 py-0.5 rounded bg-gray-50 border text-gray-600"${title}>${esc(
      label,
    )} <b>${esc(value)}</b></span>`;
  }

  // Trailing-edge debounce — for search-as-you-type inputs that hit the server.
  function debounce(fn, ms) {
    let t = null;
    return function () {
      const args = arguments,
        ctx = this;
      if (t) clearTimeout(t);
      t = setTimeout(
        () => {
          t = null;
          fn.apply(ctx, args);
        },
        ms == null ? 250 : ms,
      );
    };
  }

  window.Microplans = {
    esc,
    getCookie,
    setCsrf,
    getCsrf,
    post,
    del,
    apiCall,
    apiGet,
    enqueueAndPoll,
    colorFor,
    OPP_COLORS,
    oppColorFor,
    walkCoords,
    boundsOf,
    fitTo,
    upsertSource,
    removeLayers,
    removeSourceAndLayers,
    chip,
    debounce,
  };
})();
