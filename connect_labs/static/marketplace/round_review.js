// Buttons that ask the canopy agent panel to do something on a round: the round
// page's "Start AI review", and the Review tab's step buttons. Each sends the
// words its page wrote in data-ask, as if the visitor had typed them; the agent
// reads which round (and which step) from the page state. A button with
// data-ask-input names a text box whose words are appended, so a person can say
// what to change ("make cost count less"). A panel that cannot be asked yet (an
// older widget) is opened, for the visitor to ask. Buttons stay hidden until
// the panel has loaded.
(function () {
  var buttons = document.querySelectorAll(
    '#round-ai-review, [data-canopy-ask]',
  );
  if (!buttons.length) return;
  function reveal() {
    buttons.forEach(function (b) {
      b.classList.remove('hidden');
    });
  }
  document.addEventListener('canopy:ready', reveal);
  if (window.canopyHost) reveal();
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var w = window.canopyHost && window.canopyHost.widget;
      if (!w) return;
      var text = btn.getAttribute('data-ask') || '';
      var inputId = btn.getAttribute('data-ask-input');
      var typed = inputId && document.getElementById(inputId);
      if (typed && typed.value.trim()) text += ' ' + typed.value.trim();
      if (typeof w.ask === 'function') w.ask(text);
      else if (typeof w.open === 'function') w.open();
    });
  });
})();
