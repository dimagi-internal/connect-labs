/* The choropleth ramp, derived from the indicator rather than fixed.

   The ramp used to be six hard-coded stops at 0/25/50/75/100/150 — the
   conventional reporting breaks for under-5 mortality, which is what this page
   showed when it had eleven indicators. It now has fifty-two, and the fixed
   ramp was wrong for almost all of them: malaria incidence runs to 800 and
   rendered as one flat block of the darkest colour, severe wasting tops out at
   15 and rendered as one flat block of the palest. In both cases the map
   showed no variation at all while the data underneath was full of it.

   Each measure already declares the range it is read over — threshold_min and
   threshold_max, which the slider uses. The ramp reads the same declaration,
   so a new indicator gets a sensible map without anyone choosing stops. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};

  var RAMP = ['#f0f9f8', '#c3e5e1', '#8fcac4', '#57a9a2', '#2f867f', '#14554f'];

  // A round number a reader can hold: 1 / 2 / 5 x a power of ten.
  /* The smallest round step that still covers the whole range.

     This used to round DOWN to 1/2/5, which makes a tidy number and a ramp
     too short to reach the data: under-5 mortality runs to 200, and a step of
     20 across six colours stopped at 100 — so every region between 100 and
     200, the worst half of the measure, was painted the same darkest shade.
     That is the flat-block failure the derived ramp was introduced to fix,
     surviving at the top end.

     Rounding up costs at most one wasted band and guarantees the last stop
     sits at or above the maximum. */
  function niceStep(span, count) {
    var raw = span / count;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / mag;
    var step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
    return step * mag;
  }

  function stopsFor(meta) {
    var lo = typeof meta.threshold_min === 'number' ? meta.threshold_min : 0;
    var hi = typeof meta.threshold_max === 'number' ? meta.threshold_max : 100;
    if (!(hi > lo)) {
      hi = lo + 100;
    }
    var step = niceStep(hi - lo, RAMP.length - 1);
    var start = Math.floor(lo / step) * step;
    var stops = [];
    for (var i = 0; i < RAMP.length; i++) {
      var v = start + step * i;
      // Never repeat a stop value: mapbox's interpolate requires strictly
      // ascending inputs and throws on a duplicate, which would blank the map.
      if (i > 0 && v <= stops[i - 1][0]) v = stops[i - 1][0] + step;
      stops.push([v, RAMP[i]]);
    }
    return stops;
  }

  // A burden is worse when high, so dark should mean high. A coverage measure
  // is worse when LOW, so dark must mean low or the map paints the places
  // already doing well as the ones that need help.
  function colorExpression(indicator, meta) {
    var stops = stopsFor(meta);
    var colors = meta.lower_is_worse ? RAMP.slice().reverse() : RAMP.slice();
    var expr = [
      'interpolate',
      ['linear'],
      ['coalesce', ['get', indicator], -1],
    ];
    stops.forEach(function (s, i) {
      expr.push(s[0], colors[i]);
    });
    return [
      'case',
      ['==', ['coalesce', ['get', indicator], -1], -1],
      '#e7e5e4',
      expr,
    ];
  }

  function legendItems(meta) {
    var stops = stopsFor(meta);
    var colors = meta.lower_is_worse ? RAMP.slice().reverse() : RAMP.slice();
    return stops.map(function (s, i) {
      var round = Math.abs(s[0]) >= 10 ? 0 : 1;
      return {
        value: s[0].toFixed(round),
        color: colors[i],
        last: i === stops.length - 1,
      };
    });
  }

  window.Targeting.scale = {
    stopsFor: stopsFor,
    colorExpression: colorExpression,
    legendItems: legendItems,
    RAMP: RAMP,
  };
})();
