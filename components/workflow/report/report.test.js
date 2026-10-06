// The shared report library's contract. Render code in production depends on
// these names and props at runtime, where nothing type-checks them, so the
// tests pin what a render sees: the exported names, and what each component
// draws for the inputs a report hands it.
import { describe, expect, test } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { LabsReport as R } from './index';

const h = React.createElement;
const html = (el) => renderToStaticMarkup(el);

describe('the published surface', () => {
  test('every name a render may call is present', () => {
    // Removing or renaming one of these breaks live render code silently.
    const names = [
      'VERSION',
      'visitFlagsOf',
      'caseLabel',
      'nCount',
      'dateLbl',
      'fmtValue',
      'tintFor',
      'attention',
      'sortRows',
      'useTableSort',
      'Card',
      'SectionTitle',
      'Notice',
      'Loading',
      'Pill',
      'Button',
      'ReportHeader',
      'HeadlineTiles',
      'WeeklyActivity',
      'WeeklyActivityCard',
      'TrendCard',
      'ScoreCell',
      'AttentionCell',
      'ScorecardLegend',
      'scoreSortValue',
      'PeerBars',
      'PeerTrend',
      'PeerCard',
    ];
    for (const n of names) expect(R[n], n).toBeDefined();
  });
});

describe('formatting', () => {
  test('a percentage is a fraction printed to one decimal', () => {
    expect(R.fmtValue({ unit: '%' }, 0.7054)).toBe('70.5%');
  });
  test('a count carries separators and a mean keeps a decimal', () => {
    expect(R.fmtValue({ unit: 'n', kind: 'count' }, 37853)).toBe('37,853');
    expect(R.fmtValue({ unit: 'n' }, 3.14)).toBe('3.1');
  });
  test('grams and weeks', () => {
    expect(R.fmtValue({ unit: 'g' }, 1834.4)).toBe('1,834');
    expect(R.fmtValue({ unit: 'wks' }, 33.26)).toBe('33.3');
  });
  test('no value is a dash, not a zero', () => {
    expect(R.fmtValue({ unit: '%' }, null)).toBe('—');
  });
  test('a date reads as day and month', () => {
    expect(R.dateLbl('2026-09-15')).toBe('15 Sep');
  });
  test('attention counts off-target and watch cells', () => {
    expect(
      R.attention({
        a: { band: 'red' },
        b: { band: 'yellow' },
        c: { band: 'red' },
      }),
    ).toEqual({ reds: 2, yellows: 1 });
  });
});

describe('sorting', () => {
  const rows = [{ v: 2 }, { v: null }, { v: 5 }, { v: 1 }];
  const by = (r) => r.v;
  test('descending, with no value at the bottom', () => {
    expect(R.sortRows(rows, { key: 'v', dir: 'desc' }, by).map(by)).toEqual([
      5,
      2,
      1,
      null,
    ]);
  });
  test('ascending keeps no value at the bottom too: no data is not a low score', () => {
    expect(R.sortRows(rows, { key: 'v', dir: 'asc' }, by).map(by)).toEqual([
      1,
      2,
      5,
      null,
    ]);
  });
  test('the first click sorts descending, the second flips it', () => {
    expect(R.nextSort(null, 'x')).toEqual({ key: 'x', dir: 'desc' });
    expect(R.nextSort({ key: 'x', dir: 'desc' }, 'x')).toEqual({
      key: 'x',
      dir: 'asc',
    });
  });
});

