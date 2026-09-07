/* The workings, rendered from the same function the download ships. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var state = T.state;
  var api = T.api;

  function refresh() {
    var el = document.getElementById('tg-methodology');
    if (!el) return Promise.resolve();
    var mine = state.ticket('selection');
    return api
      .methodology(T.controls.currentResolution())
      .then(function (d) {
        // A slower earlier request must not overwrite a newer answer.
        if (!state.isCurrent('selection', mine)) return;
        el.innerHTML = d.html || '';
      })
      .catch(function () {
        if (!state.isCurrent('selection', mine)) return;
        el.innerHTML =
          '<p class="text-amber-700">The workings could not be loaded. ' +
          'The download still carries them.</p>';
      });
  }

  window.Targeting.methodology = { refresh: refresh };
})();
