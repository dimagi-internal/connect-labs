// The choropleth ramp and the state pipeline's invalidation table — the two
// pieces of real logic in the targeting front end.
import { describe, it, expect, beforeEach } from 'vitest';

function loadModule(path) {
  // These files attach to window rather than exporting: static assets go
  // through a hashing storage that does not rewrite ES import specifiers, so
  // the app cannot use modules. Tests load them the same way the page does.
  globalThis.window = globalThis.window || globalThis;
  const fs = require('fs');
  // eslint-disable-next-line no-eval
  eval(fs.readFileSync(new URL(path, import.meta.url), 'utf8'));
  return globalThis.window.Targeting;
}

describe('scale', () => {
  const T = loadModule('./scale.js');
  const scale = T.scale;

  it('spans the measure it is drawing, not under-5 mortality', () => {
    // The ramp used to be six hard-coded stops at 0/25/50/75/100/150 — the
    // reporting breaks for under-5 mortality. Malaria incidence runs to 800
    // and rendered as one flat block of the darkest colour.
    const stops = scale.stopsFor({ threshold_min: 10, threshold_max: 800 });
    expect(stops[stops.length - 1][0]).toBeGreaterThan(400);
  });

  it('does not collapse a narrow measure into one shade', () => {
    // Severe wasting tops out at 15 and rendered entirely in the palest.
    const stops = scale.stopsFor({ threshold_min: 0, threshold_max: 15 });
    expect(stops[stops.length - 1][0]).toBeLessThanOrEqual(30);
    expect(stops[stops.length - 1][0]).toBeGreaterThan(stops[0][0]);
  });

  it('always ascends strictly', () => {
    // mapbox's interpolate throws on a duplicate input and blanks the map.
    for (const hi of [1, 3, 15, 60, 100, 480, 800]) {
      const stops = scale.stopsFor({ threshold_min: 0, threshold_max: hi });
      for (let i = 1; i < stops.length; i++) {
        expect(stops[i][0]).toBeGreaterThan(stops[i - 1][0]);
      }
    }
  });

  it('darkens toward the bad end for a coverage measure', () => {
    // A coverage measure is worse when LOW. Reusing the burden ramp paints the
    // places already doing well as the ones that need help.
    const burden = scale.legendItems({ threshold_min: 0, threshold_max: 100 });
    const coverage = scale.legendItems({
      threshold_min: 0,
      threshold_max: 100,
      lower_is_worse: true,
    });
    expect(coverage[0].color).toBe(burden[burden.length - 1].color);
    expect(coverage[coverage.length - 1].color).toBe(burden[0].color);
  });

  it('falls back to a sane range when a measure declares none', () => {
    const stops = scale.stopsFor({});
    expect(stops).toHaveLength(6);
    expect(stops[0][0]).toBe(0);
  });
});

describe('state invalidation', () => {
  const T = loadModule('./state.js');
  const state = T.state;

  it('rollup and year cannot invalidate the map', () => {
    // They change which ROWS come back, never which shapes are drawn.
    // Refetching the continental GeoJSON for a checkbox was a wasted round
    // trip on every toggle.
    expect(state.INVALIDATES.rollup).toBe('selection');
    expect(state.INVALIDATES.year).toBe('selection');
    expect(state.INVALIDATES.threshold).toBe('selection');
  });

  it('an indicator change invalidates everything', () => {
    // A different indicator can be answered by different methods and measured
    // at a different depth.
    expect(state.INVALIDATES.indicator).toBe('methods');
    expect(state.CHANNELS[0]).toBe('methods');
  });

  it('orders channels coarse to fine', () => {
    expect(state.CHANNELS).toEqual([
      'methods',
      'scope',
      'map',
      'selection',
      'costing',
    ]);
  });

  it('every invalidation names a real channel', () => {
    Object.values(state.INVALIDATES).forEach((ch) => {
      expect(state.CHANNELS).toContain(ch);
    });
  });
});

describe('per-channel tickets', () => {
  const T = loadModule('./state.js');
  const state = T.state;

  it('gives every channel its own ticket', () => {
    // One global ticket meant any later apply cancelled any earlier one, even
    // when they touched nothing in common: nudging the threshold and then
    // editing the unit cost cancelled the selection fetch still in flight, and
    // the table went on showing the previous answer under the new slider
    // value. Fifteen counties where the truth was none, with no error.
    state.CHANNELS.forEach((c) => {
      const before = state.ticket(c);
      expect(typeof before).toBe('number');
      expect(state.isCurrent(c, before)).toBe(true);
    });
  });

  it('does not treat one channel’s ticket as another’s', () => {
    const sel = state.ticket('selection');
    const cost = state.ticket('costing');
    // Distinct counters. If these were the same variable, a costing run would
    // invalidate a selection run and vice versa — which is the bug.
    expect(state.isCurrent('selection', sel)).toBe(true);
    expect(state.isCurrent('costing', cost)).toBe(true);
  });
});

describe('the sentence an empty table shows', () => {
  // This text is the whole answer when nothing is selected, and a reader acts
  // on it. It has been wrong twice: it asserted "above" for coverage measures
  // that select below, and it reported a country we have no data for as a
  // country where nothing crossed the threshold — which reads as good news.
  function load(S) {
    globalThis.window = globalThis.window || globalThis;
    globalThis.window.Targeting = {
      util: { esc: (x) => x },
      state: { get: () => S },
    };
    const fs = require('fs');
    // eslint-disable-next-line no-eval
    eval(fs.readFileSync(new URL('./table.js', import.meta.url), 'utf8'));
    return globalThis.window.Targeting.table.emptyReason;
  }

  const withData = {
    method: 'subnational_survey',
    methodInfo: {
      methods: {
        subnational_survey: {
          countries: [
            { iso: 'NGA', name: 'Nigeria', available: true, reason: '' },
          ],
        },
      },
    },
  };

  it('says "above" for a burden measure', () => {
    const reason = load({
      ...withData,
      iso: 'NGA',
      indicator: 'u5mr',
      indicatorMeta: { u5mr: { lower_is_worse: false } },
    });
    expect(reason()).toBe('No area is above this threshold.');
  });

  it('says "below" for a coverage measure', () => {
    const reason = load({
      ...withData,
      iso: 'NGA',
      indicator: 'ors_coverage',
      indicatorMeta: { ors_coverage: { lower_is_worse: true } },
    });
    expect(reason()).toBe('No area is below this threshold.');
  });

  it('does not report an unmeasured country as one that cleared the bar', () => {
    const reason = load({
      iso: 'LBR',
      indicator: 'improved_water',
      indicatorMeta: { improved_water: { lower_is_worse: true } },
      method: 'subnational_relevelled',
      methodInfo: {
        methods: {
          subnational_relevelled: {
            countries: [
              {
                iso: 'LBR',
                name: 'Liberia',
                available: false,
                reason: 'no subnational survey for this country',
              },
            ],
          },
        },
      },
    });
    const text = reason();
    expect(text).toContain('no subnational survey for this country');
    expect(text).toContain('not a finding about Liberia');
    // The bug: this sentence claimed Liberia's water coverage was fine.
    expect(text).not.toContain('this threshold.');
  });
});