describe('headline tiles', () => {
  const spec = {
    id: 'lost_by_day_28',
    label: 'Lost by day 28',
    pct: true,
    sub: 'target 10%',
  };
  test('value, band word and change since the previous report', () => {
    const out = html(
      h(R.HeadlineTiles, {
        tiles: [
          {
            spec,
            entry: { value: 0.12, band: 'red', n: 400 },
            previous: { value: 0.1 },
            previousDate: '2026-09-08',
          },
        ],
      }),
    );
    expect(out).toContain('12.0%');
    expect(out).toContain('Off target');
    expect(out).toContain('+2.0 pt since 8 Sep');
  });
  test('a target tick on the bar, and a chip for a lower-is-better measure', () => {
    const out = html(
      h(R.HeadlineTiles, {
        tiles: [
          {
            spec,
            entry: { value: 0.12, band: 'red', n: 400 },
            progress: 12,
            progressColour: '#b91c1c',
            progressTarget: 10,
            direction: 'lower',
          },
        ],
      }),
    );
    expect(out).toContain('data-target-tick');
    expect(out).toContain('lower is better');
    expect(out).toContain('#b91c1c');
  });
  test('no tick without a target, no chip for higher is better', () => {
    const out = html(
      h(R.HeadlineTiles, {
        tiles: [
          {
            spec,
            entry: { value: 0.12, band: 'red', n: 400 },
            progress: 12,
            direction: 'higher',
          },
        ],
      }),
    );
    expect(out).not.toContain('data-target-tick');
    expect(out).not.toContain('is better');
  });
  test('too few cases reads as the floor, not a number', () => {
    const out = html(
      h(R.HeadlineTiles, {
        tiles: [{ spec, entry: { value: 0.5, band: 'insufficient' } }],
      }),
    );
    expect(out).toContain('n&lt;20');
    expect(out).not.toContain('50.0%');
  });
});

describe('scorecard cells', () => {
  const col = { id: 'pct_healthy_growth', label: '%healthy' };
  const measure = { unit: '%', min_denominator: 30 };
  test('an off-target cell is tinted red', () => {
    const out = html(
      h(
        'table',
        null,
        h(
          'tbody',
          null,
          h(
            'tr',
            null,
            h(R.ScoreCell, {
              column: col,
              entry: { value: 0.4, band: 'red' },
              measure,
            }),
          ),
        ),
      ),
    );
    expect(out).toContain('bg-red-50');
    expect(out).toContain('40.0%');
  });
  test("a thin cell shows the measure's own floor", () => {
    const out = html(
      h(
        'table',
        null,
        h(
          'tbody',
          null,
          h(
            'tr',
            null,
            h(R.ScoreCell, {
              column: col,
              entry: { value: 0.4, band: 'insufficient' },
              measure,
            }),
          ),
        ),
      ),
    );
    expect(out).toContain('n&lt;30');
  });
  test('a denominator column prints n and is never tinted', () => {
    const out = html(
      h(
        'table',
        null,
        h(
          'tbody',
          null,
          h(
            'tr',
            null,
            h(R.ScoreCell, {
              column: { ...col, denOnly: true },
              entry: { value: 0.4, n: 1234, band: 'red' },
              measure,
            }),
          ),
        ),
      ),
    );
    expect(out).toContain('1,234');
    expect(out).not.toContain('bg-red-50');
  });
  test('a withheld cell sorts last, a denominator column sorts on n', () => {
    expect(
      R.scoreSortValue(col, { value: 0.4, band: 'insufficient' }),
    ).toBeNull();
    expect(R.scoreSortValue({ ...col, denOnly: true }, { n: 9 })).toBe(9);
  });
});

describe('peer trend', () => {
  const measure = { unit: '%', title: 'Healthy growth' };
  const series = {
    W0: [
      { peer_index: 1, value: 0.5 },
      { peer_index: 2, value: 0.6 },
    ],
    W2: [{ peer_index: 1, value: 0.55 }],
    W20: [{ peer_index: 2, value: 0.7 }],
  };
  test('points sit at their week number, so a gap stays a gap', () => {
    const out = html(h(R.PeerTrend, { series, measure }));
    // Peer 1's W0 and W2 are 2 weeks apart of a 20-week axis: 1/10 of the plot
    // width (218), not half of it as position-spacing drew them.
    const d = /d="M34\.0 [\d.]+ L([\d.]+)/.exec(out);
    expect(d, out).not.toBeNull();
    expect(Number(d[1])).toBeCloseTo(34 + 218 / 10, 1);
  });
  test('the axis is labelled from week 1', () => {
    const out = html(h(R.PeerTrend, { series, measure }));
    expect(out).toContain('week 1');
    expect(out).toContain('week 21');
  });
});

