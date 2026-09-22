/**
 * The header widget's pure pieces. The DOM half only copies these into text
 * and attributes, so these are where a wrong number or a wrong "ago" would be
 * made. No jsdom here (see windows.test.js); the module skips its DOM boot
 * when there is no `document`.
 */
import { describe, it, expect } from 'vitest';
import { createRequire } from 'node:module';

const PulseWidget = createRequire(import.meta.url)('./widget.js');

describe('count', () => {
  it('groups thousands the way people read them', () => {
    expect(PulseWidget.count(4120)).toBe('4,120');
    expect(PulseWidget.count(0)).toBe('0');
    expect(PulseWidget.count(undefined)).toBe('0');
  });
});

describe('ago', () => {
  it('reads as a person would say it', () => {
    expect(PulseWidget.ago(12)).toBe('just now');
    expect(PulseWidget.ago(240)).toBe('4 min ago');
    expect(PulseWidget.ago(3 * 3600 + 5)).toBe('3 h ago');
    expect(PulseWidget.ago(2 * 86400)).toBe('2 d ago');
  });

  it('never says a service happened in the future', () => {
    expect(PulseWidget.ago(-30)).toBe('just now');
  });
});

describe('latest', () => {
  it('joins what it has and skips what it does not', () => {
    expect(
      PulseWidget.latest({
        service: 'Kangaroo Mother Care',
        country: 'Nigeria',
        seconds_ago: 240,
      }),
    ).toBe('Kangaroo Mother Care · Nigeria · 4 min ago');
    expect(
      PulseWidget.latest({ service: 'Malaria', country: '', seconds_ago: 30 }),
    ).toBe('Malaria · just now');
    expect(PulseWidget.latest(null)).toBe('');
  });
});

describe('bars', () => {
  const hourly = Array.from({ length: 24 }, (_, i) =>
    i === 23 ? 10 : i === 0 ? 5 : 0,
  );
  const bars = PulseWidget.bars(hourly, 72, 18);

  it('gives every hour a slot, oldest on the left', () => {
    expect(bars).toHaveLength(24);
    expect(bars[0].x).toBe(0);
    expect(bars[23].x).toBeGreaterThan(bars[22].x);
  });

  it('scales to the busiest hour and stays inside the box', () => {
    expect(bars[23].h).toBe(18);
    expect(bars[0].h).toBe(9);
    for (const b of bars) {
      expect(b.y).toBeGreaterThanOrEqual(0);
      expect(b.y + b.h).toBeCloseTo(18, 5);
      expect(b.x + b.w).toBeLessThanOrEqual(72);
    }
  });

  it('draws a quiet hour as a stub, not as nothing', () => {
    expect(bars[5].h).toBe(1);
  });

  it('survives a day with no services at all', () => {
    const empty = PulseWidget.bars(new Array(24).fill(0), 72, 18);
    expect(empty.every((b) => b.h === 1)).toBe(true);
  });
});
