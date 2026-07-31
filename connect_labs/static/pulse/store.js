/**
 * PulseStore — one clock and one data subscription, shared by every card.
 *
 * This is the reason Pulse is its own app rather than a `pages` surface. If
 * each card fetched independently, the map and the ticker would disagree about
 * what time it is during replay, and "pause" would only pause some of them.
 *
 * Cards never fetch. They subscribe, and they render whatever the store says
 * the current moment contains.
 *
 * Two modes:
 *   live   — poll /api/events/ from a cursor; events surface as they arrive.
 *   replay — load a bounded window and play it back on FIELD time, because a
 *            worker who syncs 20 visits at once actually delivered them across
 *            the morning. Pacing on arrival order would render real work as an
 *            artificial burst.
 */
(function (global) {
  'use strict';

  const DEFAULTS = {
    base: '/labs/pulse',
    program: null,
    mode: 'replay',
    speed: 240,
    replayHours: 48,
    livePollMs: 15000,
    // Replay-seconds of silence tolerated before jumping to the next event.
    // 20 min keeps natural gaps between visits feeling real while collapsing
    // the overnight dead zone.
    deadAirSkipSeconds: 20 * 60,
    maxSparks: 400,
  };

  class PulseStore {
    constructor(options) {
      this.opts = Object.assign({}, DEFAULTS, options || {});
      this.mode = this.opts.mode;
      this.program = this.opts.program || null;
      this.speed = this.opts.speed;
      this.playing = true;

      this.summary = null;
      this.events = [];
      this.fields = [];
      this.cursor = null;
      // Live arrivals that landed while paused, flushed in order on resume.
      this._heldLive = [];

      this.clock = null;
      this.windowStart = null;
      this.windowEnd = null;
      this.emitIndex = 0;

      // Accumulators for the current pass.
      this.counts = { verified: 0, total: 0, flagged: 0, usd: 0 };
      this.recent = [];
      this.ingest = { live_ok: false, message: 'Loading…' };

      this._subscribers = new Map();
      this._lastFrame = 0;
      this._livePollTimer = null;
    }

    /* ---- subscription ---------------------------------------------- */

    on(event, fn) {
      if (!this._subscribers.has(event))
        this._subscribers.set(event, new Set());
      this._subscribers.get(event).add(fn);
      return () => this._subscribers.get(event).delete(fn);
    }

    emit(event, payload) {
      const subs = this._subscribers.get(event);
      if (!subs) return;
      for (const fn of subs) {
        try {
          fn(payload, this);
        } catch (err) {
          // One broken card must not take down the display. On a wall screen
          // nobody is watching a console, so a throwing card should degrade to
          // a stale card, not a blank page.
          console.error('[pulse] subscriber failed for', event, err);
        }
      }
    }

    /* ---- data ------------------------------------------------------- */

    async start() {
      await this.refreshSummary();
      if (this.mode === 'replay') {
        await this.loadReplayWindow();
      } else {
        await this.startLive();
      }
      requestAnimationFrame((t) => this._tick(t));
      return this;
    }

    async refreshSummary() {
      const res = await fetch(this._url('/api/summary/'), {
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) throw new Error(`summary ${res.status}`);
      this.summary = await res.json();
      this.ingest = this.summary.ingest || {};
      this.emit('summary', this.summary);
      this.emit('ingest', this.ingest);
      return this.summary;
    }

    /* Every read has to carry the filter. A single endpoint that forgot it
       would mix another programme's services into a filtered view, which is
       worse than not filtering at all. */
    _url(path, params) {
      const u = new URLSearchParams(params || {});
      if (this.program) u.set('program', this.program);
      const q = u.toString();
      return `${this.opts.base}${path}${q ? '?' + q : ''}`;
    }

    /* Re-fetch EVERYTHING. The headline figures are server-side aggregates, so
       a filter that only redrew the map would leave whole-estate totals above
       one programme's points. */
    async setProgram(programId) {
      const next = programId || null;
      if (next === this.program) return;
      this.program = next;
      if (this._livePollTimer) clearInterval(this._livePollTimer);
      this._heldLive.length = 0;
      this.events = [];
      this.recent.length = 0;
      this.cursor = null;
      this.counts = { total: 0, verified: 0, usd: 0, flagged: 0 };
      this.emit('counts', this.counts);
      this.emit('backfill', {});
      await this.refreshSummary();
      if (this.mode === 'replay') await this.loadReplayWindow();
      else await this.startLive();
      this.emit('control', { program: this.program });
    }

    async loadReplayWindow(hours) {
      const h = hours || this.opts.replayHours;
      const res = await fetch(this._url('/api/replay/', { hours: h }));
      if (!res.ok) throw new Error(`replay ${res.status}`);
      const payload = await res.json();

      this.fields = payload.fields;
      this.events = payload.events.map((row) =>
        this._decode(row, payload.fields),
      );
      this.events.sort((a, b) => a.field_ts - b.field_ts);
      this.ingest = payload.ingest || this.ingest;

      if (this.events.length) {
        this.windowStart = this.events[0].field_ts;
        this.windowEnd = this.events[this.events.length - 1].field_ts;
        // Open partway in, so the display never greets a viewer with zeroes.
        this._seek(0.42);
      }
      this.emit('window', payload.window);
      return payload;
    }

    async startLive() {
      const poll = async () => {
        try {
          const url = this.cursor
            ? this._url('/api/events/', { since: this.cursor })
            : this._url('/api/events/', { limit: 200 });
          const res = await fetch(url);
          if (!res.ok) throw new Error(`events ${res.status}`);
          const payload = await res.json();

          this.fields = payload.fields;
          this.ingest = payload.ingest || this.ingest;
          this.emit('ingest', this.ingest);

          const fresh = payload.events.map((row) =>
            this._decode(row, payload.fields),
          );
          if (payload.cursor) this.cursor = payload.cursor;
          // Paused in live mode holds arrivals rather than dropping them: the
          // point of pausing a wall display is to stop and point at a frame,
          // and losing the services that landed while you talked would make
          // the totals disagree with the ticker on resume.
          if (this.playing) for (const ev of fresh) this._deliver(ev);
          else this._heldLive.push(...fresh);
        } catch (err) {
          console.error('[pulse] live poll failed', err);
          this.ingest = Object.assign({}, this.ingest, {
            live_ok: false,
            message: 'Lost contact with the server — not live.',
          });
          this.emit('ingest', this.ingest);
        }
      };
      await poll();
      this._livePollTimer = setInterval(poll, this.opts.livePollMs);
    }

    _decode(row, fields) {
      const ev = {};
      fields.forEach((name, i) => (ev[name] = row[i]));
      return ev;
    }

    /* ---- clock ------------------------------------------------------ */

    _seek(fraction) {
      this.clock =
        this.windowStart + (this.windowEnd - this.windowStart) * fraction;
      this.emitIndex = 0;
      this.counts = { verified: 0, total: 0, flagged: 0, usd: 0 };
      this.recent = [];
      // Bank everything before the cursor without animating it, so the counters
      // open on a real number rather than climbing from zero.
      while (
        this.emitIndex < this.events.length &&
        this.events[this.emitIndex].field_ts <= this.clock
      ) {
        this._account(this.events[this.emitIndex++]);
      }
      const from = Math.max(0, this.emitIndex - 8);
      this.recent = this.events.slice(from, this.emitIndex).reverse();
      this.emit('seek', { clock: this.clock });
      this.emit('counts', this.counts);
      this.emit('backfill', this.events.slice(0, this.emitIndex));
    }

    _account(ev) {
      this.counts.total += 1;
      if (ev.status === 'approved') {
        this.counts.verified += 1;
        if (ev.usd) this.counts.usd += ev.usd;
      }
      if (ev.flag_type) this.counts.flagged += 1;
    }

    _deliver(ev) {
      this._account(ev);
      this.recent.unshift(ev);
      if (this.recent.length > 40) this.recent.pop();
      this.emit('event', ev);
      this.emit('counts', this.counts);
    }

    _tick(now) {
      requestAnimationFrame((t) => this._tick(t));
      const dt = this._lastFrame
        ? Math.min((now - this._lastFrame) / 1000, 0.25)
        : 0;
      this._lastFrame = now;
      if (!this.playing || this.mode !== 'replay' || !this.events.length)
        return;

      this.clock += dt * this.speed;
      if (this.clock > this.windowEnd) {
        this._seek(0);
        return;
      }

      let emitted = 0;
      while (
        this.emitIndex < this.events.length &&
        this.events[this.emitIndex].field_ts <= this.clock &&
        emitted < 20
      ) {
        this._deliver(this.events[this.emitIndex++]);
        emitted += 1;
      }

      // Skip dead air. The work happens on Nigerian and Kenyan working hours,
      // so a 48h window contains ~13 hours a night where nothing at all is
      // delivered -- and at any sane speed the screen sits frozen through them,
      // which reads as broken rather than as night-time.
      //
      // We jump the clock forward to the next real event instead of playing
      // the silence. Nothing is fabricated and the displayed timestamp stays
      // truthful; we just decline to spend two minutes rendering an empty
      // Tuesday night.
      if (!emitted && this.emitIndex < this.events.length) {
        const next = this.events[this.emitIndex].field_ts;
        if (next - this.clock > this.opts.deadAirSkipSeconds) {
          this.clock = next - 1;
          this.emit('skip', { to: this.clock });
        }
      }
      // At high speed the clock can outrun the animation budget; bank the
      // remainder silently so the numbers stay honest even if the map skips.
      while (
        this.emitIndex < this.events.length &&
        this.events[this.emitIndex].field_ts <= this.clock - 300
      ) {
        this._account(this.events[this.emitIndex++]);
      }
      this.emit('clock', this.clock);
    }

    /* ---- controls --------------------------------------------------- */

    setSpeed(speed) {
      this.speed = speed;
      this.emit('control', { speed });
    }

    toggle() {
      this.playing = !this.playing;
      if (this.playing && this._heldLive.length) {
        const held = this._heldLive.splice(0);
        for (const ev of held) this._deliver(ev);
      }
      this.emit('control', { playing: this.playing });
      return this.playing;
    }

    async setMode(mode) {
      if (mode === this.mode) return;
      if (this._livePollTimer) clearInterval(this._livePollTimer);
      this.mode = mode;
      // Anything held from a previous live session is stale once the source
      // changes; the replay window reloads from the server regardless.
      this._heldLive.length = 0;
      if (mode === 'replay') await this.loadReplayWindow();
      else await this.startLive();
      this.emit('control', { mode });
    }

    /**
     * Whether the display may honestly badge itself LIVE.
     * The server decides; a page that decides for itself will show a green
     * badge over data that stopped arriving days ago.
     */
    get canClaimLive() {
      return this.mode === 'live' && !!this.ingest.live_ok;
    }

    get statusLabel() {
      if (this.mode === 'live')
        return this.ingest.live_ok ? 'Live' : 'Not live';
      return 'Replay';
    }
  }

  global.PulseStore = PulseStore;
})(window);
