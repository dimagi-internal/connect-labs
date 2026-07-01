/**
 * Unit tests for funder-charts.js helper functions.
 *
 * Focuses on:
 *  - weekStart() — day-of-week math, UTC vs local timezone edge cases
 *  - uniqueWeeks(), groupByOpp(), fmtNum(), fmtUSD()
 *  - referenceWeeks(), trendInfo(), countVisitsInWeek(), sumUSDInWeek()
 *  - activeFLWs(), maxVisitDate(), aggregateForReport()
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  weekStart,
  uniqueWeeks,
  groupByOpp,
  oppColor,
  resetColors,
  fmtNum,
  fmtUSD,
  referenceWeeks,
  countVisitsInWeek,
  sumUSDInWeek,
  trendInfo,
  activeFLWs,
  maxVisitDate,
  aggregateForReport,
} = require('./funder-charts.js');

// ---------------------------------------------------------------------------
// weekStart()
// ---------------------------------------------------------------------------
describe('weekStart', () => {
  it('returns the Monday for a mid-week Wednesday', () => {
    // 2025-03-19 is a Wednesday -> Monday is 2025-03-17
    expect(weekStart('2025-03-19')).toBe('2025-03-17');
  });

  it('returns the same date when given a Monday', () => {
    // 2025-03-17 is a Monday
    expect(weekStart('2025-03-17')).toBe('2025-03-17');
  });

  it('returns the previous Monday for a Sunday', () => {
    // 2025-03-23 is a Sunday -> Monday is 2025-03-17
    expect(weekStart('2025-03-23')).toBe('2025-03-17');
  });

  it('returns the previous Monday for a Saturday', () => {
    // 2025-03-22 is a Saturday -> Monday is 2025-03-17
    expect(weekStart('2025-03-22')).toBe('2025-03-17');
  });

  it('handles Tuesday correctly', () => {
    // 2025-03-18 is a Tuesday -> Monday is 2025-03-17
    expect(weekStart('2025-03-18')).toBe('2025-03-17');
  });

  it('handles Friday correctly', () => {
    // 2025-03-21 is a Friday -> Monday is 2025-03-17
    expect(weekStart('2025-03-21')).toBe('2025-03-17');
  });

  it('crosses month boundary backwards', () => {
    // 2025-03-01 is a Saturday -> Monday is 2025-02-24
    expect(weekStart('2025-03-01')).toBe('2025-02-24');
  });

  it('crosses year boundary backwards', () => {
    // 2025-01-01 is a Wednesday -> Monday is 2024-12-30
    expect(weekStart('2025-01-01')).toBe('2024-12-30');
  });

  it('returns null for null/undefined/empty input', () => {
    expect(weekStart(null)).toBeNull();
    expect(weekStart(undefined)).toBeNull();
    expect(weekStart('')).toBeNull();
  });

  it('returns null for invalid date strings', () => {
    expect(weekStart('not-a-date')).toBeNull();
    expect(weekStart('9999-99-99')).toBeNull();
  });

  // --- UTC vs local timezone edge cases ---
  // The original bug: new Date("2025-03-17") (without T00:00:00) is parsed as
  // UTC midnight. In timezones behind UTC (e.g. US Eastern, UTC-4), that becomes
  // Sunday March 16 in local time, causing weekStart to return the PREVIOUS
  // Monday (March 10) instead of March 17.

  it('handles a Monday date string without being affected by UTC parsing', () => {
    // This is the core regression test. If the function parsed "2025-03-17" as
    // UTC midnight, in UTC-4 it would be Sun Mar 16 local -> week start Mar 10.
    // The fix appends T00:00:00 to force local-time parsing.
    expect(weekStart('2025-03-17')).toBe('2025-03-17');
  });

  it('strips time/timezone suffixes and uses only the date portion', () => {
    // If someone passes a full ISO datetime, only the YYYY-MM-DD is used
    expect(weekStart('2025-03-19T23:59:59Z')).toBe('2025-03-17');
    expect(weekStart('2025-03-19T00:00:00-05:00')).toBe('2025-03-17');
    expect(weekStart('2025-03-19T12:30:00+05:30')).toBe('2025-03-17');
  });

  it('handles dates at month-end correctly', () => {
    // 2025-02-28 is a Friday -> Monday is 2025-02-24
    expect(weekStart('2025-02-28')).toBe('2025-02-24');
  });

  it('handles leap year date', () => {
    // 2024-02-29 is a Thursday -> Monday is 2024-02-26
    expect(weekStart('2024-02-29')).toBe('2024-02-26');
  });

  it('pads single-digit months and days in output', () => {
    // 2025-01-06 is a Monday
    expect(weekStart('2025-01-07')).toBe('2025-01-06');
    // Ensure zero-padding: month 1 -> "01", day 6 -> "06"
    expect(weekStart('2025-01-07')).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

// ---------------------------------------------------------------------------
// uniqueWeeks()
// ---------------------------------------------------------------------------
describe('uniqueWeeks', () => {
  it('returns sorted unique week-start strings', () => {
    const dates = [
      '2025-03-19', // Wed -> 2025-03-17
      '2025-03-20', // Thu -> 2025-03-17 (same week)
      '2025-03-10', // Mon -> 2025-03-10
      '2025-03-25', // Tue -> 2025-03-24
    ];
    expect(uniqueWeeks(dates)).toEqual([
      '2025-03-10',
      '2025-03-17',
      '2025-03-24',
    ]);
  });

  it('returns empty array for empty input', () => {
    expect(uniqueWeeks([])).toEqual([]);
  });

  it('filters out invalid dates', () => {
    expect(uniqueWeeks(['bad', '', null])).toEqual([]);
  });

  it('deduplicates same-week dates', () => {
    // All in the same week (Mon Mar 17 - Sun Mar 23)
    const dates = ['2025-03-17', '2025-03-18', '2025-03-19', '2025-03-23'];
    expect(uniqueWeeks(dates)).toEqual(['2025-03-17']);
  });
});

// ---------------------------------------------------------------------------
// groupByOpp()
// ---------------------------------------------------------------------------
describe('groupByOpp', () => {
  it('groups rows by opp_id', () => {
    const rows = [
      { opp_id: 1, opp_name: 'Alpha', country: 'KE', visit_date: '2025-03-01' },
      { opp_id: 2, opp_name: 'Beta', country: 'NG', visit_date: '2025-03-02' },
      { opp_id: 1, opp_name: 'Alpha', country: 'KE', visit_date: '2025-03-03' },
    ];
    const result = groupByOpp(rows);
    expect(Object.keys(result)).toEqual(['1', '2']);
    expect(result['1'].rows).toHaveLength(2);
    expect(result['2'].rows).toHaveLength(1);
    expect(result['1'].opp_name).toBe('Alpha');
    expect(result['1'].country).toBe('KE');
  });

  it('returns empty object for empty array', () => {
    expect(groupByOpp([])).toEqual({});
  });

  it('uses fallback opp_name when missing', () => {
    const rows = [{ opp_id: 42 }];
    const result = groupByOpp(rows);
    expect(result['42'].opp_name).toBe('Opp 42');
  });
});

// ---------------------------------------------------------------------------
// oppColor() / resetColors()
// ---------------------------------------------------------------------------
describe('oppColor / resetColors', () => {
  beforeEach(() => {
    resetColors();
  });

  it('returns a consistent color for the same opp id', () => {
    const c1 = oppColor('A');
    const c2 = oppColor('A');
    expect(c1).toBe(c2);
  });

  it('returns different colors for different opp ids', () => {
    const c1 = oppColor('A');
    const c2 = oppColor('B');
    expect(c1).not.toBe(c2);
  });

  it('wraps around after exhausting the palette', () => {
    // There are 15 colors in OPP_COLORS
    const colors = [];
    for (let i = 0; i < 16; i++) {
      colors.push(oppColor('opp-' + i));
    }
    // The 16th color (index 15) should wrap to index 0
    expect(colors[15]).toBe(colors[0]);
  });

  it('resetColors clears the mapping', () => {
    oppColor('X');
    resetColors();
    // After reset, 'X' gets reassigned from the start
    const c = oppColor('X');
    expect(c).toBe(oppColor('X'));
  });
});

// ---------------------------------------------------------------------------
// fmtNum()
// ---------------------------------------------------------------------------
describe('fmtNum', () => {
  it('formats numbers with comma separators', () => {
    expect(fmtNum(1000)).toBe('1,000');
    expect(fmtNum(1234567)).toBe('1,234,567');
  });

  it('handles zero', () => {
    expect(fmtNum(0)).toBe('0');
  });

  it('returns "0" for null/undefined', () => {
    expect(fmtNum(null)).toBe('0');
    expect(fmtNum(undefined)).toBe('0');
  });

  it('formats small numbers without commas', () => {
    expect(fmtNum(42)).toBe('42');
  });
});

// ---------------------------------------------------------------------------
// fmtUSD()
// ---------------------------------------------------------------------------
describe('fmtUSD', () => {
  it('formats dollar amounts with $ prefix', () => {
    expect(fmtUSD(1000)).toBe('$1,000');
  });

  it('rounds to whole dollars (no decimals)', () => {
    // toLocaleString with maximumFractionDigits: 0
    expect(fmtUSD(1234.56)).toBe('$1,235');
  });

  it('returns "$0" for null/undefined', () => {
    expect(fmtUSD(null)).toBe('$0');
    expect(fmtUSD(undefined)).toBe('$0');
  });
});

// ---------------------------------------------------------------------------
// referenceWeeks()
// ---------------------------------------------------------------------------
describe('referenceWeeks', () => {
  it('returns thisWeekStart as the week-start of the max date', () => {
    const dates = ['2025-03-10', '2025-03-19', '2025-03-15'];
    const result = referenceWeeks(dates);
    // Max date is 2025-03-19 (Wed) -> week start is 2025-03-17 (Mon)
    expect(result.thisWeekStart).toBe('2025-03-17');
  });

  it('returns lastWeekStart as 7 days before thisWeekStart', () => {
    const dates = ['2025-03-19'];
    const result = referenceWeeks(dates);
    expect(result.thisWeekStart).toBe('2025-03-17');
    expect(result.lastWeekStart).toBe('2025-03-10');
  });

  it('returns nulls for empty array', () => {
    const result = referenceWeeks([]);
    expect(result.thisWeekStart).toBeNull();
    expect(result.lastWeekStart).toBeNull();
  });

  it('handles single-element array', () => {
    const result = referenceWeeks(['2025-03-17']); // Monday
    expect(result.thisWeekStart).toBe('2025-03-17');
    expect(result.lastWeekStart).toBe('2025-03-10');
  });
});

// ---------------------------------------------------------------------------
// trendInfo()
// ---------------------------------------------------------------------------
describe('trendInfo', () => {
  it('returns "up" when value increased', () => {
    const t = trendInfo(150, 100);
    expect(t.direction).toBe('up');
    expect(t.pct).toBe(50);
  });

  it('returns "down" when value decreased', () => {
    const t = trendInfo(50, 100);
    expect(t.direction).toBe('down');
    expect(t.pct).toBe(50); // absolute value
  });

  it('returns "flat" when both are zero', () => {
    expect(trendInfo(0, 0)).toEqual({ pct: 0, direction: 'flat' });
  });

  it('returns "up" 100% when last was zero but this is nonzero', () => {
    expect(trendInfo(42, 0)).toEqual({ pct: 100, direction: 'up' });
  });

  it('returns "flat" when values are equal and nonzero', () => {
    expect(trendInfo(100, 100)).toEqual({ pct: 0, direction: 'flat' });
  });

  it('rounds percentage to nearest integer', () => {
    // (200 - 300) / 300 = -33.33%
    const t = trendInfo(200, 300);
    expect(t.pct).toBe(33);
    expect(t.direction).toBe('down');
  });
});

// ---------------------------------------------------------------------------
// countVisitsInWeek()
// ---------------------------------------------------------------------------
describe('countVisitsInWeek', () => {
  const visits = [
    { visit_date: '2025-03-17', status: 'approved' },
    { visit_date: '2025-03-18', status: 'approved' },
    { visit_date: '2025-03-19', status: 'rejected' },
    { visit_date: '2025-03-24', status: 'approved' },
  ];

  it('counts only approved visits in the given week', () => {
    // Week of 2025-03-17: two approved, one rejected
    expect(countVisitsInWeek(visits, '2025-03-17')).toBe(2);
  });

  it('returns 0 for a week with no approved visits', () => {
    expect(countVisitsInWeek(visits, '2025-03-10')).toBe(0);
  });

  it('counts the next week correctly', () => {
    // Week of 2025-03-24: one approved
    expect(countVisitsInWeek(visits, '2025-03-24')).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// sumUSDInWeek()
// ---------------------------------------------------------------------------
describe('sumUSDInWeek', () => {
  const payments = [
    { status_modified_date: '2025-03-17', usd_flw: '100', usd_org: '50' },
    { status_modified_date: '2025-03-19', usd_flw: '200', usd_org: '0' },
    { status_modified_date: '2025-03-24', usd_flw: '300', usd_org: '100' },
  ];

  it('sums usd_flw + usd_org for all payments in the week', () => {
    // Week of 2025-03-17: (100+50) + (200+0) = 350
    expect(sumUSDInWeek(payments, '2025-03-17')).toBe(350);
  });

  it('returns 0 for a week with no payments', () => {
    expect(sumUSDInWeek(payments, '2025-03-10')).toBe(0);
  });

  it('falls back to payment_date when status_modified_date is missing', () => {
    const p = [{ payment_date: '2025-03-18', usd_flw: '50', usd_org: '25' }];
    expect(sumUSDInWeek(p, '2025-03-17')).toBe(75);
  });
});

// ---------------------------------------------------------------------------
// activeFLWs()
// ---------------------------------------------------------------------------
describe('activeFLWs', () => {
  const visits = [
    { username: 'alice', visit_date: '2025-03-20' },
    { username: 'bob', visit_date: '2025-03-15' },
    { username: 'alice', visit_date: '2025-03-18' },
    { username: 'carol', visit_date: '2025-03-01' },
  ];

  it('counts distinct usernames within the last N days', () => {
    // maxDate = 2025-03-20, days = 7 -> cutoff = 2025-03-13
    // alice (Mar 20, Mar 18), bob (Mar 15) -> 3 entries, 2 unique users
    expect(activeFLWs(visits, '2025-03-20', 7)).toBe(2);
  });

  it('includes the boundary dates', () => {
    // days = 5 -> cutoff = 2025-03-15
    // alice (Mar 20, Mar 18), bob (Mar 15) -> alice + bob
    expect(activeFLWs(visits, '2025-03-20', 5)).toBe(2);
  });

  it('returns 0 when maxDateStr is falsy', () => {
    expect(activeFLWs(visits, null, 7)).toBe(0);
  });

  it('returns 0 when no visits fall in the window', () => {
    expect(activeFLWs(visits, '2025-03-20', 0)).toBe(1); // only exact match Mar 20
  });
});

// ---------------------------------------------------------------------------
// maxVisitDate()
// ---------------------------------------------------------------------------
describe('maxVisitDate', () => {
  it('returns the latest visit_date', () => {
    const visits = [
      { visit_date: '2025-03-10' },
      { visit_date: '2025-03-20' },
      { visit_date: '2025-03-15' },
    ];
    expect(maxVisitDate(visits)).toBe('2025-03-20');
  });

  it('returns null for empty array', () => {
    expect(maxVisitDate([])).toBeNull();
  });

  it('skips entries without visit_date', () => {
    const visits = [{ username: 'alice' }, { visit_date: '2025-03-05' }];
    expect(maxVisitDate(visits)).toBe('2025-03-05');
  });
});

// ---------------------------------------------------------------------------
// aggregateForReport()
// ---------------------------------------------------------------------------
describe('aggregateForReport', () => {
  const visits = [
    {
      opp_id: 1,
      opp_name: 'Alpha',
      country: 'KE',
      delivery_type: 'CHW',
      status: 'approved',
      visit_date: '2025-03-17',
      username: 'alice',
      entity_name: 'Family A',
    },
    {
      opp_id: 1,
      opp_name: 'Alpha',
      country: 'KE',
      delivery_type: 'CHW',
      status: 'approved',
      visit_date: '2025-03-18',
      username: 'bob',
      entity_name: 'Family B',
    },
    {
      opp_id: 1,
      opp_name: 'Alpha',
      country: 'KE',
      delivery_type: 'CHW',
      status: 'rejected',
      visit_date: '2025-03-19',
      username: 'carol',
      entity_name: 'Family C',
    },
  ];

  const payments = [
    {
      opp_id: 1,
      opp_name: 'Alpha',
      status_modified_date: '2025-03-17',
      usd_flw: '100',
      usd_org: '50',
    },
    {
      opp_id: 1,
      opp_name: 'Alpha',
      status_modified_date: '2025-03-18',
      usd_flw: '200',
      usd_org: '100',
    },
  ];

  it('counts only approved visits', () => {
    const report = aggregateForReport(visits, payments, [], 0);
    expect(report.total_visits).toBe(2);
  });

  it('sums total USD from all payments', () => {
    const report = aggregateForReport(visits, payments, [], 0);
    // (100+50) + (200+100) = 450
    expect(report.total_usd_distributed).toBe(450);
  });

  it('computes budget utilization when budget is provided', () => {
    const report = aggregateForReport(visits, payments, [], 1000);
    expect(report.budget_utilization_pct).toBe(45);
  });

  it('returns null budget_utilization_pct when no budget', () => {
    const report = aggregateForReport(visits, payments, [], 0);
    expect(report.budget_utilization_pct).toBeNull();
  });

  it('counts distinct countries from approved visits', () => {
    const report = aggregateForReport(visits, payments, [], 0);
    expect(report.country_count).toBe(1);
    expect(report.countries).toEqual(['KE']);
  });

  it('counts distinct families (entity_name) from approved visits', () => {
    const report = aggregateForReport(visits, payments, [], 0);
    // Only approved: Family A, Family B
    expect(report.families_reached).toBe(2);
  });

  it('produces per-opportunity summaries', () => {
    const report = aggregateForReport(visits, payments, [], 0);
    expect(report.opportunity_summaries).toHaveLength(1);
    const opp = report.opportunity_summaries[0];
    expect(opp.opp_id).toBe('1');
    expect(opp.opp_name).toBe('Alpha');
    expect(opp.visit_count).toBe(2);
    expect(opp.payment_total_usd).toBe(450);
    expect(opp.flw_count).toBe(2); // alice, bob (approved only)
  });

  it('includes weekly_spend limited to last 12 weeks', () => {
    const report = aggregateForReport(visits, payments, [], 0);
    expect(report.weekly_spend.length).toBeGreaterThan(0);
    expect(report.weekly_spend.length).toBeLessThanOrEqual(12);
    // All payments are in the same week (2025-03-17)
    expect(report.weekly_spend[0].week).toBe('2025-03-17');
    expect(report.weekly_spend[0].usd).toBe(450);
  });
});
