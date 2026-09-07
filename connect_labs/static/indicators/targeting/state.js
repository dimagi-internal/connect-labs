/* The question, in one object — and the table that says what each part of it
   invalidates.

   This file exists because of how the surface used to work. Fifteen module
   globals, and every control mutated some of them and then called its own
   combination of reload() / fetchSelection() / refreshScope() /
   renderPicker(). Nothing said which combination was right, so each new
   control guessed: the year picker refreshed the table and not the download
   link, the rank checkbox refetched the whole map it could not affect, and
   changing indicator kept a pinned admin level that the new indicator had no
   data at. Every one of those was a real bug, and they were all the same bug.

   So: a control sets a field. It never decides what to redraw. The
   INVALIDATES table decides, once, for everybody. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};

  var S = {
    // The question the page is asking.
    indicator: null,
    method: null,
    iso: '',
    level: null,
    year: null,
    rollup: true,
    threshold: null,
    // Which indicator the current threshold belongs to. A threshold is only
    // meaningful on the scale it was chosen for — 80 per 1,000 carried onto a
    // percentage measure selects almost nothing and reads as missing data.
    thresholdFor: null,
    // The costing laid over it.
    basis: null,
    unitCost: null,
    preset: null,
    // Reference data fetched from the server.
    methodInfo: null,
    scopeInfo: null,
    costInfo: null,
    indicatorMeta: {},
  };

  // What each field makes stale. Ordered coarse to fine: a channel implies
  // every channel after it, because you cannot honestly paint a selection over
  // a map that is still showing the previous question.
  var CHANNELS = ['methods', 'scope', 'map', 'selection', 'costing'];

  var INVALIDATES = {
    // A different indicator can be answered by different methods and measured
    // at a different depth, so everything is stale.
    indicator: 'methods',
    method: 'scope',
    iso: 'scope',
    // Level changes which units are drawn and which rows are returned; it
    // cannot change which methods exist.
    level: 'map',
    // Rollup and year change the ROWS, never the shapes. Refetching the map
    // for them was a wasted round trip on every toggle.
    rollup: 'selection',
    year: 'selection',
    threshold: 'selection',
    basis: 'costing',
    unitCost: 'costing',
    preset: 'costing',
  };

  var handlers = {};

  /* One ticket PER CHANNEL, not one for the whole pipeline.

     A single global ticket meant any later apply() cancelled any earlier one,
     even when they touched nothing in common. Nudge the threshold and then
     immediately edit the unit cost, and the costing run — which cannot affect
     the table — cancelled the selection fetch still in flight. The slider read
     10 and the table went on showing the answer for 90: fifteen counties where
     the truth was none, with no error anywhere. */
  var tokens = {};
  CHANNELS.forEach(function (c) {
    tokens[c] = 0;
  });

  function register(name, fn) {
    handlers[name] = fn;
  }

  function get() {
    return S;
  }

  function from(channel) {
    return CHANNELS.slice(CHANNELS.indexOf(channel));
  }

  /* Apply a patch and refresh exactly what it invalidated.

     Sequenced: every apply takes a ticket, and a handler whose ticket has been
     superseded stops rather than painting a stale answer over a fresh one.
     Only the methodology pane used to do this, so a slow selection fetch could
     land after a newer one and leave the table describing the previous
     question. */
  function apply(patch, opts) {
    var widest = null;
    Object.keys(patch || {}).forEach(function (k) {
      if (S[k] === patch[k] && !(opts && opts.force)) return;
      S[k] = patch[k];
      var ch = INVALIDATES[k];
      if (!ch) return;
      if (widest === null || CHANNELS.indexOf(ch) < CHANNELS.indexOf(widest)) {
        widest = ch;
      }
    });
    if (opts && opts.channel) {
      if (
        widest === null ||
        CHANNELS.indexOf(opts.channel) < CHANNELS.indexOf(widest)
      ) {
        widest = opts.channel;
      }
    }
    // A page left mid-failure has to be rebuilt from the widest channel, not
    // from whatever happens to have changed since.
    if (stale) widest = CHANNELS[0];
    if (widest === null) return Promise.resolve();

    var queue = from(widest);
    var failed = false;
    // Claim only the channels this run will actually execute.
    var mine = {};
    queue.forEach(function (name) {
      mine[name] = ++tokens[name];
    });

    return queue
      .reduce(function (chain, name) {
        return chain.then(function () {
          // Superseded for THIS channel — a newer run of the same channel is
          // under way, so stop rather than paint an older answer over it.
          if (mine[name] !== tokens[name]) return null;
          if (failed) return null;
          var fn = handlers[name];
          if (!fn) return null;
          return Promise.resolve()
            .then(function () {
              return fn(S);
            })
            .catch(function (err) {
              // A channel that throws used to reject the whole chain: every
              // later channel was skipped, including the one that knows how to
              // show an error, so a failed request looked like a click that did
              // nothing. Stop the run — later channels would paint over a
              // question that was never answered — but say so first.
              failed = true;
              stale = true;
              if (onError) onError(err, name);
              return null;
            });
        });
      }, Promise.resolve())
      .then(function () {
        if (!failed) stale = false;
      });
  }

  // What to do when a channel cannot answer. One hook, because the remedy is
  // the same wherever it happens: tell the reader, and do not leave figures up
  // that belong to a question nobody managed to ask.
  var onError = null;

  // Set when a run could not finish. The fields moved but the page was never
  // repainted from them, so S and the screen disagree until something redraws.
  // Until then every apply must redraw from the top, even one that changes
  // nothing — otherwise re-picking the value that failed is treated as "no
  // change" and the page stays stuck on the error.
  var stale = false;

  function setErrorHandler(fn) {
    onError = fn;
  }

  function isStale() {
    return stale;
  }

  function isCurrent(channel, mine) {
    return tokens[channel] === mine;
  }

  function ticket(channel) {
    return tokens[channel];
  }

  window.Targeting.state = {
    get: get,
    apply: apply,
    setErrorHandler: setErrorHandler,
    isStale: isStale,
    register: register,
    isCurrent: isCurrent,
    ticket: ticket,
    CHANNELS: CHANNELS,
    INVALIDATES: INVALIDATES,
  };
})();
