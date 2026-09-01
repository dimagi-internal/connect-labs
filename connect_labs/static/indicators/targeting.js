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
  var currentIndicator = null;
  var costInfo = null;
  var currentBasis = null;
  var indicatorMeta = {};
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
    if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
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
      ['coalesce', ['get', currentIndicator], -1],
    ];
    STOPS.forEach(function (s) {
      expr.push(s[0], s[1]);
    });
    return [
      'case',
      ['==', ['coalesce', ['get', currentIndicator], -1], -1],
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
      var rate = p[currentIndicator];
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

  // Which burden count belongs beside this indicator: deaths for mortality,
  // untreated children for a diarrhoea/ORS view.
  // Which burden count belongs beside the chosen indicator: untreated children
  // for a diarrhoea/ORS view, the unreached population for any other coverage
  // measure, expected deaths for mortality.
  var lastData = null;

  function burdenIsOrs() {
    return (
      currentIndicator === 'diarrhoea_prevalence' ||
      currentIndicator === 'ors_coverage'
    );
  }

  function burdenLabel() {
    if (burdenIsOrs()) return 'Children with untreated diarrhoea';
    if (lastData && lastData.gap_label) return lastData.gap_label;
    return 'Expected under-5 deaths / year';
  }

  function burdenHeader() {
    if (burdenIsOrs()) return 'Untreated/now';
    if (lastData && lastData.gap_label) return 'Unreached';
    return 'Deaths/yr';
  }

  function burdenOf(r) {
    if (burdenIsOrs()) return r.ors_gap_children;
    if (r.gap !== null && r.gap !== undefined) return r.gap;
    return r.expected_deaths;
  }

  function renderTable(data) {
    var th = document.getElementById('th-burden');
    if (th) th.textContent = burdenHeader();

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
        (r.ci_low !== null && r.ci_low !== undefined
          ? '<span class="block text-xs ' +
            (r.straddles_threshold ? 'text-amber-700' : 'text-stone-400') +
            '" title="' +
            (r.straddles_threshold
              ? 'This interval spans the threshold — inclusion is within uncertainty'
              : 'Published confidence interval') +
            '">' +
            r.ci_low +
            '–' +
            r.ci_high +
            (r.straddles_threshold ? ' ?' : '') +
            '</span>'
          : '') +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num font-medium">' +
        (burdenOf(r) === null
          ? '<span class="text-stone-400">—</span>'
          : fmtFull(burdenOf(r))) +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num text-stone-600">' +
        birthsCell +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num text-stone-600">' +
        fmtFull(r.pop_u5) +
        '</td>' +
        '<td class="px-3 py-2 text-xs text-stone-600">' +
        sourceCell +
        '</td>' +
        '<td class="px-3 py-2 text-xs text-stone-600">' +
        esc(r.method_label || '—') +
        (r.logic
          ? '<span class="block text-xs text-stone-400 mt-0.5">' +
            esc(r.logic) +
            '</span>'
          : '') +
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
    lastData = data;
    document.getElementById('tg-births').textContent = fmt(data.totals.births);
    var burdenTotal = burdenIsOrs()
      ? data.totals.ors_gap_children
      : data.totals.gap !== null && data.totals.gap !== undefined
      ? data.totals.gap
      : data.totals.expected_deaths;
    document.getElementById('tg-deaths').textContent = fmt(burdenTotal);
    document.getElementById('tg-burden-label').textContent = burdenLabel();
    document.getElementById('tg-popu5').textContent = fmt(data.totals.pop_u5);
    document.getElementById('tg-poptotal').textContent = fmt(
      data.totals.pop_total,
    );

    var c = data.counts;
    // Neutral now that births is one card among four rather than the headline.
    document.getElementById('tg-scope').textContent =
      c.units +
      ' region' +
      (c.units === 1 ? '' : 's') +
      ' selected across ' +
      c.countries +
      ' countr' +
      (c.countries === 1 ? 'y' : 'ies') +
      (c.rows !== c.units ? ' (' + c.rows + ' rows after rollup)' : '');

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

    // A rate inherits downward: a region with no value of its own takes its
    // country's. That is legitimate and each row says so, but the totals above
    // are then a mixture of regions measured in their own right and regions
    // carrying a national figure — and only a count says how much of each.
    var offEl = document.getElementById('tg-offmethod');
    if (c.inherited_units && c.units) {
      offEl.innerHTML =
        '<strong>' +
        c.inherited_units +
        ' of ' +
        c.units +
        ' regions</strong> carry a figure measured somewhere coarser — usually ' +
        'their country — because they have no value of their own. ' +
        'The Method column names what produced each row.';
      offEl.classList.remove('hidden');
    } else {
      offEl.classList.add('hidden');
      offEl.innerHTML = '';
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

    // The full availability sentence is read-once and lives in the popover; the
    // count itself is per-query and stays visible, because a method that answers
    // no country is the whole explanation for an empty map.
    var chip = document.getElementById('tg-method-chip');
    if (chip) {
      chip.textContent =
        m.countries_available + '/' + m.countries_total + ' countries';
      chip.className =
        'ml-auto text-[11px] tg-num ' +
        (m.countries_available === 0
          ? 'text-amber-700 font-medium'
          : 'text-stone-500');
    }

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

  function renderIndicatorSelect() {
    var sel = document.getElementById('tg-indicator');
    sel.innerHTML = '';
    (methodInfo.indicators || []).forEach(function (i) {
      indicatorMeta[i.code] = i;
      var o = document.createElement('option');
      o.value = i.code;
      o.textContent = i.label;
      o.selected = i.code === currentIndicator;
      sel.appendChild(o);
    });
    sel.onchange = function () {
      currentIndicator = sel.value;
      applyThresholdScale();
      // Availability is per indicator as well as per method — IGME models
      // mortality, not diarrhoea — so the whole picker is rebuilt.
      return fetch(TG.urls.methods + '?indicator=' + currentIndicator)
        .then(function (r) {
          return r.json();
        })
        .then(function (info) {
          methodInfo = info;
          if (
            !methodInfo.methods[currentMethod] ||
            !methodInfo.methods[currentMethod].countries_available
          ) {
            var best = Object.keys(methodInfo.methods).filter(function (c) {
              return methodInfo.methods[c].countries_available > 0;
            });
            if (best.length) currentMethod = best[0];
          }
          renderPicker();
          return reload();
        });
    };
  }

  // Each measure carries its own slider range and starting point. Leaving an
  // 80-per-1,000 mortality threshold in place when switching to a percentage
  // indicator selects almost nothing and looks like missing data.
  function applyThresholdScale() {
    var meta = indicatorMeta[currentIndicator];
    if (!meta) return;
    var el = document.getElementById('tg-threshold');
    el.min = meta.threshold_min;
    el.max = meta.threshold_max;
    el.step = meta.per_1000 ? 5 : 1;
    el.value = meta.threshold_default;
    document.getElementById('tg-scale-min').textContent = meta.per_1000
      ? (meta.threshold_min / 10).toFixed(0) + '%'
      : meta.threshold_min + '%';
    document.getElementById('tg-scale-max').textContent = meta.per_1000
      ? (meta.threshold_max / 10).toFixed(0) + '%'
      : meta.threshold_max + '%';
  }

  function renderPicker() {
    renderIndicatorSelect();
    renderResolutionToggle();
    renderMethodSelect();
    renderMethodNotes();
    renderThresholdLabels();
  }

  // Thresholds mean different things per indicator, and the sentence is what
  // carries that. A burden measure is read "worse than X"; a coverage measure
  // is read "fewer than X% reached", selects the other way, and is really a
  // question about who is left out.
  function renderThresholdLabels() {
    var meta = indicatorMeta[currentIndicator] || {};
    var name = (meta.label || '').toLowerCase();
    var coverage = !!meta.lower_is_worse;

    document.getElementById('tg-threshold-label').innerHTML = coverage
      ? 'Show me where <strong class="font-semibold text-stone-900">fewer than</strong> this share of people have ' +
        esc(name)
      : 'Show me where ' +
        esc(name) +
        ' is <strong class="font-semibold text-stone-900">worse than</strong>';

    document.getElementById('tg-family').textContent = coverage
      ? 'Coverage measure — lower is worse, so this selects the places below the line.'
      : 'Burden measure — higher is worse, so this selects the places above the line.';
    var famChip = document.getElementById('tg-family-chip');
    if (famChip)
      famChip.textContent = coverage
        ? 'coverage · selects below'
        : 'burden · selects above';

    // The unit caption sits under the number and must be the indicator's own
    // unit. It is not always a percentage, and it is never a percentage of
    // live births unless the measure actually is.
    document.getElementById('tg-threshold-unit').textContent = meta.per_1000
      ? meta.unit || 'per 1,000 live births'
      : coverage
      ? (meta.unit || '').replace(/^%\s*of\s*/, 'of the ') + ' reached'
      : meta.unit || '';

    document.getElementById('tg-legend-title').textContent =
      (meta.label || '') + (meta.unit ? ' (' + meta.unit + ')' : '');
    document.getElementById('tg-legend-selected').textContent = coverage
      ? 'below threshold'
      : 'above threshold';
    document.getElementById('tg-table-title').textContent = coverage
      ? 'Areas below threshold'
      : 'Areas above threshold';
    var thValue = document.getElementById('th-value');
    if (thValue) thValue.textContent = meta.short_label || meta.label || '';
    document.getElementById('tg-subtitle').textContent = coverage
      ? 'Where ' +
        name +
        ' is lowest across Africa, and how many people that leaves unreached.'
      : 'Where ' +
        name +
        ' is highest across Africa, and the population living there.';

    // The headline number depends on this metadata too, and the first paint
    // happens before the methods call returns — without this it renders the
    // fallback ("80%") and never corrects itself.
    updateThresholdLabels();
  }

  // The workings, on the page. Same endpoint, same function, same text the
  // download ships as METHODOLOGY.md — a page that paraphrased its own
  // methodology would be free to drift from the file a funder was sent.
  var methodologyToken = 0;

  function loadMethodology() {
    var mine = ++methodologyToken;
    var el = document.getElementById('tg-methodology');
    if (!el) return;
    var params =
      '?indicator=' +
      encodeURIComponent(currentIndicator) +
      '&threshold=' +
      threshold() +
      '&resolution=' +
      encodeURIComponent(currentResolution()) +
      (currentMethod ? '&method=' + encodeURIComponent(currentMethod) : '');

    fetch(TG.urls.methodology + params)
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        // A slower earlier request must not overwrite a newer answer.
        if (mine !== methodologyToken) return;
        el.innerHTML = d.html || '';
      })
      .catch(function () {
        if (mine !== methodologyToken) return;
        el.innerHTML =
          '<p class="text-amber-700">The workings could not be loaded. ' +
          'The download still carries them.</p>';
      });
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
      TG.urls.map +
        '?indicator=' +
        currentIndicator +
        '&method=' +
        currentMethod,
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        geojson = data;
        paint();
        loadMethodology();
        return fetchSelection();
      });
  }

  // --- costing --------------------------------------------------------------

  function renderCostControls() {
    var presetSel = document.getElementById('tg-preset');
    var basisSel = document.getElementById('tg-basis');
    if (!costInfo) return;

    presetSel.innerHTML = '<option value="">— choose your own —</option>';
    costInfo.interventions.forEach(function (i) {
      var o = document.createElement('option');
      o.value = i.slug;
      o.textContent =
        i.label + ' ($' + i.unit_cost_usd.toFixed(2) + ' ' + i.basis + ')';
      presetSel.appendChild(o);
    });

    basisSel.innerHTML = '';
    costInfo.bases.forEach(function (b) {
      var o = document.createElement('option');
      o.value = b.code;
      o.textContent = b.label;
      // A basis with no count for this indicator is offered but disabled, so
      // its absence is visible rather than mysterious.
      o.disabled = !b.available_for_indicator;
      if (o.disabled) o.textContent += ' — not available for this indicator';
      o.selected = b.code === currentBasis;
      basisSel.appendChild(o);
    });

    presetSel.onchange = function () {
      var pick = costInfo.interventions.filter(function (i) {
        return i.slug === presetSel.value;
      })[0];
      if (!pick) return fetchScenario();
      currentBasis = pick.basis;
      document.getElementById('tg-unitcost').value = pick.unit_cost_usd;
      basisSel.value = currentBasis;

      // A preset carries the indicator it is meant for. Without this, picking
      // "ORS" while targeting mortality silently costs expected deaths rather
      // than untreated diarrhoea — the right arithmetic on the wrong quantity.
      if (pick.targets && pick.targets !== currentIndicator) {
        var indSel = document.getElementById('tg-indicator');
        indSel.value = pick.targets;
        return indSel.onchange();
      }
      return fetchScenario();
    };
    basisSel.onchange = function () {
      currentBasis = basisSel.value;
      presetSel.value = '';
      return fetchScenario();
    };
    document.getElementById('tg-unitcost').oninput = function () {
      clearTimeout(window.__costT);
      window.__costT = setTimeout(fetchScenario, 350);
    };
  }

  function fetchScenario() {
    var cost = parseFloat(document.getElementById('tg-unitcost').value);
    if (!currentBasis || isNaN(cost)) return Promise.resolve();
    return fetch(
      TG.urls.scenario +
        '?indicator=' +
        currentIndicator +
        '&threshold=' +
        threshold() +
        '&basis=' +
        currentBasis +
        '&unit_cost=' +
        cost +
        (currentMethod ? '&method=' + currentMethod : ''),
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        var el = document.getElementById('tg-absorb');
        var detail = document.getElementById('tg-absorb-detail');
        var caveat = document.getElementById('tg-absorb-caveat');
        if (d.error) {
          el.textContent = '—';
          detail.textContent = d.error;
          caveat.textContent = '';
          return;
        }
        el.textContent =
          d.absorbable_usd === null ? '—' : '$' + fmt(d.absorbable_usd);
        detail.textContent =
          fmtFull(d.units) +
          ' ' +
          d.basis.noun_plural +
          ' — ' +
          d.basis.measure_label;
        var notes = [];
        if (!d.complete) {
          notes.push(
            'A floor: ' +
              (d.unit_coverage.of - d.unit_coverage.with_value) +
              ' of ' +
              d.unit_coverage.of +
              ' regions have no count.',
          );
        }
        if (d.intervention && d.intervention.caveat)
          notes.push(d.intervention.caveat);
        caveat.textContent = notes.join(' ');
      });
  }

  function threshold() {
    return parseFloat(document.getElementById('tg-threshold').value);
  }

  function updateThresholdLabels() {
    var t = threshold();
    var meta = indicatorMeta[currentIndicator] || {};

    // The headline is the threshold in the indicator's OWN unit. Dividing
    // every threshold by ten assumed per-1,000 and made a 50% sanitation
    // threshold read as 5.0%, which is where "the slider is still a
    // percentage" came from.
    document.getElementById('tg-threshold-pct').textContent = meta.per_1000
      ? String(t)
      : t + '%';

    // A per-1,000 rate has a second, more legible reading. A measure already
    // in percent has none, so the line goes away rather than inventing one.
    var alt = document.getElementById('tg-threshold-alt');
    if (meta.per_1000) {
      alt.style.display = '';
      document.getElementById('tg-threshold-abs').textContent =
        (t / 10).toFixed(1) + '% of children die before five';
    } else {
      alt.style.display = 'none';
      document.getElementById('tg-threshold-abs').textContent = '';
    }
    document.getElementById('tg-download-md').href =
      TG.urls.download +
      '?indicator=' +
      currentIndicator +
      '&threshold=' +
      t +
      (currentMethod ? '&method=' + currentMethod : '');
    document.getElementById('tg-download').href =
      TG.urls.download +
      '?indicator=' +
      currentIndicator +
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
        currentIndicator +
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
        fetchScenario();
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
    debounceTimer = setTimeout(function () {
      fetchSelection();
      // The workings quote the threshold, so they go stale with it.
      loadMethodology();
    }, 250);
  }

  // One info dot per read-once explanation. Everything a person needs on every
  // query stays on the panel; everything they need once sits behind the dot, so
  // the controls stay dense without the page becoming unexplained.
  function closePopovers(except) {
    document.querySelectorAll('.tg-pop').forEach(function (pop) {
      if (pop === except) return;
      pop.classList.add('hidden');
    });
    document.querySelectorAll('.tg-info').forEach(function (btn) {
      var pop = document.getElementById(btn.getAttribute('data-pop'));
      btn.setAttribute(
        'aria-expanded',
        pop && !pop.classList.contains('hidden') ? 'true' : 'false',
      );
    });
  }

  function initPopovers() {
    document.querySelectorAll('.tg-info').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var pop = document.getElementById(btn.getAttribute('data-pop'));
        if (!pop) return;
        var willOpen = pop.classList.contains('hidden');
        closePopovers();
        if (willOpen) {
          pop.classList.remove('hidden');
          btn.setAttribute('aria-expanded', 'true');
        }
      });
    });
    // Clicking anywhere else, or Escape, dismisses — a popover that traps the
    // page is worse than the paragraph it replaced.
    document.addEventListener('click', function (ev) {
      if (!ev.target.closest || !ev.target.closest('.tg-pop')) closePopovers();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closePopovers();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initPopovers();
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
        currentIndicator = TG.indicator;
        renderIndicatorSelect();
        applyThresholdScale();
        renderPicker();
        return initMap();
      })
      .then(function () {
        return fetch(TG.urls.interventions + '?indicator=' + currentIndicator);
      })
      .then(function (r) {
        return r.json();
      })
      .then(function (info) {
        costInfo = info;
        var preset = info.interventions[0];
        currentBasis = preset ? preset.basis : 'person';
        document.getElementById('tg-unitcost').value = preset
          ? preset.unit_cost_usd
          : 1;
        renderCostControls();
        return reload();
      })
      .catch(function (err) {
        console.error('targeting: load failed', err);
        fetchSelection();
      });
  });
})();
