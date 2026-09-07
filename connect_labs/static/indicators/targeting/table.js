/* The headline tiles and the area table. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var util = T.util;
  var state = T.state;
  var lastData = null;

  function burdenIsOrs() {
    var S = state.get();
    return (
      S.indicator === 'diarrhoea_prevalence' || S.indicator === 'ors_coverage'
    );
  }

  var MORTALITY = ['u5mr', 'nmr', 'imr'];

  /* Does this indicator imply a fundable count at all?

     A coverage measure has an unreached count; mortality has expected deaths.
     A burden measure like stunting, open defecation or unmet need for family
     planning has neither — and the emphasised tile fell back to "Expected
     under-5 deaths / year" for every one of them. That is not a fallback, it
     is a different subject: a selection on anaemia in women 15-49 headlined
     1.7M under-5 deaths. Roughly fifteen of the fifty-two indicators were
     affected. */
  function hasBurdenCount() {
    if (burdenIsOrs()) return true;
    if (lastData && lastData.gap_label) return true;
    return MORTALITY.indexOf(state.get().indicator) !== -1;
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
        '<tr><td colspan="10" class="px-5 py-8 text-center text-stone-400">' +
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
      var sourceCell = util.esc(r.source_name || '—');
      if (r.source_detail) {
        sourceCell =
          '<span title="' +
          util.esc(r.source_detail) +
          '">' +
          sourceCell +
          '</span>';
      }
      if (r.adjusted) {
        sourceCell +=
          '<span class="block text-xs text-teal-700" title="' +
          util.esc(r.adjusted_note) +
          '">re-levelled to today</span>';
      }
      if (r.inherited) {
        sourceCell +=
          '<span class="block text-xs text-amber-700">national figure, from ' +
          util.esc(r.measured_at) +
          '</span>';
      }

      var linkCell = r.source_url
        ? '<a href="' +
          util.esc(r.source_url) +
          '" target="_blank" rel="noopener noreferrer" ' +
          'class="text-teal-700 hover:underline whitespace-nowrap">' +
          util.esc(util.hostOf(r.source_url)) +
          ' \u2197</a>'
        : '<span class="text-stone-300">—</span>';

      var birthsCell =
        r.births === null
          ? '<span class="text-stone-400" title="No births estimate for this area">—</span>'
          : util.fmtFull(r.births) +
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
        (r.small_sample
          ? '<span class="block text-xs text-amber-700" title="Fewer than 50 ' +
            'surveyed cases behind this figure">' +
            (r.sample ? 'n=' + r.sample : 'small sample') +
            '</span>'
          : '') +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num font-medium">' +
        (burdenOf(r) === null
          ? '<span class="text-stone-400">—</span>'
          : util.fmtFull(burdenOf(r))) +
        (r.gap_annual !== null && r.gap_annual !== undefined
          ? '<span class="block text-xs text-stone-500" ' +
            'title="The same cases over a year, not one recall window">' +
            util.fmtFull(r.gap_annual) +
            '/yr</span>'
          : '') +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num text-stone-600">' +
        birthsCell +
        '</td>' +
        '<td class="px-3 py-2 text-right tg-num text-stone-600">' +
        util.fmtFull(r.pop_u5) +
        '</td>' +
        '<td class="px-3 py-2 text-xs text-stone-600">' +
        sourceCell +
        '</td>' +
        '<td class="px-3 py-2 text-xs text-stone-600">' +
        util.esc(r.method_label || '—') +
        (r.logic
          ? '<span class="block text-xs text-stone-400 mt-0.5">' +
            util.esc(r.logic) +
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
    document.getElementById('tg-births').textContent = util.fmt(
      data.totals.births,
    );
    var burdenTotal = burdenIsOrs()
      ? data.totals.ors_gap_children
      : data.totals.gap !== null && data.totals.gap !== undefined
      ? data.totals.gap
      : data.totals.expected_deaths;
    document.getElementById('tg-deaths').textContent = util.fmt(burdenTotal);
    document.getElementById('tg-burden-label').textContent = burdenLabel();

    // The twentyfold trap, stated rather than left to be assumed. A survey
    // asks about the last two weeks, so a prevalence-derived count is a
    // fortnight's worth; the annual sibling is a different number answering a
    // different question, and both belong on the face of the tile.
    var annualEl = document.getElementById('tg-burden-annual');
    if (
      data.totals.gap_annual !== null &&
      data.totals.gap_annual !== undefined
    ) {
      annualEl.textContent =
        util.fmt(data.totals.gap_annual) +
        ' over a year · the figure above is one two-week recall window';
      annualEl.classList.remove('hidden');
    } else {
      annualEl.classList.add('hidden');
      annualEl.textContent = '';
    }
    // The population this measure is about, named by the server. For ORS that
    // is under-fives; for unmet need it is married women 15-49, which a fixed
    // "Under-5 population" tile could never have shown.
    var denomEl = document.getElementById('tg-popu5');
    var denomLabel = document.getElementById('tg-popu5-label');
    var haveDenom =
      data.denominator_label &&
      data.totals.denominator !== null &&
      data.totals.denominator !== undefined;
    denomEl.textContent = util.fmt(
      haveDenom ? data.totals.denominator : data.totals.pop_u5,
    );
    if (denomLabel) {
      denomLabel.textContent = haveDenom
        ? data.denominator_label
        : 'Under-5 population';
    }

    // The emphasised tile is hidden rather than filled with a different
    // subject when this indicator implies no fundable count.
    // Inline style, not a utility class: Tailwind is built ahead of time and
    // purges classes it cannot see in the source, so a class only ever added
    // from JS ships with no rule behind it and silently does nothing.
    var burdenTile = document.getElementById('tg-burden-tile');
    if (burdenTile) {
      burdenTile.style.visibility = hasBurdenCount() ? '' : 'hidden';
    }
    document.getElementById('tg-poptotal').textContent = util.fmt(
      data.totals.pop_total,
    );

    var c = data.counts;
    // Neutral now that births is one card among four rather than the headline.
    // It names the scope because a reader arriving at a screenshot cannot
    // otherwise tell whether these are Africa's numbers or Liberia's.
    var where =
      data.scope && !data.scope.whole_continent
        ? data.scope.countries.join(', ')
        : null;
    document.getElementById('tg-scope').textContent =
      c.units +
      ' ' +
      util.plural(c.units, 'area') +
      ' selected' +
      (where
        ? ' in ' + where
        : ' across ' +
          c.countries +
          ' countr' +
          (c.countries === 1 ? 'y' : 'ies')) +
      (data.pinned_level !== null && data.pinned_level !== undefined
        ? ' at ADM' + data.pinned_level
        : '') +
      (c.rows !== c.units
        ? ' (' + c.rows + ' ' + util.plural(c.rows, 'row') + ' after rollup)'
        : '') +
      (c.small_sample_units
        ? ' · ' + c.small_sample_units + ' rest on fewer than 50 surveyed cases'
        : '');

    // A projected total is not a measured one, and the difference has to be on
    // the page beside the number, not only in the download.
    var projEl = document.getElementById('tg-projected');
    if (data.projected_to) {
      projEl.innerHTML =
        '<strong>Counts carried to ' +
        data.projected_to +
        '.</strong> Population, births and case counts are grown at each ' +
        "country's own rate; rates are left as measured, because nothing " +
        'here models how coverage moves.' +
        (data.projected_without_rate && data.projected_without_rate.length
          ? ' <span class="text-amber-700">No growth series for ' +
            data.projected_without_rate.join(', ') +
            ' — left at their own year, so on a different basis from the ' +
            'rest.</span>'
          : '');
      projEl.classList.remove('hidden');
    } else {
      projEl.classList.add('hidden');
      projEl.innerHTML = '';
    }

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
    // ANY count that is short, not just births. The warning existed to stop an
    // undercount being read as a total, and then only ever guarded one of the
    // counts: a handwashing selection with 371 of 386 units carrying a gap
    // showed its headline as though it were complete, because the missing
    // count was households rather than births.
    var short = Object.keys(data.coverage || {})
      .map(function (k) {
        return Object.assign({ code: k }, data.coverage[k]);
      })
      .filter(function (c) {
        return c.of && c.with_value < c.of;
      })
      // Worst first: the count furthest from complete is the one that most
      // undermines the headline.
      .sort(function (a, b) {
        return a.with_value / a.of - b.with_value / b.of;
      });
    var cov = short[0];
    if (cov && cov.of && cov.with_value < cov.of) {
      floorEl.innerHTML =
        '<strong>A floor, not a total.</strong> ' +
        (cov.of - cov.with_value) +
        ' of ' +
        cov.of +
        ' areas carry no <b>' +
        util.esc(cov.label || cov.code) +
        '</b> and contribute nothing to that figure.' +
        (short.length > 1
          ? ' (' +
            (short.length - 1) +
            ' other count' +
            util.plural(short.length - 1, '') +
            ' also short.)'
          : '');
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
    // A coverage measure is worse when LOW, so its selection is the places
    // below the line. Saying "entirely above threshold" of a country every one
    // of whose counties falls short inverts the finding.
    if (data.countries_fully_above.length) {
      gaps.push(
        '<strong>Entirely ' +
          (data.lower_is_worse ? 'below' : 'above') +
          ' threshold:</strong> ' +
          data.countries_fully_above.join(', '),
      );
    }
    if (data.skipped_no_data.length) {
      gaps.push(
        '<strong>No ' +
          (data.indicator_label || 'indicator').toLowerCase() +
          ' data, excluded:</strong> ' +
          data.skipped_no_data.join(', '),
      );
    }
    document.getElementById('tg-gaps').innerHTML = gaps.join('<br>');
  }

  window.Targeting.table = {
    render: function (data) {
      renderHeadline(data);
      renderTable(data);
    },
  };
})();
