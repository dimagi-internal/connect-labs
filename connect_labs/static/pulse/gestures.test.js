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
  roll = 0,
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
  if (roll) {
    // Turn the whole hand about the index knuckle, clockwise as the viewer
    // sees it (the preview is mirrored), in physical (aspect-corrected) space.
    const r = (roll * Math.PI) / 180;
    for (const q of lm) {
      const ox = (q.x - x) * aspect;
      const oy = q.y - y;
      q.x = x + (Math.cos(r) * ox + Math.sin(r) * oy) / aspect;
      q.y = y - Math.sin(r) * ox + Math.cos(r) * oy;
    }
  }
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

describe('the map hook', () => {
  it('display.js exposes zoomBy for gesture zoom, and it stops the act tour', () => {
    const DISPLAY = fs.readFileSync(path.join(here, 'display.js'), 'utf8');
    const flat = DISPLAY.replace(/\s+/g, ' ');
    expect(flat).toMatch(/const handMoved = \(\) => \{ autoCycle = false;/);
  });
});

describe('open palm: spin and zoom', () => {
  const spins = (actions) => actions.filter((a) => a.type === 'spin');
  const zooms = (actions) => actions.filter((a) => a.type === 'zoom');
  const zoomed = (actions) => zooms(actions).reduce((s, a) => s + a.dz, 0);

  // An armed engine with an open palm at rest (size 0.2).
  const resting = () => {
    const { e, t } = armed();
    const held = feed(e, t, 10, hand({ palm: 'facing' }));
    return { e, t: held.t };
  };
  // Move the palm to `to` over 8 frames, then hold it there for `hold` frames.
  const pushTo = (e, t, to, hold) => {
    const move = feed(e, t, 8, (i) =>
      hand({ size: 0.2 + ((to - 0.2) * (i + 1)) / 8, palm: 'facing' }),
    );
    const held = feed(e, move.t, hold, hand({ size: to, palm: 'facing' }));
    return { move, held };
  };

  it('push and HOLD keeps zooming in, with no limit from arm reach', () => {
    const { e, t } = resting();
    const { held } = pushTo(e, t, 0.28, 90); // ~3s held
    const firstSec = zoomed(held.actions.slice(0, 30));
    const lastSec = zoomed(held.actions.filter((a) => a.t >= held.t - 1000));
    // Still zooming a full second later, at a steady rate.
    expect(lastSec).toBeGreaterThan(0.5);
    expect(zoomed(held.actions)).toBeGreaterThan(firstSec * 2);
  });

  it('further from rest zooms faster', () => {
    const near = resting();
    const a = pushTo(near.e, near.t, 0.24, 30);
    const far = resting();
    const b = pushTo(far.e, far.t, 0.3, 30);
    expect(zoomed(b.held.actions)).toBeGreaterThan(zoomed(a.held.actions) * 2);
  });

  it('pulling back past rest zooms out, and does not go back', () => {
    const { e, t } = resting();
    const { move, held } = pushTo(e, t, 0.14, 30);
    const all = [...move.actions, ...held.actions];
    expect(zoomed(all)).toBeLessThan(-0.5);
    expect(types(all)).not.toContain('back');
  });

  it('returning to rest stops the zoom', () => {
    const { e, t } = resting();
    const { held } = pushTo(e, t, 0.28, 30);
    const back = feed(e, held.t, 60, hand({ palm: 'facing' }));
    // Allow the smoothing a moment to settle, then nothing.
    expect(zooms(back.actions.filter((a) => a.t >= back.t - 1000))).toEqual([]);
  });

  it('closing and reopening the hand makes a new rest point (the clutch)', () => {
    const { e, t } = resting();
    const { held } = pushTo(e, t, 0.28, 10);
    // Relax the hand (not open), then reopen it where it is.
    const relax = feed(e, held.t, 6, hand({ size: 0.28 }));
    const reopen = feed(e, relax.t, 60, hand({ size: 0.28, palm: 'facing' }));
    expect(zooms(reopen.actions)).toEqual([]);
  });

  it('a fast push of an open palm is a zoom, never a drill-in', () => {
    // As fast as the pointing-hand push that drills in, below.
    const { e, t } = resting();
    const { actions } = feed(e, t, 20, (i) =>
      hand({ size: 0.2 * (1 + Math.min(i, 11) * 0.05), palm: 'facing' }),
    );
    expect(zoomed(actions)).toBeGreaterThan(0.3);
    expect(types(actions)).not.toContain('select');
  });

  it('a steady palm does not breathe', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 90, (i) =>
      hand({ size: 0.2 * (1 + (i % 2 ? 0.015 : -0.015)), palm: 'facing' }),
    );
    expect(zooms(actions)).toEqual([]);
  });

  it('a pointing hand pushed forward still drills in, and never zooms', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 12, (i) =>
      hand({ size: 0.2 * (1 + i * 0.05) }),
    );
    expect(types(actions)).toContain('select');
    expect(zooms(actions)).toEqual([]);
  });

  it('nothing zooms while disarmed', () => {
    const e = core.createEngine();
    const { actions } = feed(e, 0, 60, (i) =>
      hand({ size: 0.15 + (i % 30) * 0.01, palm: 'facing' }),
    );
    expect(actions).toEqual([]);
  });

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

  it('a hand whose speed hovers at the threshold fades in, not stutters', () => {
    const { e, t } = armed();
    const settle = feed(e, t, 10, hand({ palm: 'facing' }));
    // Screen speed hovering either side of spinMinSpeed (0.15/s): camera
    // steps of 0.0024-0.004 are 0.12-0.2 screen widths/s through the crop.
    let x = 0.5;
    const slow = feed(e, settle.t, 30, (i) => {
      x -= i % 2 ? 0.0024 : 0.004;
      return hand({ x, palm: 'facing' });
    });
    const fast = feed(e, slow.t, 20, (i) =>
      hand({ x: x - (i + 1) * 0.01, palm: 'facing' }),
    );
    const biggest = (acts) =>
      Math.max(0, ...spins(acts).map((a) => Math.abs(a.dx)));
    // Near the threshold the globe barely moves; it is not the full step
    // the same hand gets when clearly moving.
    expect(biggest(slow.actions)).toBeLessThan(biggest(fast.actions) / 5);
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

describe('dial', () => {
  const dials = (actions) =>
    actions.filter((a) => a.type === 'dial').map((a) => a.step);
  // An armed engine with an open palm settled upright.
  const settled = () => {
    const { e, t } = armed();
    const held = feed(e, t, 10, hand({ palm: 'facing' }));
    return { e, t: held.t };
  };

  it('turning the open palm clockwise steps once to the next', () => {
    const { e, t } = settled();
    const turn = feed(e, t, 6, (i) => hand({ palm: 'facing', roll: i * 7 }));
    const back = feed(e, turn.t, 6, (i) =>
      hand({ palm: 'facing', roll: 35 - i * 7 }),
    );
    expect(dials([...turn.actions, ...back.actions])).toEqual([1]);
  });

  it('anti-clockwise steps to the previous', () => {
    const { e, t } = settled();
    const { actions } = feed(e, t, 6, (i) =>
      hand({ palm: 'facing', roll: -i * 7 }),
    );
    expect(dials(actions)).toEqual([-1]);
  });

  it('holding the turn keeps stepping, like a jog shuttle', () => {
    const { e, t } = settled();
    const { actions } = feed(e, t, 60, (i) =>
      hand({ palm: 'facing', roll: Math.min(i * 7, 35) }),
    );
    // One on crossing, then one per repeat interval over ~1.8s held.
    expect(dials(actions).length).toBeGreaterThanOrEqual(3);
    expect(new Set(dials(actions))).toEqual(new Set([1]));
  });

  it('a turn jittering across the threshold is one step, not a burst', () => {
    const { e, t } = settled();
    // ~0.6s, under one repeat interval, wobbling either side of 25 degrees.
    const { actions } = feed(e, t, 18, (i) =>
      hand({ palm: 'facing', roll: i < 4 ? i * 7 : i % 2 ? 22 : 28 }),
    );
    expect(dials(actions)).toEqual([1]);
  });

  it('a small wobble does not step', () => {
    const { e, t } = settled();
    const { actions } = feed(e, t, 60, (i) =>
      hand({ palm: 'facing', roll: i % 2 ? 15 : -15 }),
    );
    expect(dials(actions)).toEqual([]);
  });

  it('upright is wherever the palm settled, not true vertical', () => {
    const { e, t } = armed();
    // A palm that naturally rests tilted 30 degrees -- past a step, measured
    // from vertical -- is not a turn.
    const held = feed(e, t, 30, hand({ palm: 'facing', roll: 30 }));
    expect(dials(held.actions)).toEqual([]);
  });

  it('a pointing hand turned does not dial', () => {
    const { e, t } = armed();
    const { actions } = feed(e, t, 20, (i) => hand({ roll: i * 5 }));
    expect(dials(actions)).toEqual([]);
  });

  it('turning does not zoom', () => {
    const { e, t } = settled();
    const { actions } = feed(e, t, 12, (i) =>
      hand({ palm: 'facing', roll: i * 7 }),
    );
    expect(actions.filter((a) => a.type === 'zoom')).toEqual([]);
  });

  it('nothing dials while disarmed', () => {
    const e = core.createEngine();
    const { actions } = feed(e, 0, 60, (i) =>
      hand({ palm: 'facing', roll: (i % 20) * 4 }),
    );
    expect(actions).toEqual([]);
  });
});
