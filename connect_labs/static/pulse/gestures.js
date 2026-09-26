/**
 * Pulse gesture control — drive the drill-down windows by hand, from the webcam.
 *
 * A demo toy, not a feature: the display only loads this file when the URL
 * carries `?gestures=1`, and even then the camera is not touched until the
 * Gestures button is clicked. Nothing about the ordinary display changes.
 *
 * **Nothing leaves the browser.** Frames go from the <video> element into
 * MediaPipe's WASM recogniser in this tab and the landmarks it returns are used
 * and discarded here. The only network traffic is the one-time download of the
 * recogniser itself (library + WASM from jsDelivr, model weights from Google's
 * MediaPipe bucket).
 *
 * Two halves, deliberately separate:
 *
 *   - `createEngine()` is pure. It takes one hand observation per frame (21
 *     landmarks plus MediaPipe's pose label) and a timestamp, and returns the
 *     actions to perform plus a readout. It knows nothing about the DOM, so the
 *     whole gesture grammar — including the part that matters most, that
 *     nothing fires while disarmed — is unit-tested in gestures.test.js.
 *   - The page layer owns the camera, draws the preview, and turns actions into
 *     the page's own inputs: a synthetic click where the page already listens
 *     for one, an Escape keydown for "back", and `PulseWindows.moveBy` for drag.
 *     It never reaches into windows.js internals.
 *
 * The grammar:
 *
 *   arm      open palm, held still ~0.6s. A visible frame says "armed".
 *   disarm   hand out of view ~1s, or 30s with nothing done.
 *   point    the index knuckle steers a smoothed cursor (the knuckle, not the
 *            fingertip: the fingertip moves when you pinch, so a pinch would
 *            click beside the thing you were pointing at). Point with the
 *            index finger to aim; an open hand moves the globe instead.
 *   drill in quick pinch-and-release, or a push toward the camera with a
 *            pointing (not open) hand, or a thumbs up -- which opens the
 *            partner card showing on the map without aiming at it -- or a
 *            bloom: fingertips bunched, then flung wide, which does the same.
 *   drill out closed fist, or a pull away from the camera — exactly Esc.
 *   drag     pinch, then move: the top window follows.
 *   globe    an open hand, palm to the camera: move it and the globe turns
 *            the way the hand moves, as if pushing its surface. Push it
 *            toward the camera and HOLD to keep zooming in (further = faster),
 *            pull it back past where it started to zoom out, return to stop.
 *            To move further than an arm allows, close the hand and reopen
 *            it: wherever it reopens is the new resting point.
 *   dial     in a partner window: turn the open palm like a knob, clockwise
 *            for the next opportunity, anti-clockwise for the previous; hold
 *            the turn and it keeps stepping. (The globe cannot move while a
 *            window is open, so the open palm is free for this.)
 *   swipe    reported in the readout only; not wired to anything yet.
 */
