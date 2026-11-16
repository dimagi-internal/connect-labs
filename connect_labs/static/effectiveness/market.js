/* Market size: the scenario's controls beside the market they give. The state, the controls and the figures are the
 * shared parts (`scenario.js`, `dials.js`, `marketview.js`); this only puts them on the page. */
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('scenario-data').textContent);
  var $ = function (id) {
    return document.getElementById(id);
  };

  var scn = window.EffScenario.create(D);
  window.EffDials.mount($('controls'), scn, D);
  var view = window.EffMarketView.create(
    {
      tiles: $('tiles'),
      note: $('sel-note'),
      byModel: $('by-model'),
      rows: $('rows'),
      more: $('more'),
    },
    D,
  );

  function say(text) {
    $('error').hidden = !text;
    $('error').textContent = text || '';
  }
  if (D.error) {
    $('notice').hidden = false;
    $('notice').textContent = D.error + ' Showing the models as published.';
  }
  scn.follow(
    D.api_url,
    function (r) {
      say('');
      view.draw(r);
    },
    say,
  );
})();
