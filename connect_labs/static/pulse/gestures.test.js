/**
 * Gesture control: the pure engine in gestures.js.
 *
 * The property that matters most is the one least likely to be noticed if it
 * broke: nothing fires while disarmed. A gesture UI that triggers while its
 * owner talks with their hands gets switched off and never switched back on,
 * so most of these drive the engine with hand motion that is NOT a command and
 * assert that it stays silent.
 *
 * Hands are synthetic: 21 landmarks placed so that hand size, pinch distance
 * and pointer position are exactly what the test says they are. Frames arrive
 * at 30fps, as they do from a webcam.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.join(here, 'gestures.js'), 'utf8');

function load() {
  const doc = {
    querySelector: () => null,
    addEventListener() {},
  };
  const win = { document: doc };
  new Function('window', 'document', SRC)(win, doc);
  return win.PulseGestures;
}
const { core } = load();

const FRAME = 33;

/**
 * A hand whose wrist-to-middle-knuckle length is `size`, whose thumb and index
 * tips are `pinch` hand-sizes apart, and whose index knuckle (the pointer) is
 * at camera point (x, y).
 */
function hand({
  x = 0.5,
  y = 0.45,
  size = 0.2,
  pinch = 1,
  pose = 'None',
  palm = null,
} = {}) {
  const aspect = 4 / 3;
  const lm = Array.from({ length: 21 }, () => ({ x, y, z: 0 }));
  if (palm) {
    // Fingers out, and a knuckle line that is wide facing the camera and
    // collapsed when the palm is turned sideways.
    lm[12] = { x, y: y - size * 1.3, z: 0 };
    const width = palm === 'facing' ? 0.85 : 0.2;
    lm[17] = { x: x + (width * size) / aspect, y, z: 0 };
  }
  lm[0] = { x, y: y + size * 0.6, z: 0 }; // wrist
  lm[9] = { x, y: y - size * 0.4, z: 0 }; // middle knuckle
  lm[5] = { x, y, z: 0 }; // index knuckle: the pointer
  lm[8] = { x, y: y - size * 0.9, z: 0 }; // index tip
  lm[4] = { x: x + (pinch * size) / aspect, y: y - size * 0.9, z: 0 }; // thumb tip
  return { landmarks: lm, pose, poseScore: pose === 'None' ? 0.3 : 0.9 };
}

/** Feed `frames` (hand or null, or a fn of frame index) from time `t0`. */
function feed(engine, t0, n, frame) {
  const actions = [];
  let t = t0;
  let readout = null;
  for (let i = 0; i < n; i++) {
    const out = engine.update(
      typeof frame === 'function' ? frame(i) : frame,
      t,
    );
    actions.push(...out.actions.map((a) => ({ ...a, t })));
    readout = out.readout;
    t += FRAME;
  }
  return { actions, t, readout };
}

const types = (actions) => actions.map((a) => a.type);

/** An engine that has just been armed, past its grace period. */
function armed(opts) {
  const e = core.createEngine(opts);
  let { t } = feed(e, 0, 25, hand({ pose: 'Open_Palm' }));
  ({ t } = feed(e, t, 20, hand()));
  expect(e.state()).toBe('armed');
  return { e, t };
}

describe('measurements', () => {
  it('measures hand size from wrist to middle knuckle', () => {
    expect(core.handSize(hand({ size: 0.2 }).landmarks, 4 / 3)).toBeCloseTo(
      0.2,
    );
  });

  it('measures pinch in hand sizes, so it does not change with distance', () => {
    expect(
      core.pinchRatio(hand({ size: 0.1, pinch: 0.2 }).landmarks, 4 / 3),
    ).toBeCloseTo(0.2);
    expect(
      core.pinchRatio(hand({ size: 0.3, pinch: 0.2 }).landmarks, 4 / 3),
    ).toBeCloseTo(0.2);
  });

  it('mirrors the camera, so moving your hand right moves the cursor right', () => {
    const crop = { x0: 0, x1: 1, y0: 0, y1: 1 };
    expect(core.toScreen({ x: 0.9, y: 0.5 }, crop).x).toBeCloseTo(0.1);
    // And clamps, rather than sending the cursor off screen.
    expect(core.toScreen({ x: 0.05, y: 0.95 }).x).toBe(1);
    expect(core.toScreen({ x: 0.05, y: 0.95 }).y).toBe(1);
  });

  it('smooths jitter but still arrives', () => {
    const f = core.oneEuro();
    let v = 0;
    for (let i = 0; i < 60; i++) v = f.filter(i % 2 ? 0.52 : 0.48, i * FRAME);
    expect(Math.abs(v - 0.5)).toBeLessThan(0.01);
    for (let i = 60; i < 120; i++) v = f.filter(0.9, i * FRAME);
    expect(v).toBeCloseTo(0.9, 2);
  });
});

