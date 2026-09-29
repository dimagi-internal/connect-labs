/*
 * marker_size.js -- how big a place is drawn in the portfolio map's Stock mode.
 *
 * Area tracks on-hand (hence the square root), against the biggest holding of
 * the same unit among places of the same KIND: a worker's 40 sachets are
 * never drawn against a warehouse's 4,000 cartons, which would shrink every
 * worker to a dot. Colour stays the band (the page's COVER table), so size
 * is how much and colour is how long it lasts. Nothing held -- or a negative
 * balance -- is the smallest size, never no marker at all.
 */
(function (root) {
  'use strict';
  var STORE = { min: 5, span: 15 };
  var WORKER = { min: 2.5, span: 6.5 };

  function markerRadius(kind, amount, biggest) {
    var scale = kind === 'user_held' ? WORKER : STORE;
    if (!amount || !biggest || amount <= 0 || biggest <= 0) return scale.min;
    return scale.min + scale.span * Math.sqrt(Math.min(amount / biggest, 1));
  }

  function biggestKey(kind, unit) {
    return (kind === 'user_held' ? 'worker|' : 'place|') + (unit || '');
  }

  root.SupplyMarkerSize = {
    markerRadius: markerRadius,
    biggestKey: biggestKey,
  };
})(typeof window !== 'undefined' ? window : globalThis);
