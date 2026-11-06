// The round page's "Start AI review" button: sends the words the page wrote in
// data-ask to the canopy agent panel, as if the visitor had typed them. The agent
// reads which round from the page state, so nothing else is needed. A panel that
// cannot be asked yet (an older widget) is opened, for the visitor to ask.
(function () {
  var btn = document.getElementById('round-ai-review');
  if (!btn) return;
  document.addEventListener('canopy:ready', function () {
    btn.classList.remove('hidden');
  });
  if (window.canopyHost) btn.classList.remove('hidden');
  btn.addEventListener('click', function () {
    var w = window.canopyHost && window.canopyHost.widget;
    if (!w) return;
    var text = btn.getAttribute('data-ask') || '';
    if (typeof w.ask === 'function') w.ask(text);
    else if (typeof w.open === 'function') w.open();
  });
})();
