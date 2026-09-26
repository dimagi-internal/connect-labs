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
    expect(out.match(/Another organisation/g)).toHaveLength(5);
    expect(out).toContain('not collected');
  });
});