describe('peer bars', () => {
  test("this opportunity's bar is inserted by rank and named", () => {
    const out = html(
      h(R.PeerBars, {
        peers: [{ value: 0.2 }, { value: 0.8 }],
        measure: { unit: '%' },
        own: 0.5,
      }),
    );
    expect(out).toContain('2 anonymous peers + this opportunity');
    expect(out).toContain('this opportunity: 50.0%');
    expect(out.match(/bg-indigo-600/g)).toHaveLength(1);
  });
});

describe('trend card', () => {
  test('while loading, keeps the newest band word in its header', () => {
    const out = html(
      h(R.TrendCard, {
        label: 'x',
        target: 0.5,
        format: String,
        loading: true,
        points: [
          { date: '2026-09-15', entry: { value: 0.72, band: 'green', n: 50 } },
        ],
      }),
    );
    expect(out).toContain('Loading the trend');
    expect(out).toContain('On target');
  });
  test('says it is loading rather than that there is one report', () => {
    const out = html(
      h(R.TrendCard, { label: 'x', points: null, target: 0.5, format: String }),
    );
    expect(out).toContain('Loading the trend');
  });
  test('a report with too few cases is a gap, not a zero', () => {
    const out = html(
      h(R.TrendCard, {
        label: 'x',
        pct: true,
        target: 0.7,
        format: (e) => String(e.value),
        points: [
          { date: '2026-09-01', entry: { value: 0.6, band: 'yellow', n: 40 } },
          {
            date: '2026-09-08',
            entry: { value: 0.1, band: 'insufficient', n: 3 },
          },
          { date: '2026-09-15', entry: { value: 0.72, band: 'green', n: 50 } },
        ],
      }),
    );
    // Two drawn points, split into two one-point runs by the gap.
    expect(out.match(/<circle/g)).toHaveLength(2);
    expect(out).toContain('On target');
  });
});