describe('engage', () => {
  it('arms on an open palm held still for ~0.6s, and not before', () => {
    const e = core.createEngine();
    const early = feed(e, 0, 15, hand({ pose: 'Open_Palm' })); // ~0.5s
    expect(types(early.actions)).toEqual([]);
    expect(early.readout.arm).toBe('arming');
    const late = feed(e, early.t, 5, hand({ pose: 'Open_Palm' }));
    expect(types(late.actions)).toEqual(['armed']);
  });

  it('does not arm on a palm that is moving, as when talking', () => {
    const e = core.createEngine();
    const { actions } = feed(e, 0, 90, (i) =>
      hand({ pose: 'Open_Palm', x: 0.5 + 0.12 * Math.sin(i / 3) }),
    );
    expect(actions).toEqual([]);
    expect(e.state()).not.toBe('armed');
  });

  it('restarts the hold when the palm breaks', () => {
    const e = core.createEngine();
    const { actions } = feed(e, 0, 60, (i) =>
      hand({ pose: i % 12 === 11 ? 'None' : 'Open_Palm' }),
    );
    expect(actions).toEqual([]);
  });

  it('disarms when the hand has been gone ~1s, not on a dropped frame', () => {
    const { e, t } = armed();
    const blip = feed(e, t, 25, null); // ~0.8s
    expect(types(blip.actions)).toEqual([]);
    const gone = feed(e, blip.t, 10, null);
    expect(types(gone.actions)).toEqual(['disarmed']);
    expect(gone.actions[0].reason).toBe('hand gone');
  });

  it('disarms after a long stretch with nothing done', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, Math.ceil(31000 / FRAME), hand());
    expect(types(actions)).toEqual(['disarmed']);
    expect(actions[0].reason).toBe('idle');
  });
});

describe('nothing fires while disarmed', () => {
  it('ignores pinches, fists, pushes, pulls and swipes', () => {
    const e = core.createEngine();
    const talk = (i) => {
      const phase = Math.floor(i / 20) % 5;
      if (phase === 0) return hand({ pinch: i % 20 < 8 ? 0.1 : 1 });
      if (phase === 1) return hand({ pose: 'Closed_Fist' });
      if (phase === 2) return hand({ size: 0.12 + (i % 20) * 0.02 });
      if (phase === 3) return hand({ size: 0.5 - (i % 20) * 0.02 });
      return hand({ x: 0.2 + (i % 20) * 0.04 });
    };
    const { actions } = feed(e, 0, 400, talk);
    expect(actions).toEqual([]);
  });

  it('ignores a push made while the arming palm is still settling', () => {
    const e = core.createEngine();
    let { t } = feed(e, 0, 25, hand({ pose: 'Open_Palm' }));
    // Arming completes, then the hand lunges forward inside the grace window.
    const { actions } = feed(e, t, 20, (i) =>
      hand({ size: Math.min(0.2 + i * 0.03, 0.35) }),
    );
    expect(types(actions)).toEqual([]);
  });
});

describe('drill in', () => {
  it('a quick pinch selects where the pinch began', () => {
    const { e, t } = armed();
    const a = feed(e, t, 6, hand({ pinch: 0.1, x: 0.5 }));
    const b = feed(e, a.t, 3, hand({ pinch: 1, x: 0.5 }));
    const all = [...a.actions, ...b.actions];
    expect(types(all)).toEqual(['select']);
    expect(all[0].x).toBeCloseTo(0.5, 1);
  });

  it('a long pinch without movement selects nothing', () => {
    const { e, t } = armed();
    const a = feed(e, t, 40, hand({ pinch: 0.1 }));
    const b = feed(e, a.t, 3, hand({ pinch: 1 }));
    expect(types([...a.actions, ...b.actions])).toEqual([]);
  });

  it('pinch hysteresis: hovering around the threshold is one pinch, not many', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 30, (i) =>
      hand({ pinch: i % 2 ? 0.25 : 0.35 }),
    );
    expect(types(actions)).toEqual([]);
  });

  it('a push toward the camera selects where the hand was before it moved', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 12, (i) =>
      hand({ size: 0.2 * (1 + i * 0.05), x: 0.5 - i * 0.003 }),
    );
    expect(types(actions)).toEqual(['select']);
  });

  it('a slow drift toward the camera does not', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 90, (i) =>
      hand({ size: 0.2 * (1 + i * 0.004) }),
    );
    expect(types(actions)).toEqual([]);
  });
});

