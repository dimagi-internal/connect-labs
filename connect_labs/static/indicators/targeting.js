/* Targeting surface: choropleth of under-5 mortality, a threshold, and the
 * births that sit above it.
 *
 * The map is painted once from the full ADM1 GeoJSON; moving the threshold
 * repaints client-side from a feature-state flag rather than refetching
 * geometry, so the slider stays responsive. Only the numbers and the table come
 * back from the server, because only they require the rollup rules.
 */
(function () {
  'use strict';

  var TG = window.TG;
  var methodInfo = null;
  var currentMethod = null;
  var map = null;
  var selected = new Set();
  var geojson = null;
  var debounceTimer = null;

  // Africa's extent, used to frame the map on load. A fixed centre/zoom cannot
  // do this: the right zoom depends on the container's size, and at a wide
  // viewport it left the continent small and surrounded by South America and
  // South Asia.
  var AFRICA_BOUNDS = [
    [-18.5, -35.5],
    [52.0, 38.0],
  ];

  // Sequential ramp for the mortality choropleth. Stops are the conventional
  // reporting breaks for under-5 mortality, not an even split — 25/50/75/100
  // are the numbers people already have intuitions about.
  var STOPS = [
    [0, '#f0f9f8'],
    [25, '#c3e5e1'],
    [50, '#8fcac4'],
    [75, '#57a9a2'],
    [100, '#2f867f'],
    [150, '#14554f'],
  ];

  function fmt(n) {
    if (n === null || n === undefined) return '—';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return Math.round(n / 1e3) + 'k';
    return Math.round(n).toLocaleString();
  }

  // Row content is server data, but it lands in innerHTML — escape it rather
  // than trusting a source name or URL to be markup-free.
  function esc(v) {
    return String(v === null || v === undefined ? '' : v).replace(
      /[&<>"']/g,
      function (c) {
        return {
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        }[c];
      },
    );
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '');
    } catch (e) {
      return 'source';
    }
  }

  function fmtFull(n) {
    if (n === null || n === undefined) return '—';
    return Math.round(n).toLocaleString();
  }

  function legend() {
    var el = document.getElementById('tg-legend');
    el.innerHTML = '';
    STOPS.forEach(function (s, i) {
      var box = document.createElement('span');
      box.className = 'inline-flex items-center gap-1';
      var sw = document.createElement('span');
      sw.className = 'inline-block w-3 h-3 rounded-sm';
      sw.style.background = s[1];
      var lbl = document.createElement('span');
      lbl.textContent = i === STOPS.length - 1 ? s[0] + '+' : s[0];
      box.appendChild(sw);
      box.appendChild(lbl);
      el.appendChild(box);
    });
  }

  function colorExpression() {
    var expr = [
      'interpolate',
      ['linear'],
      ['coalesce', ['get', TG.indicator], -1],
    ];
    STOPS.forEach(function (s) {
      expr.push(s[0], s[1]);
    });
    return [
      'case',
      ['==', ['coalesce', ['get', TG.indicator], -1], -1],
      '#e7e5e4',
      expr,
    ];
  }

  function initMap() {
    if (!window.MAPBOX_TOKEN) {
      document.getElementById('tg-map').innerHTML =
        '<div class="flex items-center justify-center h-full text-stone-500 text-sm">' +
        'Map unavailable — MAPBOX_TOKEN is not configured. The table and download still work.</div>';
      return Promise.resolve();
    }
    mapboxgl.accessToken = window.MAPBOX_TOKEN;
    map = new mapboxgl.Map({
      container: 'tg-map',
      style: 'mapbox://styles/mapbox/light-v11',
      // Mapbox v3 defaults to a globe at low zoom, which renders Africa as a
      // patch on a sphere alongside South America. This is a thematic map of
      // one continent, so a flat projection is the honest frame.
      projection: 'mercator',
      bounds: AFRICA_BOUNDS,
      fitBoundsOptions: { padding: 20 },
    });
    map.addControl(new mapboxgl.NavigationControl(), 'top-right');

    return new Promise(function (resolve) {
      map.on('load', resolve);
    });
  }

  function paint() {
    if (!map || !geojson) return;

    if (!map.getSource('areas')) {
      map.addSource('areas', {
        type: 'geojson',
        data: geojson,
        promoteId: 'pk',
      });
      map.addLayer({
        id: 'areas-fill',
        type: 'fill',
        source: 'areas',
        paint: { 'fill-color': colorExpression(), 'fill-opacity': 0.85 },
      });
      map.addLayer({
        id: 'areas-line',
        type: 'line',
        source: 'areas',
        paint: {
          'line-color': [
            'case',
            ['boolean', ['feature-state', 'above'], false],
            '#0b3d39',
            '#ffffff',
          ],
          'line-width': [
            'case',
            ['boolean', ['feature-state', 'above'], false],
            2,
            0.4,
          ],
        },
      });
      // Knock back below-threshold areas just enough to make the selection
      // read, but not so far that the mortality ramp the legend advertises
      // becomes invisible. At 0.55 the whole continent washed out to near-white
      // and the choropleth stopped saying anything.
      map.addLayer({
        id: 'areas-dim',
        type: 'fill',
        source: 'areas',
        paint: {
          'fill-color': '#f5f5f4',
          'fill-opacity': [
            'case',
            ['boolean', ['feature-state', 'above'], false],
            0,
            0.3,
          ],
        },
      });
      wireTooltip();
    }
  }

  function wireTooltip() {
    var popup = new mapboxgl.Popup({ closeButton: false, closeOnClick: false });
    map.on('mousemove', 'areas-fill', function (e) {
      var p = e.features[0].properties;
      map.getCanvas().style.cursor = 'pointer';
      var rate = p[TG.indicator];
      var inherited = p.inherited === true || p.inherited === 'true';
      popup
        .setLngLat(e.lngLat)
        .setHTML(
          '<div style="font:13px/1.4 system-ui;min-width:180px">' +
            '<div style="font-weight:600">' +
            p.name +
            '</div>' +
            '<div style="color:#57534e">' +
            p.country +
            '</div>' +
            '<hr style="margin:6px 0;border:0;border-top:1px solid #e7e5e4">' +
            '<div>U5MR: <b>' +
            (rate === null || rate === 'null' ? 'no data' : rate) +
            '</b> per 1,000</div>' +
            '<div>Births/yr: <b>' +
            fmtFull(p.births === 'null' ? null : p.births) +
            '</b></div>' +
            '<div>Under-5: <b>' +
            fmtFull(p.pop_u5 === 'null' ? null : p.pop_u5) +
            '</b></div>' +
            '<div style="color:#78716c;margin-top:4px;font-size:11px">' +
            (p.source || 'no source') +
            (inherited ? ' · national figure applied here' : '') +
            '</div>' +
            '</div>',
        )
        .addTo(map);
    });
    map.on('mouseleave', 'areas-fill', function () {
      map.getCanvas().style.cursor = '';
      popup.remove();
    });
  }

  function applySelection(pks) {
    if (!map || !map.getSource('areas')) return;
    selected.forEach(function (pk) {
      map.setFeatureState({ source: 'areas', id: pk }, { above: false });
    });
    selected = new Set(pks);
    selected.forEach(function (pk) {
      map.setFeatureState({ source: 'areas', id: pk }, { above: true });
    });
  }

  function renderTable(data) {
    var tbody = document.getElementById('tg-rows');
    tbody.innerHTML = '';

    if (!data.rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="8" class="px-5 py-8 text-center text-stone-400">' +
        'No area is above this threshold.</td></tr>';
      return;
    }

    data.rows.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.className = 'hover:bg-stone-50';

      var scope = r.whole_country
        ? '<span class="ml-2 text-xs bg-teal-50 text-teal-800 px-1.5 py-0.5 rounded">whole country · ' +
          r.units_covered +
          ' regions</span>'
        : '';

      // Source, year and link are three separate columns: "NG2024DHS" in one
      // cell told a reader nothing and led nowhere.
      var sourceCell = esc(r.source_name || '—');
      if (r.source_detail) {
        sourceCell =
          '<span title="' +
          esc(r.source_detail) +
          '">' +
          sourceCell +
          '</span>';
      }
      if (r.adjusted) {
        sourceCell +=
          '<span class="block text-xs text-teal-700" title="' +
          esc(r.adjusted_note) +
          '">re-levelled to today</span>';
      }
      if (r.inherited) {
        sourceCell +=
          '<span class="block text-xs text-amber-700">national figure, from ' +
          esc(r.measured_at) +
          '</span>';
      }

      var linkCell = r.source_url
        ? '<a href="' +
          esc(r.source_url) +
          '" target="_blank" rel="noopener noreferrer" ' +
          'class="text-teal-700 hover:underline whitespace-nowrap">' +
          esc(hostOf(r.source_url)) +
          ' \u2197</a>'
        : '<span class="text-stone-300">—</span>';

      var birthsCell =
        r.births === null
          ? '<span class="text-stone-400" title="No births estimate for this area">—</span>'
          : fmtFull(r.births) +
            (r.births_partial
              ? '<span class="block text-xs text-amber-700">partial</span>'
              : '');

      tr.innerHTML =
        '<td class="px-5 py-2 font-medium text-stone-900">' +
        r.name +
        scope +
        '</td>' +
        '<td class="px-3 py-2 text-stone-600">' +
        r.country +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num">' +
        (r.value === null ? '—' : r.value) +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num font-medium">' +
        birthsCell +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num text-stone-600">' +
        fmtFull(r.pop_u5) +
        '</td>' +
        '<td class="px-3 py-2 text-xs text-stone-600">' +
        sourceCell +
        '</td>' +
        '<td class="px-3 py-2 text-xs text-right tg-num ' +
        (r.year && new Date().getFullYear() - r.year >= 8
          ? 'text-amber-700 font-medium'
          : 'text-stone-600') +
        '" title="Year the underlying survey was carried out">' +
        (r.year || '—') +
        '</td>' +
        '<td class="px-5 py-2 text-xs">' +
        linkCell +
        '</td>';
      tbody.appendChild(tr);
    });
  }

  function renderHeadline(data) {
    document.getElementById('tg-births').textContent = fmt(data.totals.births);
    document.getElementById('tg-popu5').textContent = fmt(data.totals.pop_u5);
    document.getElementById('tg-poptotal').textContent = fmt(
      data.totals.pop_total,
    );

    var c = data.counts;
    document.getElementById('tg-scope').textContent =
      fmtFull(data.totals.births) +
      ' births across ' +
      c.units +
      ' region' +
      (c.units === 1 ? '' : 's') +
      ' in ' +
      c.countries +
      ' countr' +
      (c.countries === 1 ? 'y' : 'ies');

    document.getElementById('tg-rowcount').textContent =
      c.rows +
      ' row' +
      (c.rows === 1 ? '' : 's') +
      ' · ' +
      c.units +
      ' underlying regions';

    // The floor caveat belongs beside the number it qualifies, not at the foot
    // of the page under a 150-row table.
    var floorEl = document.getElementById('tg-floor');
    var cov = (data.coverage || {}).births;
    if (cov && cov.of && cov.with_value < cov.of) {
      floorEl.innerHTML =
        '<strong>A floor, not a total.</strong> ' +
        (cov.of - cov.with_value) +
        ' of ' +
        cov.of +
        ' regions have no births estimate yet and contribute nothing here.';
      floorEl.classList.remove('hidden');
    } else {
      floorEl.classList.add('hidden');
      floorEl.innerHTML = '';
    }

    var gaps = [];
    if (data.countries_unsupported && data.countries_unsupported.length) {
      gaps.push(
        '<strong>Cannot answer with this method:</strong> ' +
          data.countries_unsupported.join(', ') +
          '. Left out rather than answered at a different level.',
      );
    }
    if (data.countries_fully_above.length) {
      gaps.push(
        '<strong>Entirely above threshold:</strong> ' +
          data.countries_fully_above.join(', '),
      );
    }
    if (data.skipped_no_data.length) {
      gaps.push(
        '<strong>No mortality data, excluded:</strong> ' +
          data.skipped_no_data.join(', '),
      );
    }
    document.getElementById('tg-gaps').innerHTML = gaps.join('<br>');
  }

  // --- method + resolution picker -----------------------------------------

  function methodsFor(resolution) {
    return (methodInfo.resolutions[resolution] || []).map(function (code) {
      return Object.assign({ code: code }, methodInfo.methods[code]);
    });
  }

  function currentResolution() {
    return methodInfo.methods[currentMethod].resolution;
  }

  function renderResolutionToggle() {
    var el = document.getElementById('tg-resolution');
    el.innerHTML = '';
    Object.keys(methodInfo.resolutions).forEach(function (res) {
      if (!methodInfo.resolutions[res].length) return;
      var active = res === currentResolution();
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = res === 'national' ? 'National' : 'Subnational';
      b.className =
        'flex-1 text-sm rounded-md py-1.5 transition ' +
        (active
          ? 'bg-white shadow-sm font-medium text-stone-900'
          : 'text-stone-600 hover:text-stone-900');
      b.onclick = function () {
        // Switching level picks that level's default method rather than
        // trying to carry one across — a national method has no subnational
        // equivalent, and pretending otherwise is how you get a national
        // number painted onto regions.
        var candidates = methodsFor(res);
        if (!candidates.length) return;
        var pick =
          candidates.filter(function (m) {
            return m.default;
          })[0] || candidates[0];
        currentMethod = pick.code;
        renderPicker();
        reload();
      };
      el.appendChild(b);
    });
  }

  function renderMethodSelect() {
    var sel = document.getElementById('tg-method');
    sel.innerHTML = '';
    methodsFor(currentResolution()).forEach(function (m) {
      var o = document.createElement('option');
      o.value = m.code;
      o.textContent = m.label;
      o.selected = m.code === currentMethod;
      sel.appendChild(o);
    });
    sel.onchange = function () {
      currentMethod = sel.value;
      renderPicker();
      reload();
    };
  }

  function renderMethodNotes() {
    var m = methodInfo.methods[currentMethod];
    document.getElementById('tg-method-desc').textContent = m.description || '';
    document.getElementById('tg-method-caveat').textContent = m.caveat || '';

    var cover = document.getElementById('tg-method-cover');
    var missing = m.countries_total - m.countries_available;
    var text =
      'Available for ' +
      m.countries_available +
      ' of ' +
      m.countries_total +
      ' countries';
    if (missing > 0) {
      var names = m.unavailable.slice(0, 4).map(function (c) {
        return c.name;
      });
      text +=
        ' — ' +
        missing +
        ' cannot answer with this method (' +
        names.join(', ') +
        (missing > names.length ? ', …' : '') +
        '). They are left out rather than answered at another level.';
    }
    cover.textContent = text;
  }

  function renderPicker() {
    renderResolutionToggle();
    renderMethodSelect();
    renderMethodNotes();
  }

  function reload() {
    updateThresholdLabels();
    geojson = null;
    if (map && map.getSource('areas')) {
      map.removeLayer('areas-dim');
      map.removeLayer('areas-line');
      map.removeLayer('areas-fill');
      map.removeSource('areas');
      selected = new Set();
    }
    return fetch(
      TG.urls.map + '?indicator=' + TG.indicator + '&method=' + currentMethod,
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        geojson = data;
        paint();
        return fetchSelection();
      });
  }

  function threshold() {
    return parseFloat(document.getElementById('tg-threshold').value);
  }

  function updateThresholdLabels() {
    var t = threshold();
    document.getElementById('tg-threshold-pct').textContent =
      (t / 10).toFixed(1) + '%';
    document.getElementById('tg-threshold-abs').textContent = t;
    document.getElementById('tg-download').href =
      TG.urls.download +
      '?indicator=' +
      TG.indicator +
      '&threshold=' +
      t +
      (currentMethod ? '&method=' + currentMethod : '');
  }

  function fetchSelection() {
    var t = threshold();
    document.getElementById('tg-births').textContent = '…';
    return fetch(
      TG.urls.selection +
        '?indicator=' +
        TG.indicator +
        '&threshold=' +
        t +
        (currentMethod ? '&method=' + currentMethod : ''),
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        renderHeadline(data);
        renderTable(data);
        applySelection(data.selected_pks);
      })
      .catch(function (err) {
        document.getElementById('tg-births').textContent = 'error';
        document.getElementById('tg-rows').innerHTML =
          '<tr><td colspan="6" class="px-5 py-8 text-center text-red-600">' +
          'Could not load the selection: ' +
          err +
          '</td></tr>';
      });
  }

  function onSlide() {
    updateThresholdLabels();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(fetchSelection, 250);
  }

  document.addEventListener('DOMContentLoaded', function () {
    legend();
    updateThresholdLabels();
    document.getElementById('tg-threshold').addEventListener('input', onSlide);

    // Methods first: the picker decides which sources answer and at what
    // level, so the map and table must not load before it is known.
    fetch(TG.urls.methods + '?indicator=' + TG.indicator)
      .then(function (r) {
        return r.json();
      })
      .then(function (info) {
        methodInfo = info;
        currentMethod = TG.defaultMethod || info.default;
        renderPicker();
        return initMap();
      })
      .then(function () {
        return reload();
      })
      .catch(function (err) {
        console.error('targeting: load failed', err);
        fetchSelection();
      });
  });
})();
