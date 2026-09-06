/* The read-once info dots. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var util = T.util;
  var state = T.state;

  function closePopovers(except) {
    document.querySelectorAll('.tg-pop').forEach(function (pop) {
      if (pop === except) return;
      pop.classList.add('hidden');
    });
    document.querySelectorAll('.tg-info').forEach(function (btn) {
      var pop = document.getElementById(btn.getAttribute('data-pop'));
      btn.setAttribute(
        'aria-expanded',
        pop && !pop.classList.contains('hidden') ? 'true' : 'false',
      );
    });
  }

  function initPopovers() {
    document.querySelectorAll('.tg-info').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var pop = document.getElementById(btn.getAttribute('data-pop'));
        if (!pop) return;
        var willOpen = pop.classList.contains('hidden');
        closePopovers();
        if (willOpen && window.Targeting.menu) window.Targeting.menu.close();
        if (willOpen) {
          pop.classList.remove('hidden');
          btn.setAttribute('aria-expanded', 'true');
        }
      });
    });
    // Clicking anywhere else, or Escape, dismisses — a popover that traps the
    // page is worse than the paragraph it replaced.
    document.addEventListener('click', function (ev) {
      if (!ev.target.closest || !ev.target.closest('.tg-pop')) closePopovers();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closePopovers();
    });
  }

  window.Targeting.popovers = { close: closePopovers, init: initPopovers };
})();