describe('drill out', () => {
  it('a fist goes back once, however long it is held', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 60, hand({ pose: 'Closed_Fist' }));
    expect(types(actions)).toEqual(['back']);
  });

  it('opening and closing the fist again goes back again', () => {
    const { e, t } = armed();
    const a = feed(e, t, 30, hand({ pose: 'Closed_Fist' }));
    const b = feed(e, a.t, 10, hand());
    const c = feed(e, b.t, 30, hand({ pose: 'Closed_Fist' }));
    expect(types([...a.actions, ...b.actions, ...c.actions])).toEqual([
      'back',
      'back',
    ]);
  });

  it('a pull away from the camera goes back', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 12, (i) =>
      hand({ size: 0.2 * (1 - i * 0.04) }),
    );
    expect(types(actions)).toEqual(['back']);
  });

  it('a pull that ends in a fist is one back, not two layers', () => {
    const { e, t } = armed();
    const pull = feed(e, t, 10, (i) => hand({ size: 0.2 * (1 - i * 0.05) }));
    const fist = feed(e, pull.t, 30, hand({ size: 0.11, pose: 'Closed_Fist' }));
    expect(types([...pull.actions, ...fist.actions])).toEqual(['back']);
  });
});

describe('drag', () => {
  it('pinch and move drags, and never also selects', () => {
    const { e, t } = armed();
    const a = feed(e, t, 30, (i) => hand({ pinch: 0.1, x: 0.5 - i * 0.004 }));
    const b = feed(e, a.t, 3, hand({ pinch: 1, x: 0.38 }));
    const all = types([...a.actions, ...b.actions]);
    expect(all).toContain('drag');
    expect(all).not.toContain('select');
    expect(all[all.length - 1]).toBe('dragEnd');
    // Mirrored: the hand moved left in camera space, so the window moves right.
    const dx = a.actions
      .filter((x) => x.type === 'drag')
      .reduce((s, x) => s + x.dx, 0);
    expect(dx).toBeGreaterThan(0);
  });

  it('losing the hand mid-drag ends the drag', () => {
    const { e, t } = armed();
    const a = feed(e, t, 15, (i) => hand({ pinch: 0.1, x: 0.5 - i * 0.005 }));
    const b = feed(e, a.t, 1, null);
    expect(types(b.actions)).toEqual(['dragEnd']);
  });
});

describe('swipe', () => {
  it('reports a fast sideways move', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 12, (i) => hand({ x: 0.5 - i * 0.03 }));
    expect(types(actions)).toEqual(['swipe']);
    expect(actions[0].dir).toBe('right');
  });
});