(function (global) {
  'use strict';

  /* ── pure engine ───────────────────────────────────────────────────── */

  const LM = {
    WRIST: 0,
    THUMB_TIP: 4,
    INDEX_MCP: 5,
    INDEX_TIP: 8,
    MIDDLE_MCP: 9,
    MIDDLE_TIP: 12,
    PINKY_TIP: 20,
    PINKY_MCP: 17,
  };

  const DEFAULTS = {
    // Camera frames are wider than tall; landmark x and y are each normalised
    // to their own axis, so distances are corrected by this before comparing.
    aspect: 4 / 3,
    minPoseScore: 0.6,
    armHoldMs: 600,
    // How far the wrist may wander (in hand-size units) while arming. A palm
    // flashed mid-sentence is moving; a palm held up on purpose is not.
    armStillness: 0.35,
    disarmAfterMs: 1000,
    idleDisarmMs: 30000,
    // After arming, ignore gestures briefly: the arming palm is often still
    // travelling toward the camera, which would otherwise read as a push.
    graceMs: 400,
    // A pinch was hard to land: closing the fingers moves the index knuckle
    // the cursor follows, so a tap that drifted 3% of the screen read as a
    // drag, and a deliberate pinch often outlasted the old 0.55s. A miss now
    // says why, in the panel log.
    pinchEnter: 0.33,
    pinchExit: 0.48,
    tapMaxMs: 900,
    dragStart: 0.06,
    fistHoldMs: 300,
    // Thumbs up: "yes, open it" -- the pinned partner card, or whatever the
    // cursor is on. Needs no aim precision, so it is also the easy click.
    thumbHoldMs: 300,
    // Bloom: fingertips bunched, then flung wide -- also "open it". A
    // transition, not a pose (an open palm alone arms, spins and zooms):
    // the index-to-pinky fingertip span, in hand sizes, has to go from under
    // bloomClosed to over bloomOpen within bloomWindowMs, ending open-palmed.
    bloomClosed: 0.6,
    bloomOpen: 1.1,
    bloomWindowMs: 450,
    depthWindowMs: 350,
    pushRatio: 1.3,
    pullRatio: 0.77,
    swipeWindowMs: 300,
    swipeDistance: 0.3,
    actionCooldownMs: 800,
    // The open palm drives the globe. Its shape is read from geometry, not
    // MediaPipe's pose label: fingers out (wrist to middle fingertip, in hand
    // sizes), and a wide knuckle line (index to pinky), which collapses when
    // the palm turns sideways. A fist or a relaxed curl is not an open palm.
    openReach: 1.5,
    facingWidth: 0.6,
    // The palm has to hold briefly before it moves the globe, so a hand
    // opening on its way to a pinch does not nudge it.
    palmEngageMs: 150,
    // Spin is position control -- the globe's surface follows the hand, like
    // a drag. It fades in between spinMinSpeed and twice that rather than
    // switching on at a threshold (a hand whose speed hovers around a hard
    // threshold stutters), and its velocity is smoothed. Screen widths/second.
    spinMinSpeed: 0.15,
    spinSmoothing: 0.5,
    // Screen widths of map moved per screen width of hand movement.
    spinGain: 1.5,
    // Zoom is RATE control: how far the palm is from where it settled sets how
    // FAST the map zooms, not how far -- push and hold to keep zooming in, pull
    // back past the rest point to zoom out, return to it to stop. Position
    // control ran out of arm: a stroke's worth of reach was a few zoom levels.
    // Depth is log2(hand size / resting size); inside the dead zone nothing
    // happens, then speed eases in (quadratic) up to zoomRateMax at
    // zoomDeadzone + zoomRange. Closing the hand and reopening it re-rests.
    zoomDeadzone: 0.08,
    zoomRange: 0.45,
    zoomRateMax: 2.5, // zoom levels per second
    zoomFilter: { minCutoff: 0.8, beta: 0.3, dCutoff: 1.0 },
    // Dial: roll the open palm like a knob. Past dialEnter degrees from where
    // the palm settled is one step; holding it there repeats, like a jog
    // shuttle, so a long list needs no regrip; back inside dialExit stops.
    dialEnter: 25,
    dialExit: 12,
    dialRepeatMs: 700,
    // The part of the camera frame that maps to the whole screen, so nobody has
    // to reach the very edge of the frame to reach the edge of the screen.
    crop: { x0: 0.2, x1: 0.8, y0: 0.15, y1: 0.7 },
    smoothing: { minCutoff: 1.2, beta: 1.5, dCutoff: 1.0 },
  };

  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

  function dist(a, b, aspect) {
    return Math.hypot((a.x - b.x) * (aspect || 1), a.y - b.y);
  }

  /** Wrist to middle knuckle: unaffected by what the fingers are doing. */
  function handSize(lm, aspect) {
    return dist(lm[LM.WRIST], lm[LM.MIDDLE_MCP], aspect);
  }

  /** Thumb-tip to index-tip, in hand sizes. ~0.15 pinched, ~1 open. */
  function pinchRatio(lm, aspect) {
    const s = handSize(lm, aspect);
    return s > 0 ? dist(lm[LM.THUMB_TIP], lm[LM.INDEX_TIP], aspect) / s : 1;
  }

  /** Camera coordinates to 0..1 screen coordinates: mirrored, then cropped. */
  function toScreen(p, crop) {
    const c = crop || DEFAULTS.crop;
    return {
      x: clamp01((1 - p.x - c.x0) / (c.x1 - c.x0)),
      y: clamp01((p.y - c.y0) / (c.y1 - c.y0)),
    };
  }

  /* The One Euro filter (Casiez et al. 2012): heavy smoothing when the hand is
     slow, so the cursor sits still over a target, and little when it is fast,
     so the cursor does not lag a deliberate move. */
  function oneEuro(opts) {
    const o = Object.assign({}, DEFAULTS.smoothing, opts || {});
    const alpha = (cutoff, dt) => {
      const tau = 1 / (2 * Math.PI * cutoff);
      return 1 / (1 + tau / dt);
    };
    let prev = null;
    let dPrev = 0;
    let tPrev = 0;
    return {
      reset() {
        prev = null;
      },
      filter(v, tMs) {
        if (prev == null) {
          prev = v;
          dPrev = 0;
          tPrev = tMs;
          return v;
        }
        const dt = Math.max((tMs - tPrev) / 1000, 1e-3);
        tPrev = tMs;
        const d = (v - prev) / dt;
        dPrev = dPrev + alpha(o.dCutoff, dt) * (d - dPrev);
        const cutoff = o.minCutoff + o.beta * Math.abs(dPrev);
        prev = prev + alpha(cutoff, dt) * (v - prev);
        return prev;
      },
    };
  }

  /**
   * The gesture state machine.
   *
   * `update(hand, t)` — `hand` is `{ landmarks, pose, poseScore }`, or null when
   * no hand is in view (an array is accepted, and its first hand used); `t` is
   * milliseconds. Returns
   * `{ actions: [...], readout: {...} }`. Action types:
   *
   *   armed, disarmed{reason}      state changes, for the indicator
   *   select{x,y}                  drill in at a screen point (0..1)
   *   back                         drill out one layer
   *   drag{dx,dy}, dragEnd         move the top window (0..1 units)
   *   swipe{dir}                   'left' | 'right'
   *   spin{dx,dy}                  turn the globe with the hand (0..1 units)
   *   zoom{dz}                     map zoom levels, + is in
   *   dial{step}                   +1 clockwise (as the viewer sees it), -1 anti
   *   confirm{via,x,y}             thumbs up or bloom: open the pinned card /
   *                                the target
   *   tapMissed{reason}            a pinch that did not click, and why
   *
   * Only `armed` can be produced while disarmed.
   */
  function createEngine(options) {
    const o = Object.assign({}, DEFAULTS, options || {});
    const fx = oneEuro(o.smoothing);
    const fy = oneEuro(o.smoothing);
    const fz = oneEuro(o.zoomFilter);

    let arm = 'disarmed';
    let armStart = 0;
    let armAnchor = null;
    let armedAt = 0;
    let lastSeen = -Infinity;
    let lastActionAt = 0;
    let cooldownUntil = 0;
    let pinch = null; // { startT, startP, lastP, dragging }
    let fistSince = null;
    let fistSpent = false;
    let thumb = null; // { since, p, spent } while a thumbs-up is held
    let bunched = null; // { t, p } the last frame the fingertips were together
    // Set when a fist has just closed something: opening that hand again is
    // the natural next move, and must not bloom the window straight back.
    let bloomBlocked = false;
    let history = []; // { t, size, p }
    let baseline = null;
    let palm = null; // { since, lastP, lastT, size, ref } while an open palm is up

    function endPinch(actions) {
      if (pinch && pinch.dragging) actions.push({ type: 'dragEnd' });
      pinch = null;
    }

    function disarm(actions, reason) {
      endPinch(actions);
      arm = 'disarmed';
      fistSince = null;
      fistSpent = false;
      actions.push({ type: 'disarmed', reason });
    }

    /** Roll of the hand in degrees, in mirrored (on-screen) space: 0 fingers
        up, positive turned clockwise as the viewer sees it. */
    function rollDeg(lm) {
      const w = lm[LM.WRIST];
      const m = lm[LM.MIDDLE_MCP];
      const dx = -(m.x - w.x) * o.aspect; // mirrored
      const dy = m.y - w.y;
      return (Math.atan2(dx, -dy) * 180) / Math.PI;
    }

    /** Fingers out and palm to the camera: the hand that drives the globe. */
    function isOpenPalm(lm) {
      const len = handSize(lm, o.aspect) || 1;
      const reach = dist(lm[LM.WRIST], lm[LM.MIDDLE_TIP], o.aspect) / len;
      const width = dist(lm[LM.INDEX_MCP], lm[LM.PINKY_MCP], o.aspect) / len;
      return reach >= o.openReach && width >= o.facingWidth;
    }

    function fire(actions, action, t) {
      actions.push(action);
      lastActionAt = t;
      cooldownUntil = t + o.actionCooldownMs;
    }

    /** The history sample nearest to `t - ms`, or null if we lack that much. */
    function ago(t, ms) {
      if (!history.length || t - history[0].t < ms * 0.8) return null;
      let best = history[0];
      for (const h of history) {
        if (h.t <= t - ms) best = h;
        else break;
      }
      return best;
    }

    function readout(extra) {
      return Object.assign(
        {
          arm,
          armProgress:
            arm === 'armed'
              ? 1
              : arm === 'arming'
                ? clamp01((extra.t - armStart) / o.armHoldMs)
                : 0,
          handVisible: false,
          pose: null,
          poseScore: 0,
          pinchRatio: null,
          pinched: !!pinch,
          pinchClose: 0,
          spinning: false,
          zooming: false,
          dial: 0,
          dragging: !!(pinch && pinch.dragging),
          depth: null,
          pointer: null,
        },
        extra,
      );
    }

    function update(input, t) {
      const actions = [];
      const hand = Array.isArray(input) ? input[0] : input;

      if (!hand || !hand.landmarks || hand.landmarks.length < 21) {
        if (arm === 'arming') arm = 'disarmed';
        endPinch(actions);
        palm = null;
        fistSince = null;
        thumb = null;
        bunched = null;
        if (arm === 'armed' && t - lastSeen >= o.disarmAfterMs)
          disarm(actions, 'hand gone');
        return { actions, readout: readout({ t }) };
      }

      lastSeen = t;
      const lm = hand.landmarks;
      const size = handSize(lm, o.aspect);
      const ratio = pinchRatio(lm, o.aspect);
      const raw = toScreen(lm[LM.INDEX_MCP], o.crop);
      const p = { x: fx.filter(raw.x, t), y: fy.filter(raw.y, t) };
      const confident = (hand.poseScore || 0) >= o.minPoseScore;
      const pose = confident ? hand.pose : null;

      history.push({ t, size, p });
      const horizon = Math.max(o.depthWindowMs, o.swipeWindowMs) * 2;
      while (history.length && history[0].t < t - horizon) history.shift();
      baseline = baseline == null ? size : baseline + 0.03 * (size - baseline);

      /* ── engage ── */
      if (arm !== 'armed') {
        if (pose === 'Open_Palm') {
          const wrist = lm[LM.WRIST];
          if (arm === 'disarmed') {
            arm = 'arming';
            armStart = t;
            armAnchor = wrist;
          } else if (dist(wrist, armAnchor, o.aspect) > o.armStillness * size) {
            // Moving palms are gesturing, not arming: start the hold again.
            armStart = t;
            armAnchor = wrist;
          } else if (t - armStart >= o.armHoldMs) {
            arm = 'armed';
            armedAt = t;
            lastActionAt = t;
            cooldownUntil = t + o.graceMs;
            fistSince = null;
            fistSpent = false;
            actions.push({ type: 'armed' });
          }
        } else {
          arm = 'disarmed';
        }
      } else if (t - lastActionAt >= o.idleDisarmMs) {
        disarm(actions, 'idle');
      }

      const live = arm === 'armed' && t - armedAt >= o.graceMs;
      // Motion before the engine was live is not a command. Without this, a
      // lunge that starts while arming reads as a push the moment grace ends.
      if (!live) history = [history[history.length - 1]];

      /* ── pinch: tap to drill in, hold and move to drag ── */
      if (live) {
        if (!pinch && ratio < o.pinchEnter) {
          pinch = { startT: t, startP: p, lastP: p, dragging: false };
        } else if (pinch && ratio > o.pinchExit) {
          if (pinch.dragging) {
            actions.push({ type: 'dragEnd' });
            lastActionAt = t;
          } else if (t - pinch.startT > o.tapMaxMs) {
            actions.push({ type: 'tapMissed', reason: 'held too long' });
          } else if (t < cooldownUntil) {
            actions.push({
              type: 'tapMissed',
              reason: 'too soon after the last one',
            });
          } else {
            fire(
              actions,
              { type: 'select', x: pinch.startP.x, y: pinch.startP.y },
              t,
            );
          }
          pinch = null;
        } else if (pinch) {
          const moved = Math.hypot(p.x - pinch.startP.x, p.y - pinch.startP.y);
          if (!pinch.dragging && moved > o.dragStart) pinch.dragging = true;
          if (pinch.dragging) {
            const dx = p.x - pinch.lastP.x;
            const dy = p.y - pinch.lastP.y;
            if (dx || dy) actions.push({ type: 'drag', dx, dy });
            lastActionAt = t;
          }
          pinch.lastP = p;
        }
      }

      /* ── thumbs up: "yes, open it", once per thumbs-up ── */
      if (live && pose === 'Thumb_Up' && !pinch) {
        if (!thumb) thumb = { since: t, p, spent: false };
        else if (!thumb.spent && t - thumb.since >= o.thumbHoldMs) {
          thumb.spent = true;
          // Aim where the hand was as the thumb went up.
          if (t >= cooldownUntil)
            fire(
              actions,
              { type: 'confirm', via: 'thumbs up', x: thumb.p.x, y: thumb.p.y },
              t,
            );
        }
      } else {
        thumb = null;
      }

      /* ── open palm: the globe. Move it to spin; push or pull to zoom ── */
      const open = isOpenPalm(lm);
      let spinning = false;
      let zooming = false;
      const roll = rollDeg(lm);
      if (live && open && !pinch && ratio > o.pinchExit) {
        if (!palm) {
          fz.reset();
          palm = {
            since: t,
            lastP: p,
            lastT: t,
            depthLog: 0,
            rest: null,
            vx: 0,
            vy: 0,
            neutral: roll,
            dialDir: 0,
            dialNext: 0,
          };
        }
        const logSize = fz.filter(Math.log2(size), t);
        if (t - palm.since < o.palmEngageMs) {
          // Settling: nothing moved while the palm was forming counts, and
          // however the palm settled is "upright" for the dial and "rest"
          // for the zoom.
          palm.rest = logSize;
          palm.neutral = roll;
        } else {
          /* The dial. The page uses it only while a window is open, where
             the globe cannot move -- so a palm is never both at once. */
          let tilt = roll - palm.neutral;
          if (tilt > 180) tilt -= 360;
          if (tilt < -180) tilt += 360;
          let dir = palm.dialDir;
          if (tilt >= o.dialEnter) dir = 1;
          else if (tilt <= -o.dialEnter) dir = -1;
          else if (Math.abs(tilt) <= o.dialExit) dir = 0;
          const held = dir !== 0 && Math.abs(tilt) >= o.dialEnter;
          if (
            dir !== 0 &&
            (dir !== palm.dialDir || (held && t >= palm.dialNext))
          ) {
            actions.push({ type: 'dial', step: dir });
            palm.dialNext = t + o.dialRepeatMs;
            lastActionAt = t;
          }
          palm.dialDir = dir;

          const dt = Math.max((t - palm.lastT) / 1000, 1e-3);
          const dx = p.x - palm.lastP.x;
          const dy = p.y - palm.lastP.y;
          palm.vx += o.spinSmoothing * (dx - palm.vx);
          palm.vy += o.spinSmoothing * (dy - palm.vy);
          const fade = clamp01(
            (Math.hypot(palm.vx, palm.vy) / dt - o.spinMinSpeed) /
              o.spinMinSpeed,
          );
          if (fade > 0) {
            actions.push({
              type: 'spin',
              dx: palm.vx * o.spinGain * fade,
              dy: palm.vy * o.spinGain * fade,
            });
            spinning = true;
          }

          palm.depthLog = logSize - palm.rest;
          const past = Math.abs(palm.depthLog) - o.zoomDeadzone;
          if (past > 0) {
            const k = Math.min(past / o.zoomRange, 1);
            const dz = Math.sign(palm.depthLog) * o.zoomRateMax * k * k * dt;
            actions.push({ type: 'zoom', dz });
            zooming = true;
          }
          if (spinning || zooming) lastActionAt = t;
        }
        palm.lastP = p;
        palm.lastT = t;
      } else {
        palm = null;
      }

      /* ── fist: drill out, once per fist ── */
      if (live && pose === 'Closed_Fist' && !pinch) {
        if (fistSince == null) fistSince = t;
        else if (!fistSpent && t - fistSince >= o.fistHoldMs) {
          fistSpent = true;
          bloomBlocked = true;
          bunched = null;
          // A pull usually precedes a fist, and both mean "back": if one just
          // fired, this fist is the same intent, not a second layer.
          if (t >= cooldownUntil) fire(actions, { type: 'back' }, t);
        }
      } else {
        fistSince = null;
        fistSpent = false;
      }

      /* ── bloom: bunched fingertips flung wide, "open it" ── */
      const span =
        dist(lm[LM.INDEX_TIP], lm[LM.PINKY_TIP], o.aspect) / (size || 1);
      if (span < o.bloomClosed) {
        if (!bloomBlocked) bunched = { t, p };
      } else if (span > o.bloomOpen) {
        if (
          live &&
          bunched &&
          open &&
          !pinch &&
          t - bunched.t <= o.bloomWindowMs &&
          t >= cooldownUntil
        )
          fire(
            actions,
            { type: 'confirm', via: 'bloom', x: bunched.p.x, y: bunched.p.y },
            t,
          );
        // Either way this opening is spent, including the one after a fist.
        bunched = null;
        bloomBlocked = false;
      }

      /* ── push / pull, and swipe: rate of change, not position ──
         Not for an open palm: its depth is zoom and its movement is spin. */
      if (
        live &&
        !pinch &&
        !open &&
        pose !== 'Closed_Fist' &&
        pose !== 'Thumb_Up' &&
        t >= cooldownUntil
      ) {
        const then = ago(t, o.depthWindowMs);
        const r = then ? size / then.size : 1;
        if (r >= o.pushRatio) {
          // Aim where the hand was before it moved: pushing drags the knuckle.
          fire(actions, { type: 'select', x: then.p.x, y: then.p.y }, t);
          history = [];
        } else if (r <= o.pullRatio) {
          fire(actions, { type: 'back' }, t);
          history = [];
        } else {
          const s = ago(t, o.swipeWindowMs);
          const dx = s ? p.x - s.p.x : 0;
          if (Math.abs(dx) >= o.swipeDistance) {
            fire(actions, { type: 'swipe', dir: dx > 0 ? 'right' : 'left' }, t);
            history = [];
          }
        }
      }

      return {
        actions,
        readout: readout({
          t,
          handVisible: true,
          pose: hand.pose || null,
          poseScore: hand.poseScore || 0,
          pinchRatio: ratio,
          spread: span,
          // 0 with the fingers apart, 1 at the pinch point: the cursor
          // shrinks with it, so you can see how close a pinch is.
          pinchClose: clamp01((1 - ratio) / (1 - o.pinchEnter)),
          // With a palm up, the meter shows depth from its rest point (what
          // drives zoom); otherwise the running baseline.
          depth:
            palm && palm.rest != null
              ? Math.pow(2, palm.depthLog)
              : baseline
                ? size / baseline
                : 1,
          pointer: p,
          spinning,
          zooming,
          dial: palm ? palm.dialDir : 0,
        }),
      };
    }

    return { update, state: () => arm };
  }

  const core = { createEngine, handSize, pinchRatio, toScreen, oneEuro, LM };

  /* ── page layer ────────────────────────────────────────────────────── */

  const MP_VERSION = '1.0.1';
  const MP_BASE =
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@' + MP_VERSION;
  const MODEL_URL =
    'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/' +
    'gesture_recognizer/float16/1/gesture_recognizer.task';

  /* What a gesture may drill into. An allow-list, not "anything clickable":
     an opportunity card inside the partner window navigates the tab to its
     dossier, which would end the session this toy is running in. */
  const TARGETS = [
    '.pulse-partner', // the partner card pinned on the map
    '.t-org[data-org]', // a partner in the ticker
    '.pulse-roster tbody tr[data-w]', // a worker in a partner window
    '.pulse-opp-narrow', // scope a partner window to one engagement
    '.pulse-map', // the map itself: pins the partner under the cursor
  ].join(',');

  const BONES = [
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 4],
    [0, 5],
    [5, 6],
    [6, 7],
    [7, 8],
    [5, 9],
    [9, 10],
    [10, 11],
    [11, 12],
    [9, 13],
    [13, 14],
    [14, 15],
    [15, 16],
    [13, 17],
    [17, 18],
    [18, 19],
    [19, 20],
    [0, 17],
  ];

  let session = null;

  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html) n.innerHTML = html;
    return n;
  }

  function buildUi() {
    const panel = el(
      'div',
      'pulse-gesture-panel',
      `<div class="pg-head"><span class="pg-state" data-arm="disarmed">Starting camera…</span>
         <button type="button" class="pg-off" title="Turn gesture control off">Off</button></div>
       <div class="pg-view"><video playsinline muted></video><canvas></canvas></div>
       <dl class="pg-read">
         <dt>Pose</dt><dd data-r="pose">—</dd>
         <dt>Pinch</dt><dd data-r="pinch">—</dd>
         <dt>Depth</dt><dd><span class="pg-depth"><i></i></span></dd>
       </dl>
       <ol class="pg-log"></ol>
       <div class="pg-help">Hold an open palm still to arm · pinch or push to open ·
         fist or pull back to close · thumbs up, or bunch your fingertips and
         fling them wide, opens the partner card showing (or what the cursor
         is on) · pinch and move to drag a window ·
         point with a finger to aim · open hand: move to spin the globe; push
         toward the camera and hold to keep zooming in, pull back to zoom out,
         return to stop; close and reopen the hand to reset · in a partner
         window, turn the open hand like a dial to step through its
         opportunities (hold the turn to keep going)</div>`,
    );
    const frame = el('div', 'pulse-gesture-frame');
    const cursor = el('div', 'pulse-gesture-cursor');
    frame.hidden = true;
    cursor.hidden = true;
    document.body.append(frame, cursor, panel);
    panel.querySelector('.pg-off').addEventListener('click', stop);
    return {
      panel,
      frame,
      cursor,
      video: panel.querySelector('video'),
      canvas: panel.querySelector('canvas'),
      state: panel.querySelector('.pg-state'),
      pose: panel.querySelector('[data-r="pose"]'),
      pinch: panel.querySelector('[data-r="pinch"]'),
      depth: panel.querySelector('.pg-depth i'),
      log: panel.querySelector('.pg-log'),
    };
  }

  function log(ui, text) {
    const li = el('li');
    li.textContent = new Date().toLocaleTimeString() + '  ' + text;
    ui.log.prepend(li);
    while (ui.log.children.length > 6) ui.log.lastChild.remove();
  }

  function draw(ui, hands, armed) {
    const c = ui.canvas;
    const v = ui.video;
    if (c.width !== v.videoWidth) c.width = v.videoWidth || 640;
    if (c.height !== v.videoHeight) c.height = v.videoHeight || 480;
    const g = c.getContext('2d');
    g.clearRect(0, 0, c.width, c.height);
    for (const lm of hands) drawHand(g, c, lm, armed);
  }

  function drawHand(g, c, lm, armed) {
    g.lineWidth = 3;
    g.strokeStyle = armed ? '#ffd166' : 'rgba(174,190,255,0.85)';
    g.beginPath();
    for (const [a, b] of BONES) {
      g.moveTo(lm[a].x * c.width, lm[a].y * c.height);
      g.lineTo(lm[b].x * c.width, lm[b].y * c.height);
    }
    g.stroke();
    g.fillStyle = '#fff';
    for (const p of lm) {
      g.beginPath();
      g.arc(p.x * c.width, p.y * c.height, 3, 0, Math.PI * 2);
      g.fill();
    }
  }

  function targetAt(x, y) {
    const hit = document.elementFromPoint(x, y);
    const target = hit && hit.closest(TARGETS);
    return target ? { hit, target } : null;
  }

  function mouse(type, node, x, y) {
    node.dispatchEvent(
      new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view: global,
        clientX: x,
        clientY: y,
      }),
    );
  }

  function setHot(s, target) {
    if (s.hot === target) return;
    if (s.hot) s.hot.classList.remove('pulse-gesture-hot');
    s.hot = target;
    if (target && !target.classList.contains('pulse-map'))
      target.classList.add('pulse-gesture-hot');
  }

  function perform(s, a) {
    const W = global.innerWidth;
    const H = global.innerHeight;
    const ui = s.ui;
    switch (a.type) {
      case 'armed':
        document.body.classList.add('pulse-gestures-armed');
        ui.frame.hidden = false;
        log(ui, 'armed');
        break;
      case 'disarmed':
        document.body.classList.remove('pulse-gestures-armed');
        ui.frame.hidden = true;
        setHot(s, null);
        log(ui, 'disarmed (' + a.reason + ')');
        break;
      case 'select': {
        const x = a.x * W;
        const y = a.y * H;
        const at = targetAt(x, y);
        log(ui, 'drill in' + (at ? '' : ' (nothing there)'));
        // Dispatched on the element actually under the cursor so it bubbles
        // through the page's own (often delegated) click handlers.
        if (at) mouse('click', at.hit, x, y);
        break;
      }
      case 'back':
        log(ui, 'drill out');
        // Literally Esc: whatever the page does on Escape, this does.
        document.dispatchEvent(
          new KeyboardEvent('keydown', {
            key: 'Escape',
            code: 'Escape',
            bubbles: true,
          }),
        );
        break;
      case 'drag':
        if (global.PulseWindows && global.PulseWindows.isOpen())
          global.PulseWindows.moveBy(a.dx * W, a.dy * H);
        break;
      case 'dragEnd':
        log(
          ui,
          global.PulseWindows && global.PulseWindows.isOpen()
            ? 'drag done'
            : 'pinch moved too much: read as a drag (no window to move)',
        );
        break;
      case 'zoom':
        // The map sits under an open window; moving it there would be
        // invisible and surprising when the window closes.
        if (
          global.PulseMap &&
          !(global.PulseWindows && global.PulseWindows.isOpen())
        )
          global.PulseMap.zoomBy(a.dz);
        break;
      case 'dial':
        // Only over an open window: there the globe is out of reach, so the
        // palm is free to be a dial.
        if (global.PulseWindows && global.PulseWindows.isOpen()) {
          const moved = global.PulseWindows.stepOpportunity(a.step);
          log(
            ui,
            'dial ' +
              (a.step > 0 ? 'next' : 'previous') +
              (moved ? '' : ' (nothing to step)'),
          );
        }
        break;
      case 'tapMissed':
        log(ui, 'pinch missed: ' + a.reason);
        break;
      case 'confirm': {
        // The partner card showing on the map is what "open it" means when
        // there is one -- the cursor is usually still on the map after the
        // click that pinned it, and clicking the map again would unpin it.
        const open = global.PulseWindows && global.PulseWindows.isOpen();
        const card = !open && document.querySelector('.pulse-partner');
        let node = null;
        let x = a.x * W;
        let y = a.y * H;
        if (card) {
          const r = card.getBoundingClientRect();
          x = r.left + r.width / 2;
          y = r.top + r.height / 2;
          node = card;
        } else {
          const at = targetAt(x, y);
          if (at && !at.target.classList.contains('pulse-map')) node = at.hit;
        }
        log(ui, a.via + (node ? ': open' : ' (nothing to open)'));
        if (node) mouse('click', node, x, y);
        break;
      }
      case 'spin':
        if (
          global.PulseMap &&
          !(global.PulseWindows && global.PulseWindows.isOpen())
        )
          global.PulseMap.panBy(a.dx * W, a.dy * H);
        break;
      case 'swipe':
        log(ui, 'swipe ' + a.dir + ' (not wired)');
        break;
    }
  }

  function render(s, r) {
    const ui = s.ui;
    const labels = {
      disarmed: r.handVisible ? 'Disarmed · hold an open palm' : 'Disarmed',
      arming: 'Arming… ' + Math.round(r.armProgress * 100) + '%',
      armed: 'ARMED',
    };
    let text = labels[r.arm];
    if (!r.handVisible && performance.now() - s.lastHand > 1500)
      text = "Can't see your hand — check the light behind you";
    ui.state.textContent = text;
    ui.state.dataset.arm = r.arm;
    ui.pose.textContent =
      (r.spread != null ? 'spread ' + r.spread.toFixed(2) + ' · ' : '') +
      (r.pose
        ? r.pose.replace('_', ' ') + ' ' + Math.round(r.poseScore * 100) + '%'
        : '—');
    ui.pinch.textContent =
      r.pinchRatio == null
        ? '—'
        : r.pinchRatio.toFixed(2) +
          (r.spinning || r.zooming || r.dial
            ? ' · ' +
              [
                r.spinning && 'spinning',
                r.zooming && 'zooming',
                r.dial > 0 && 'dial ▶',
                r.dial < 0 && 'dial ◀',
              ]
                .filter(Boolean)
                .join(' + ')
            : r.dragging
              ? ' · dragging'
              : r.pinched
                ? ' · pinched'
                : '');
    // 1.0 is the running baseline; the bar is centred on it.
    const d = r.depth == null ? 1 : r.depth;
    ui.depth.style.width = Math.min(Math.max((d - 0.5) * 100, 0), 100) + '%';

    const armed = r.arm === 'armed';
    if (r.pointer) {
      const x = r.pointer.x * global.innerWidth;
      const y = r.pointer.y * global.innerHeight;
      ui.cursor.hidden = false;
      ui.cursor.style.transform = `translate(${x}px, ${y}px)`;
      ui.cursor.dataset.armed = armed ? '1' : '';
      ui.cursor.dataset.pinched = r.pinched ? '1' : '';
      ui.cursor.style.setProperty('--close', String(r.pinchClose || 0));
      if (armed) {
        const at = targetAt(x, y);
        setHot(s, at ? at.target : null);
        // Let the map run its own hover hit-test, as it would for a mouse.
        if (at && at.target.classList.contains('pulse-map'))
          mouse('mousemove', at.hit, x, y);
        ui.cursor.dataset.hot =
          at &&
          (!at.target.classList.contains('pulse-map') ||
            at.target.dataset.overPartner === '1')
            ? '1'
            : '';
      } else {
        setHot(s, null);
        ui.cursor.dataset.hot = '';
      }
    } else {
      ui.cursor.hidden = true;
      setHot(s, null);
    }
  }

  /* A refused camera usually never prompts -- the browser remembers an old
     "Block", or macOS has not granted the browser the camera at all -- so the
     message has to say where the switch is, not just that it failed. */
  function whyNoCamera(err) {
    const name = (err && err.name) || '';
    const msg = (err && err.message) || String(err);
    if (!global.isSecureContext || !navigator.mediaDevices)
      return 'The camera needs https or localhost; this page is neither.';
    if (name === 'NotAllowedError' || name === 'SecurityError')
      return /system/i.test(msg)
        ? 'macOS is blocking the camera for this browser: System Settings → Privacy & Security → Camera.'
        : 'Camera blocked for this site: use the camera icon in the address bar to allow it, then click Gestures again.';
    if (name === 'NotFoundError' || name === 'OverconstrainedError')
      return 'No camera found.';
    if (name === 'NotReadableError' || name === 'AbortError')
      return 'The camera is in use by another app (a video call?).';
    return 'Could not start: ' + msg;
  }

  async function loadRecognizer() {
    const vision = await import(MP_BASE + '/vision_bundle.mjs');
    const files = await vision.FilesetResolver.forVisionTasks(
      MP_BASE + '/wasm',
    );
    const make = (delegate) =>
      vision.GestureRecognizer.createFromOptions(files, {
        baseOptions: { modelAssetPath: MODEL_URL, delegate },
        runningMode: 'VIDEO',
        numHands: 1,
      });
    try {
      return await make('GPU');
    } catch (err) {
      return make('CPU');
    }
  }

  async function start() {
    if (session) return;
    const ui = buildUi();
    const s = (session = {
      ui,
      engine: createEngine(),
      stream: null,
      recognizer: null,
      raf: 0,
      lastVideoTime: -1,
      lastHand: performance.now(),
      hot: null,
    });
    setButton(true);
    try {
      s.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: 'user' },
        audio: false,
      });
      if (session !== s) return s.stream.getTracks().forEach((t) => t.stop());
      ui.video.srcObject = s.stream;
      await ui.video.play();
      ui.state.textContent = 'Loading hand tracking…';
      s.recognizer = await loadRecognizer();
      if (session !== s) return s.recognizer.close();
    } catch (err) {
      console.warn('[pulse gestures] could not start', err);
      ui.state.textContent = whyNoCamera(err);
      return;
    }

    const tick = () => {
      if (session !== s) return;
      s.raf = requestAnimationFrame(tick);
      const v = ui.video;
      if (v.readyState < 2 || v.currentTime === s.lastVideoTime) return;
      s.lastVideoTime = v.currentTime;
      const now = performance.now();
      const res = s.recognizer.recognizeForVideo(v, now);
      const all = res.landmarks || [];
      const hands = all.map((lm, i) => {
        const top = res.gestures && res.gestures[i] && res.gestures[i][0];
        return {
          landmarks: lm,
          pose: top ? top.categoryName : null,
          poseScore: top ? top.score : 0,
        };
      });
      if (hands.length) s.lastHand = now;
      const out = s.engine.update(hands, now);
      out.actions.forEach((a) => perform(s, a));
      render(s, out.readout);
      draw(ui, all, out.readout.arm === 'armed');
    };
    tick();
  }

  function stop() {
    const s = session;
    if (!s) return;
    session = null;
    cancelAnimationFrame(s.raf);
    if (s.stream) s.stream.getTracks().forEach((t) => t.stop());
    if (s.recognizer) s.recognizer.close();
    setHot(s, null);
    document.body.classList.remove('pulse-gestures-armed');
    s.ui.panel.remove();
    s.ui.frame.remove();
    s.ui.cursor.remove();
    setButton(false);
  }

  const button = document.querySelector('#btn-gestures');
  function setButton(on) {
    if (button) button.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
  if (button)
    button.addEventListener('click', () => (session ? stop() : start()));

  global.PulseGestures = { core, start, stop };
})(window);
