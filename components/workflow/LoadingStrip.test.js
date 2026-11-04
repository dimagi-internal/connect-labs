// @vitest-environment happy-dom
// The one strip every kind of report loading shows (see LoadingStrip.tsx).
import { describe, it, expect, afterEach } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { LoadingStrip, countedPercent, formatElapsed } from './LoadingStrip';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root;
let host;

function draw(props) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(React.createElement(LoadingStrip, props)));
  return host;
}

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
});

describe('countedPercent', () => {
  it('is a real count or nothing', () => {
    expect(countedPercent({ processed: 3200, total: 8900 })).toBe(36);
    expect(countedPercent({ processed: 3200, total: 0 })).toBeNull();
    expect(countedPercent({ current_stage: 2, total_stages: 6 })).toBeNull();
    expect(countedPercent(undefined)).toBeNull();
  });

  it('never claims more than all of it', () => {
    expect(countedPercent({ processed: 11604, total: 2491 })).toBe(100);
  });
});

describe('formatElapsed', () => {
  it('reads as a clock', () => {
    expect(formatElapsed(42)).toBe('42s');
    expect(formatElapsed(125)).toBe('2m 05s');
  });
});

describe('LoadingStrip', () => {
  it('during a fill: stage, step, clock, bar and that the viewer can leave', () => {
    const el = draw({
      message: 'Fetching visits',
      elapsedSeconds: 65,
      progress: { processed: 1, total: 4, current_stage: 2, total_stages: 6 },
    });
    expect(el.querySelector('[role=status]').textContent).toBe(
      'Fetching visits (step 2 of 6)',
    );
    expect(el.textContent).toContain('1m 05s');
    expect(el.textContent).toContain('You can leave this page');
    expect(
      el.querySelector('[role=progressbar]').getAttribute('aria-valuenow'),
    ).toBe('25');
  });

  it('keeps the clock out of the live region', () => {
    const el = draw({ message: 'Loading', elapsedSeconds: 5 });
    expect(el.querySelector('[role=status]').textContent).not.toContain('5s');
  });

  it('with the data on its way here: no clock, and no telling the viewer to leave', () => {
    const el = draw({ message: 'Loading the data...' });
    expect(el.textContent).not.toContain('You can leave');
    expect(el.querySelector('.tabular-nums')).toBeNull();
    expect(el.querySelector('[role=progressbar]')).toBeNull();
  });

  it('a single step says nothing about steps', () => {
    const el = draw({
      message: 'Loading',
      progress: { current_stage: 1, total_stages: 1 },
    });
    expect(el.querySelector('[role=status]').textContent).toBe('Loading');
  });
});
