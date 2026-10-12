/*
 * map_frame.js -- what the portfolio map frames, and when field workers show.
 *
 * The map is about where the supplies are, so it frames the PLACES: stores
 * and the workers drawn where their visits are. A supplier an order comes
 * from is drawn, with its route, but does not pull the frame out to reach it
 * -- a supplier 500 km away once framed the map at zoom 6, below the zoom the
 * workers' layer starts at, and a programme of twenty workers showed none.
 * The route still leaves the frame towards it, and the panel lists the order.
 *
 * One programme's workers show at every zoom: there are tens of them, not
 * thousands. Across several programmes they wait until the map is zoomed in
 * far enough to tell them apart.
 */
(function (root) {
  'use strict';
  var WORKERS_ACROSS_PROGRAMMES_MIN_ZOOM = 9;

  function frameTargets(places, origins) {
    return places.length ? places : origins;
  }

  function workerMinZoom(programCount) {
    return programCount > 1 ? WORKERS_ACROSS_PROGRAMMES_MIN_ZOOM : 0;
  }

  // Where a worker is drawn when their visits carry no GPS: their store, their
  // organisation's office, the places they restock or their country's centre.
  var STAND_INS = { parent: 1, org_hq: 1, served: 1, country: 1 };

  function plural(n, word) {
    return n + ' ' + word + (n === 1 ? '' : 's');
  }

  // The panel's line about a programme's workers. A worker without GPS on
  // their visits is never drawn as if it had some: it is counted here, and the
  // map draws it as approximate at its stand-in.
  function workersLine(workers, programCount) {
    if (!workers.length) return '';
    var line = plural(workers.length, 'field worker');
    if (programCount > 1) return line + ' — zoom in to see them.';
    line += ', drawn where their visits are.';
    var standIn = workers.filter(function (w) {
      return w._placed && STAND_INS[(w.location || {}).source];
    }).length;
    var nowhere = workers.filter(function (w) {
      return !w._placed;
    }).length;
    if (standIn)
      line +=
        ' ' +
        standIn +
        (standIn === 1 ? ' has' : ' have') +
        ' no GPS on their visits and ' +
        (standIn === 1 ? 'is' : 'are') +
        ' drawn at their store or office.';
    if (nowhere)
      line +=
        ' ' +
        nowhere +
        (nowhere === 1
          ? ' has no location and is'
          : ' have no location and are') +
        ' listed below.';
    return line;
  }

  root.SupplyMapFrame = {
    frameTargets: frameTargets,
    workerMinZoom: workerMinZoom,
    workersLine: workersLine,
  };
})(typeof window !== 'undefined' ? window : globalThis);