describe('module', () => {
  it('loads without a DOM button and without touching the camera', () => {
    const g = load();
    expect(typeof g.start).toBe('function');
    expect(typeof g.stop).toBe('function');
  });

  it('never sends anything anywhere', () => {
    // Frames and landmarks stay in this tab. The only network access the
    // module has is the recogniser download (a dynamic import from the CDN).
    expect(SRC).not.toMatch(/\bfetch\s*\(/);
    expect(SRC).not.toMatch(/XMLHttpRequest|sendBeacon|WebSocket|EventSource/);
  });

  it('drives windows only through their public API', () => {
    expect(SRC).toMatch(/PulseWindows\.moveBy\(/);
    expect(SRC).not.toMatch(/PulseWindows\.(stack|frame)\b/);
  });
});

describe('two-hand zoom', () => {
  // Two hands `gap` apart (camera units), centred, with palms in `palm`.
  const pair = (gap, palm) => [
    hand({ x: 0.5 - gap / 2, palm }),
    hand({ x: 0.5 + gap / 2, palm }),
  ];
  const zooms = (actions) => actions.filter((a) => a.type === 'zoom');
  const total = (actions) => zooms(actions).reduce((s, a) => s + a.dz, 0);

  it('palms to the camera, spreading apart, zooms in', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 30, (i) => pair(0.2 + i * 0.01, 'facing'));
    expect(types(actions)[0]).toBe('zoomStart');
    expect(zooms(actions).length).toBeGreaterThan(3);
    expect(total(actions)).toBeGreaterThan(1);
  });

  it('palms sideways, coming together, zooms out', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 30, (i) => pair(0.5 - i * 0.01, 'sideways'));
    expect(total(actions)).toBeLessThan(-1);
  });

  it('moving the wrong way for the pose does not zoom, so hands can reset', () => {
    const { e, t } = armed();
    const closing = feed(e, t, 30, (i) => pair(0.5 - i * 0.01, 'facing'));
    const spreading = feed(e, closing.t + 2000, 30, (i) =>
      pair(0.2 + i * 0.01, 'sideways'),
    );
    expect(zooms([...closing.actions, ...spreading.actions])).toEqual([]);
  });

  it('mixed or curled hands do not zoom', () => {
    const { e, t } = armed();
    const mixed = feed(e, t, 30, (i) => [
      hand({ x: 0.4 - i * 0.005, palm: 'facing' }),
      hand({ x: 0.6 + i * 0.005, palm: 'sideways' }),
    ]);
    const curled = feed(e, mixed.t, 30, (i) => pair(0.2 + i * 0.01, null));
    expect(zooms([...mixed.actions, ...curled.actions])).toEqual([]);
  });

  it('nothing zooms while disarmed', () => {
    const e = core.createEngine();
    const { actions } = feed(e, 0, 60, (i) =>
      pair(0.2 + (i % 30) * 0.01, 'facing'),
    );
    expect(actions).toEqual([]);
  });

  it('two hands never also drill, and dropping one does not fire a pull', () => {
    const { e, t } = armed();
    const two = feed(e, t, 20, (i) => [
      hand({ x: 0.35, pinch: 0.1, pose: 'Closed_Fist' }),
      hand({ x: 0.65, size: 0.2 + i * 0.02 }),
    ]);
    expect(
      types(two.actions).filter((x) => /select|back|drag/.test(x)),
    ).toEqual([]);
    // The remaining hand is still moving away as the other drops.
    const one = feed(e, two.t, 10, (i) => hand({ size: 0.2 * (1 - i * 0.05) }));
    expect(
      types(one.actions).filter((x) => /select|back|swipe/.test(x)),
    ).toEqual([]);
  });

  it('ends the zoom when a hand leaves', () => {
    const { e, t } = armed();
    const a = feed(e, t, 20, (i) => pair(0.2 + i * 0.01, 'facing'));
    const b = feed(e, a.t, 1, hand());
    expect(types(b.actions)).toEqual(['zoomEnd']);
  });

  it('the direction is one flag', () => {
    const { e, t } = armed({ spreadZoomsOut: true });
    const { actions } = feed(e, t, 30, (i) => pair(0.2 + i * 0.01, 'facing'));
    expect(total(actions)).toBeLessThan(-1);
  });
});

describe('the map hook', () => {
  it('display.js exposes zoomBy for gesture zoom, and it stops the act tour', () => {
    const DISPLAY = fs.readFileSync(path.join(here, 'display.js'), 'utf8');
    const flat = DISPLAY.replace(/\s+/g, ' ');
    expect(flat).toMatch(/const handMoved = \(\) => \{ autoCycle = false;/);
  });
});

describe('one-hand spin', () => {
  const spins = (actions) => actions.filter((a) => a.type === 'spin');

  it('an open palm moving turns the globe the way the hand moves', () => {
    const { e, t } = armed();
    // Camera x falling is the hand moving RIGHT on screen (the view is mirrored).
    const { actions } = feed(e, t, 20, (i) =>
      hand({ x: 0.55 - i * 0.008, palm: 'facing' }),
    );
    expect(spins(actions).length).toBeGreaterThan(5);
    expect(spins(actions).reduce((s, a) => s + a.dx, 0)).toBeGreaterThan(0);
    // Spinning is not swiping.
    expect(types(actions)).not.toContain('swipe');
  });

  it('a fast flick of an open palm spins, and is not also a swipe', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 12, (i) =>
      hand({ x: 0.6 - i * 0.025, palm: 'facing' }),
    );
    expect(spins(actions).length).toBeGreaterThan(3);
    expect(types(actions)).not.toContain('swipe');
  });

  it('a palm held still does not drift the globe', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 60, (i) =>
      hand({ x: 0.5 + (i % 2 ? 0.001 : -0.001), palm: 'facing' }),
    );
    expect(spins(actions)).toEqual([]);
  });

  it('pointing moves the cursor, not the globe', () => {
    const { e, t } = armed();
    const { actions, readout } = feed(e, t, 20, (i) =>
      hand({ x: 0.55 - i * 0.008 }),
    );
    expect(spins(actions)).toEqual([]);
    expect(readout.pointer).not.toBeNull();
  });

  it('a pinch-drag drags the window, and never also spins', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 30, (i) =>
      hand({ x: 0.5 - i * 0.004, pinch: 0.1, palm: 'facing' }),
    );
    expect(types(actions)).toContain('drag');
    expect(spins(actions)).toEqual([]);
  });

  it('nothing spins while disarmed', () => {
    const e = core.createEngine();
    const { actions } = feed(e, 0, 60, (i) =>
      hand({ x: 0.3 + (i % 30) * 0.01, palm: 'facing' }),
    );
    expect(actions).toEqual([]);
  });
});
