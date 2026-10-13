/**
 * Viewport-height units in labs' stylesheets read `--labs-host-vh` when it is set.
 *
 * `100vh` (h-screen, min-h-screen, max-h-[80vh], 100dvh...) becomes
 * `calc(var(--labs-host-vh, 1vh) * 100)`. Nothing sets the variable on a labs page, so
 * there it is `100vh` as before. The sandboxed frame (workflow/sandbox_frame.html) sets
 * it to the HOST window's height: the frame is as tall as its content, so its own
 * `vh` is the content's height, and content sized by it grew without end
 * (components/workflow/sandbox/viewport.ts does inline styles the same way).
 */
const UNIT = /(-?(?:\d+\.?\d*|\.\d+))(d|s|l)?vh\b/g;

function rewrite(value) {
  return value.replace(
    UNIT,
    (_m, n, kind) => `calc(var(--labs-host-vh, 1${kind || ''}vh) * ${n})`,
  );
}

function hostViewport() {
  return {
    postcssPlugin: 'labs-host-viewport',
    Declaration(decl) {
      if (decl.value.includes('--labs-host-vh')) return;
      UNIT.lastIndex = 0;
      if (!UNIT.test(decl.value)) return;
      UNIT.lastIndex = 0;
      decl.value = rewrite(decl.value);
    },
  };
}
hostViewport.postcss = true;
hostViewport.rewrite = rewrite;

module.exports = hostViewport;