describe('organisation benchmark', () => {
  const m = { unit: '%', direction: 'higher', bands: [70, 50] };
  const others = [
    { value: 0.68, band: 'yellow' },
    { value: null, band: 'notinapp' },
    { value: 0.39, band: 'red' },
    { value: 0.09, band: 'insufficient' },
    { value: 0.52, band: 'yellow' },
  ];
  const own = { value: 0.47, band: 'red' };
  test('ranks the reader among organisations with a usable figure, best first', () => {
    const r = R.rankOrganisations(m, own, others);
    expect(r.rank).toBe(3);
    expect(r.scored).toBe(4);
    expect(r.total).toBe(6);
    expect(r.ordered.map((a) => a.figure.value)).toEqual([
      0.68,
      0.52,
      0.47,
      0.39,
      null,
      0.09,
    ]);
  });
  test('coverage names every missing organisation and why', () => {
    expect(R.rankOrganisations(m, own, others).coverage).toBe(
      '4 of 6 · 1 not collected · 1 too few babies',
    );
  });
  test('lower is better sorts the other way', () => {
    const r = R.rankOrganisations({ ...m, direction: 'lower' }, own, others);
    expect(r.rank).toBe(2);
  });
  test('a tie shares the better place', () => {
    const tie = [
      { value: 0, band: 'green' },
      { value: 0, band: 'green' },
      { value: 3, band: 'red' },
    ];
    const r = R.rankOrganisations(
      { unit: 'd', direction: 'lower' },
      { value: 0, band: 'green' },
      tie,
    );
    expect(r.rank).toBe(1);
    expect(r.tied).toBe(true);
  });
  test('a two-sided measure is not ranked', () => {
    expect(
      R.rankOrganisations({ ...m, direction: 'mid2' }, own, others).ranked,
    ).toBe(false);
  });
  test('a percent target is converted to the value units', () => {
    expect(R.targetOf(m)).toBe(0.7);
    expect(
      R.targetOf({ unit: 'g/kg/d', direction: 'higher', bands: [15, 13] }),
    ).toBe(15);
  });
  test('the mini bars draw every organisation, the missing ones as outlines', () => {
    const out = html(
      h(R.MiniRankBars, {
        ranked: R.rankOrganisations(m, own, others),
        target: 0.7,
      }),
    );
    expect(out.match(/<rect/g)).toHaveLength(6);
    expect(out.match(/stroke-dasharray="3 3"/g)).toHaveLength(2);
    expect(out.match(/#4f46e5/g)).toHaveLength(1);
  });
  test('the full chart names only the reader', () => {
    const out = html(
      h(R.RankedBars, {
        measure: m,
        ranked: R.rankOrganisations(m, own, others),
        ownLabel: 'NAMA (you)',
      }),
    );
    expect(out).toContain('NAMA (you)');
    expect(out.match(/Another organization/g)).toHaveLength(5);
    expect(out).toContain('not collected');
  });
});

describe('the registry display contract (VERSION 3)', () => {
  const measures = [
    {
      indicator: 'reached',
      title: 'Communities reached',
      unit: 'n',
      kind: 'count',
      category: 'Reach',
      prominence: 'Top',
    },
    {
      indicator: 'pct_ok',
      title: '% visited twice',
      unit: '%',
      direction: 'higher',
      bands: [60, 40],
      category: 'Follow-up',
      prominence: 'Top',
      target: 70,
      headline: 1,
      label: 'Twice',
    },
    {
      indicator: 'pct_flag',
      title: '% flagged',
      unit: '%',
      direction: 'lower',
      bands: [10, 25],
      category: 'Quality',
      prominence: 'Lower',
    },
  ];
  test('visit flags: none declared is an empty list, and each declared flag matches its visits', () => {
    expect(R.displayOf({ cMeasures: measures }).visit_flags).toEqual([]);
    expect(R.displayOf({ cMeasures: measures }).visit_fields).toEqual([]);
    const d = R.displayOf({
      cMeasures: measures,
      display: {
        visit_flags: [
          { column: 'repeat_counts_flag', label: 'Repeat count' },
          { column: 'risk', label: 'High risk', value: 'high' },
        ],
      },
    });
    expect(
      R.visitFlagsOf(d, { repeat_counts_flag: 'yes', risk: 'HIGH' }),
    ).toEqual(['Repeat count', 'High risk']);
    expect(R.visitFlagsOf(d, { repeat_counts_flag: true })).toEqual([
      'Repeat count',
    ]);
    expect(R.visitFlagsOf(d, { repeat_counts_flag: 1 })).toEqual([
      'Repeat count',
    ]);
    // 'no', blank and absent are not flags; Connect's own `flagged` is not either
    expect(
      R.visitFlagsOf(d, { repeat_counts_flag: 'no', flagged: true }),
    ).toEqual([]);
    expect(R.visitFlagsOf(d, { repeat_counts_flag: '' })).toEqual([]);
    expect(R.visitFlagsOf(d, {})).toEqual([]);
  });
  test('a case reads by its label field, falling back to its id (VERSION 8)', () => {
    expect(R.VERSION).toBeGreaterThanOrEqual(8);
    const none = R.displayOf({ cMeasures: measures });
    expect(none.entity.label_field).toBeNull();
    expect(R.caseLabel(none, { entity_id: 'abcdef123456789' }, 6)).toBe(
      'abcdef',
    );
    const d = R.displayOf({
      cMeasures: measures,
      display: { entity: { name: 'child', label_field: 'entity_name' } },
    });
    expect(d.entity.label_field).toBe('entity_name');
    expect(
      R.caseLabel(d, { entity_id: 'abc123', entity_name: ' Amina K. ' }, 4),
    ).toBe('Amina K.');
    // a case without the field (or with a blank one) still reads by its id
    expect(R.caseLabel(d, { entity_id: 'abc123', entity_name: '' }, 4)).toBe(
      'abc1',
    );
    expect(R.caseLabel(d, { entity_id: 'abc123' })).toBe('abc123');
  });
  test('displayOf falls back to the catalog when a payload carries no display block', () => {
    const d = R.displayOf({ cMeasures: measures });
    expect(d.entity.plural).toBe('cases');
    expect(d.worker.name).toBe('worker');
    expect(d.categories).toEqual(['Reach', 'Follow-up', 'Quality']);
    // a declared headline wins over "first five Top"
    expect(d.headline).toEqual(['pct_ok']);
    expect(d.indicators.pct_flag.scorecard).toBe(false);
  });
  test('the payload display block is authoritative', () => {
    const d = R.displayOf({
      cMeasures: measures,
      display: {
        entity: { name: 'community', plural: 'communities' },
        headline: ['reached', 'pct_ok'],
        categories: ['Follow-up', 'Reach', 'Quality'],
        indicators: {
          reached: { label: 'Reached', scorecard: true },
          pct_ok: { target: 70, scorecard: true },
          pct_flag: { scorecard: true },
        },
      },
    });
    expect(d.entity.plural).toBe('communities');
    const tiles = R.headlineSpecs({ cMeasures: measures }, d);
    expect(tiles.map((t) => t.id)).toEqual(['reached', 'pct_ok']);
    expect(tiles[0].count).toBe(true);
    expect(tiles[1].pct).toBe(true);
    expect(tiles[1].target).toBeCloseTo(0.7);
    expect(tiles[1].sub).toBe('target 70.0%');
    const layout = R.scorecardLayout({ cMeasures: measures }, d);
    expect(layout.columns.map((c) => c.id)).toEqual([
      'pct_ok',
      'reached',
      'pct_flag',
    ]);
    expect(layout.groups).toEqual([
      { label: 'Follow-up', span: 1 },
      { label: 'Reach', span: 1 },
      { label: 'Quality', span: 1 },
    ]);
  });
  test('a target defaults to the green band edge, in value units', () => {
    expect(R.targetValue(measures[2], {})).toBeCloseTo(0.1);
    expect(R.targetValue({ unit: 'n', direction: 'none' }, {})).toBeNull();
  });
  test('case fields print by their format', () => {
    expect(
      R.fmtCaseField({ field: 'd', label: 'D', format: 'date' }, '2026-09-15'),
    ).toBe('15 Sep');
    expect(
      R.fmtCaseField(
        { field: 'w', label: 'W', format: 'count', unit: 'g' },
        2500,
      ),
    ).toBe('2,500 g');
    expect(R.fmtCaseField({ field: 'x', label: 'X' }, null)).toBe('—');
    expect(R.nounCount(1, { name: 'baby', plural: 'babies' })).toBe('1 baby');
  });
  test('the reading chart and the insufficient-coverage noun', () => {
    const svg = html(
      h(R.ReadingChart, {
        points: [
          { date: '2026-01-01', value: 2000 },
          { date: '2026-01-08', value: 2150 },
        ],
        label: 'Weight',
      }),
    );
    expect(svg).toContain('<path');
    const ranked = R.rankOrganisations(
      { direction: 'higher' },
      { value: 0.5, band: 'green' },
      [{ value: null, band: 'insufficient' }],
      { entityPlural: 'communities' },
    );
    expect(ranked.coverage).toContain('too few communities');
  });
  test('a trend without a target draws no target line', () => {
    const out = html(
      h(R.TrendCard, {
        label: 'x',
        points: [
          { date: '2026-01-01', entry: { value: 1, band: 'green', n: 30 } },
          { date: '2026-01-08', entry: { value: 2, band: 'green', n: 30 } },
        ],
        format: (e) => String(e.value),
      }),
    );
    expect(out).not.toContain('target ');
  });
});

describe('scenario building blocks (VERSION 4)', () => {
  test('the scenario names are published and VERSION says so', () => {
    for (const n of [
      'StatTiles',
      'NumberField',
      'RangeField',
      'Field',
      'Segmented',
      'RangeStrip',
      'rangeText',
      'stripPos',
    ]) {
      expect(R[n], n).toBeDefined();
    }
    expect(R.VERSION).toBeGreaterThanOrEqual(4);
  });

  test('rangeText collapses a range whose ends format the same', () => {
    const f = (v) => '$' + Math.round(v);
    expect(R.rangeText(1886.2, 2305, f)).toBe('$1886 – $2305');
    expect(R.rangeText(10.1, 9.9, f)).toBe('$10');
  });

  test('StatTiles draws a pre-formatted value, its sub line and a toned word', () => {
    const out = html(
      h(R.StatTiles, {
        tiles: [
          {
            id: 'x',
            label: 'Multiple of benchmark',
            value: '16.3x – 20.0x',
            sub: 'GiveWell bar 6x',
            tone: 'good',
            toneLabel: 'Clears the bar',
          },
        ],
      }),
    );
    expect(out).toContain('16.3x – 20.0x');
    expect(out).toContain('GiveWell bar 6x');
    expect(out).toContain('Clears the bar');
    expect(out).toContain('text-green-700');
  });

  test('NumberField keeps the raw string and marks an edited value', () => {
    const out = html(
      h(R.NumberField, {
        value: '0.',
        onChange: () => {},
        prefix: '$',
        edited: true,
      }),
    );
    expect(out).toContain('value="0."');
    expect(out).toContain('$');
    expect(out).toContain('bg-indigo-50');
  });

  test('RangeField reports both ends when one changes', () => {
    const calls = [];
    const el = R.RangeField({
      low: '10',
      high: '12',
      onChange: (a, b) => calls.push([a, b]),
    });
    const kids = React.Children.toArray(el.props.children);
    kids[0].props.onChange('8');
    kids[2].props.onChange('14');
    expect(calls).toEqual([
      ['8', '12'],
      ['10', '14'],
    ]);
  });

  test('Segmented marks exactly the selected option', () => {
    const out = html(
      h(R.Segmented, {
        options: [
          { id: 'a', label: 'Alpha' },
          { id: 'b', label: 'Beta' },
        ],
        value: 'b',
        onChange: () => {},
      }),
    );
    expect((out.match(/aria-checked="true"/g) || []).length).toBe(1);
    expect(out).toMatch(/aria-checked="true"[^>]*>Beta/);
  });

  test('stripPos is linear or log, and clamps to the axis', () => {
    expect(R.stripPos(50, 0, 100)).toBe(50);
    expect(R.stripPos(1000, 100, 10000, true)).toBeCloseTo(50);
    expect(R.stripPos(-5, 0, 100)).toBe(0);
    expect(R.stripPos(1e9, 100, 10000, true)).toBe(100);
  });

  test('RangeStrip draws the range and one line per marker', () => {
    const out = html(
      h(R.RangeStrip, {
        lo: 2000,
        hi: 1000,
        min: 100,
        max: 10000,
        log: true,
        tone: 'good',
        markers: [
          { value: 6278, label: 'GiveWell 6x' },
          { value: 5000, label: 'target', dashed: true },
        ],
      }),
    );
    expect(out).toContain('#16a34a');
    expect(out).toContain('title="GiveWell 6x"');
    expect(out).toContain('dashed');
  });
});

describe('enrolment against target (VERSION 5)', () => {
  // The KMC goals sheet's monthly enrolment targets (programme as of 2026-10-05),
  // against the registrations a saved run carries per LLO and cohort month.
  const TARGETS = {
    unit: 'registered babies',
    llos: {
      NAMA: {
        before_window: 165,
        monthly: {
          '2026-07': 228,
          '2026-08': 187,
          '2026-09': 176,
          '2026-10': 150,
          '2026-11': 150,
          '2026-12': 75,
          '2027-01': 100,
          '2027-02': 130,
          '2027-03': 154,
        },
      },
      PIPN: {
        before_window: 309,
        monthly: {
          '2026-07': 453,
          '2026-08': 489,
          '2026-09': 460,
          '2026-10': 1200,
          '2026-11': 1600,
          '2026-12': 1400,
          '2027-01': 1100,
          '2027-02': 1100,
          '2027-03': 1548,
        },
      },
      EHA: {
        monthly: {
          '2026-08': 50,
          '2026-09': 222,
          '2026-10': 240,
          '2026-11': 240,
          '2026-12': 150,
          '2027-01': 215,
          '2027-02': 240,
          '2027-03': 240,
        },
      },
      BERI: {},
    },
  };
  const pt = (month, count, graded) => ({
    month,
    n: count,
    counts: graded === undefined ? { registered_cases: count } : undefined,
    ind: { registered_cases: { value: graded === undefined ? count : graded } },
  });
  const BY_SCOPE = {
    'llo:NAMA': [
      pt('2026-06', 102),
      pt('2026-07', 228),
      pt('2026-08', 187),
      pt('2026-09', 194),
    ],
    'llo:PIPN': [pt('2026-07', 453), pt('2026-08', 489), pt('2026-09', 578)],
    'llo:EHA': [pt('2026-07', 149), pt('2026-08', 229), pt('2026-09', 221)],
    'llo:BERI': [pt('2026-07', 411)],
  };
  const progress = (scope, byScope) =>
    R.enrolmentProgress({
      targets: TARGETS,
      monthlyByScope: byScope || BY_SCOPE,
      asOf: '2026-09-30',
      scope,
    });

  test('the window runs from the first to the last target month', () => {
    const w = R.targetWindow(TARGETS);
    expect(w[0]).toBe('2026-07');
    expect(w[w.length - 1]).toBe('2027-03');
    expect(w.length).toBe(9);
    expect(R.targetedLlos(TARGETS)).toEqual(['NAMA', 'PIPN', 'EHA']);
  });

  test('one LLO: carry-in counts toward both sides, the goal is the sheet total', () => {
    const p = progress('NAMA');
    expect(p.goal).toBe(1515);
    expect(p.carryIn).toBe(165);
    expect(p.cumActual).toBe(165 + 228 + 187 + 194);
    expect(p.cumTargetToDate).toBe(165 + 228 + 187 + 176);
    expect(p.gap).toBe(18);
    expect(p.remainingMonths).toBe(6);
    expect(p.runRateNeeded).toBeCloseTo((1515 - 774) / 6, 6);
  });

  test('months after the as-of month carry the target only', () => {
    const p = progress('PIPN');
    const oct = p.months.find((m) => m.month === '2026-10');
    expect(oct.future).toBe(true);
    expect(oct.actual).toBeNull();
    expect(oct.cumActual).toBeNull();
    expect(oct.target).toBe(1200);
    expect(p.goal).toBe(9659);
  });

  test("an LLO's actuals count from its own first target month", () => {
    // EHA's targets start in August: July's 149 registrations are not credited.
    const p = progress('EHA');
    expect(p.months.find((m) => m.month === '2026-07').actual).toBeNull();
    expect(p.cumActual).toBe(229 + 221);
    expect(p.goal).toBe(1597);
  });

  test('the programme is the sum of the LLOs that have a target, and only those', () => {
    const p = progress('all');
    expect(p.llos).toEqual(['NAMA', 'PIPN', 'EHA']);
    expect(p.goal).toBe(12771);
    expect(p.cumActual).toBe(774 + 1829 + 450);
    expect(p.cumTargetToDate).toBe(756 + 1711 + 272);
  });

  test('a raw count wins over a suppressed graded cell; an unreadable month is named', () => {
    const p = progress('PIPN', {
      'llo:PIPN': [
        pt('2026-07', 453),
        pt('2026-08', 489, null),
        pt('2026-09', 578),
      ],
    });
    expect(p.unknownMonths).toEqual(['2026-08']);
    expect(p.cumActual).toBe(309 + 453 + 578);
    const legacy = {
      month: '2026-08',
      n: 5,
      ind: { registered_cases: { value: 5 } },
    };
    expect(
      progress('PIPN', { 'llo:PIPN': [legacy] }).months.find(
        (m) => m.month === '2026-08',
      ).actual,
    ).toBe(5);
  });

  test('a scope with no target has no progress', () => {
    expect(progress('BERI')).toBeNull();
    expect(
      R.enrolmentProgress({
        targets: {},
        monthlyByScope: BY_SCOPE,
        asOf: '2026-09-30',
        scope: 'all',
      }),
    ).toBeNull();
  });

  test('the readout and chart draw the distance to target', () => {
    const p = progress('NAMA');
    const out = html(h(R.EnrolmentTargetSummary, { progress: p }));
    expect(out).toContain('Ahead of target');
    expect(out).toContain('1,515');
    expect(out).toContain('124');
    expect(out).toContain('6 remaining months');
    const svg = html(h(R.EnrolmentTargetChart, { progress: p }));
    expect(svg).toContain('stroke-dasharray="3 2"'); // future bars: dashed, paler
    expect(svg).toContain('Mar');
  });
  test("the as-of month's target is pro-rated to the days elapsed", () => {
    // As of 4 October, PIPN has had 4/31 of October's 1,200 -- not all of it.
    expect(R.elapsedFraction('2026-10-04')).toBeCloseTo(4 / 31, 9);
    expect(R.elapsedFraction('2026-09-30')).toBe(1);
    const p = R.enrolmentProgress({
      targets: TARGETS,
      monthlyByScope: {
        'llo:PIPN': [...BY_SCOPE['llo:PIPN'], pt('2026-10', 210)],
      },
      asOf: '2026-10-04',
      scope: 'PIPN',
    });
    const f = 4 / 31;
    expect(p.cumTargetToDate).toBeCloseTo(1711 + 1200 * f, 6);
    expect(p.cumActual).toBe(1829 + 210);
    expect(p.asOfFraction).toBeCloseTo(f, 9);
    expect(p.remainingMonths).toBe(5);
    expect(p.runRateNeeded).toBeCloseTo((9659 - 2039) / (5 + 1 - f), 6);
    // The bars and the cumulative target line still state whole months.
    expect(p.months.find((m) => m.month === '2026-10').cumTarget).toBe(
      1711 + 1200,
    );
    const out = html(h(R.EnrolmentTargetSummary, { progress: p }));
    expect(out).toContain('pro-rated');
    expect(out).toContain('rest of Oct');
  });
  test('the monthly chart draws no as-of divider', () => {
    const svg = html(h(R.EnrolmentTargetChart, { progress: progress('NAMA') }));
    expect(svg).not.toContain('future: target only');
    expect(svg).not.toContain('#a5b4fc');
  });

  // Registrations by day, as the builder emits them (semantic/snapshot.py
  // daily_counts): September in full, October to the as-of day.
  const sep = (n) => Array.from({ length: 30 }, (_, i) => (i < n ? 6 : 0));
  const DAILY = {
    months: ['2026-09', '2026-10'],
    as_of: '2026-10-04',
    byScope: {
      'llo:PIPN': { '2026-09': sep(30), '2026-10': [50, 40, 60, 50] },
      'llo:NAMA': { '2026-09': sep(30), '2026-10': [2, 3, 0, 3] },
      'llo:BERI': { '2026-09': sep(30), '2026-10': [9, 9, 9, 9] },
    },
  };

  test('day by day: running total against the target spread over the month', () => {
    const p = R.dailyProgress({
      targets: TARGETS,
      daily: DAILY,
      scope: 'PIPN',
    });
    expect(p.month).toBe('2026-10');
    expect(p.daysInMonth).toBe(31);
    expect(p.day).toBe(4);
    expect(p.cumulative).toEqual([50, 90, 150, 200]);
    expect(p.target).toBe(1200);
    expect(p.onPace).toBeCloseTo((1200 * 4) / 31, 9);
    expect(p.previousByDay).toBe(24);
    expect(p.previous.length).toBe(30);
    expect(R.dailyReadout(p)).toBe(
      'Day 4 of 31: 200 enrolled vs 155 on pace (+45) · last month by day 4: 24',
    );
  });

  test('day by day: the programme sums the LLOs with a target, and only those', () => {
    const p = R.dailyProgress({ targets: TARGETS, daily: DAILY, scope: 'all' });
    expect(p.enrolled).toBe(200 + 8);
    expect(p.target).toBe(1200 + 150 + 240);
    expect(
      R.dailyProgress({ targets: TARGETS, daily: DAILY, scope: 'BERI' }),
    ).toBeNull();
    expect(
      R.dailyProgress({ targets: TARGETS, daily: null, scope: 'all' }),
    ).toBeNull();
  });

  test('day by day: behind pace reads with a minus and draws three lines', () => {
    const p = R.dailyProgress({
      targets: TARGETS,
      daily: DAILY,
      scope: 'NAMA',
    });
    expect(R.dailyReadout(p)).toContain('8 enrolled vs 19 on pace (−11)');
    const svg = html(h(R.EnrolmentDailyChart, { progress: p }));
    expect(svg).toContain('This month, day by day · Oct 2026');
    expect(svg).toContain('Last month (Sep)');
    expect(svg).toContain('text-red-700');
    expect(svg).toContain('stroke-dasharray="5 4"');
    expect(R.VERSION).toBeGreaterThanOrEqual(6);
  });
});
