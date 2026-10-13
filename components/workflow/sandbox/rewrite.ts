/**
 * The compile-time rewrite render code gets in the sandbox frame, and only there.
 * Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Compile-time
 * rewrites".
 *
 * Two things old render code does cannot be put right at run time in an
 * opaque-origin frame:
 *
 * - **Its address.** `window.location = url`, `location.href = url`,
 *   `location.assign/replace(url)`, `location.reload()`, `top.location...`: the
 *   frame would navigate itself, the host's frame-src refuses where it goes, and the
 *   host stops the frame. The Navigation API that could cancel that is disabled in
 *   an opaque-origin document, and `window.location` cannot be redefined. So every
 *   reference to the frame's GLOBAL location (not a local named `location`) is
 *   compiled to `__labsSandbox.location`, an object that reads the HOST page's
 *   address and turns every change into the bridge's `navigate` (or `setLocation`
 *   for a hash), which the host checks as it checks a link.
 * - **`confirm()`**, which is synchronous: `if (!confirm(msg)) return;`. A frame
 *   without `allow-modals` answers it false. The host's question is asynchronous and
 *   nothing synchronous can wait for it. Where the call sits directly in an `async`
 *   function, it is compiled to `(await __labsSandbox.confirm(msg))`, which is the
 *   same control flow. Where it sits in an event handler (a JSX `on*={...}`
 *   attribute, an `on*:` property of a props object, `addEventListener`'s listener),
 *   that handler is made `async` first: nothing reads an event handler's return
 *   value (unless it uses its event after the question -- `e.preventDefault()` would
 *   then run after the event is over -- when it is left alone). Anywhere else it is
 *   left alone, and the frame's `confirm` answers No and
 *   says so (frameShims.ts): the safe answer, since whatever it guards does not
 *   happen.
 *
 * Nothing here is a security boundary (the frame's origin, its CSP and the host's
 * checks are): code that wants to reach the real `window.location` still can, and
 * gets what it always got in the frame. This only makes code written for the run
 * page do in the frame what it did there.
 *
 * The plugin is written against the plugin API Babel 7 and 8 share, and is run by
 * the pinned @babel/standalone the frame loads (renderCode.tsx BABEL_URL); the tests
 * run it with that same version.
 */

/** The name render code is compiled to reach the frame's helpers by. It is a
 * parameter of the compiled wrapper (renderCode.tsx), not a global. */
export const SANDBOX_GLOBAL = '__labsSandbox';

/** Globals whose `.location` is the frame's own address. */
const LOCATION_HOLDERS = new Set([
  'window',
  'document',
  'self',
  'globalThis',
  'top',
  'parent',
  'frames',
]);
/** `window.top`, `window.parent`, `self.top`...: also windows. */
const WINDOW_PROPS = new Set(['top', 'parent', 'self', 'window', 'frames']);
/** Globals whose `.confirm(...)` is the frame's own. */
const CONFIRM_HOLDERS = new Set(['window', 'self', 'globalThis']);

/* eslint-disable @typescript-eslint/no-explicit-any */
type Babel = { types: any };
type NodePath = any;

/** The name a non-computed member (or a computed string literal) reads. */
function propertyName(node: any): string | null {
  if (!node) return null;
  if (!node.computed && node.property && node.property.type === 'Identifier') {
    return node.property.name;
  }
  if (
    node.computed &&
    node.property &&
    node.property.type === 'StringLiteral'
  ) {
    return node.property.value;
  }
  return null;
}

/** An identifier with no binding in scope: the global of that name. */
function isGlobalIdentifier(path: NodePath, names: Set<string>): boolean {
  return (
    !!path &&
    path.isIdentifier() &&
    names.has(path.node.name) &&
    !path.scope.getBinding(path.node.name)
  );
}

/** `window`, `top`, `window.parent`, `self.top.window`...: a window object. */
function isWindowExpression(path: NodePath): boolean {
  if (isGlobalIdentifier(path, LOCATION_HOLDERS)) return true;
  if (path && path.isMemberExpression()) {
    const name = propertyName(path.node);
    return (
      !!name && WINDOW_PROPS.has(name) && isWindowExpression(path.get('object'))
    );
  }
  return false;
}

/** A function nothing reads the return value of: an event handler. */
function isEventHandler(fn: NodePath): boolean {
  const node = fn.node;
  const parent = fn.parentPath;
  if (!parent) return false;
  const onName = (key: any) =>
    !!key &&
    ((key.type === 'Identifier' && /^on[A-Z]/.test(key.name)) ||
      (key.type === 'StringLiteral' && /^on[A-Z]/.test(key.value)));
  if (fn.isObjectMethod()) return onName(node.key) && !node.computed;
  if (!fn.isArrowFunctionExpression() && !fn.isFunctionExpression()) {
    return false;
  }
  // <button onClick={() => ...}>
  if (
    parent.isJSXExpressionContainer() &&
    parent.parentPath &&
    parent.parentPath.isJSXAttribute()
  ) {
    const attr = parent.parentPath.node.name;
    return (
      !!attr && attr.type === 'JSXIdentifier' && /^on[A-Z]/.test(attr.name)
    );
  }
  // h('button', { onClick: function () { ... } })
  if (parent.isObjectProperty() && parent.node.value === node) {
    return !parent.node.computed && onName(parent.node.key);
  }
  // el.addEventListener('click', () => ...)
  if (parent.isCallExpression() && parent.node.arguments[1] === node) {
    const callee = parent.node.callee;
    return (
      callee &&
      callee.type === 'MemberExpression' &&
      propertyName(callee) === 'addEventListener'
    );
  }
  return false;
}

/** Whether `fn`'s event parameter is referenced after `call` in the source. */
function usesEventAfter(fn: NodePath, call: any): boolean {
  const param = fn.node.params && fn.node.params[0];
  if (!param) return false;
  // A destructured event (`({ target }) => ...`): cannot tell, so leave it alone.
  if (param.type !== 'Identifier') return true;
  const binding = fn.scope.getBinding(param.name);
  const at = typeof call.start === 'number' ? call.start : 0;
  return !!(
    binding &&
    binding.referencePaths.some(
      (r: NodePath) => typeof r.node.start === 'number' && r.node.start > at,
    )
  );
}

/** The Babel plugin. `(babel) => ({visitor})`, as both Babel 7 and 8 take it. */
export function sandboxRewritePlugin({ types: t }: Babel) {
  const sandboxLocation = () =>
    t.memberExpression(t.identifier(SANDBOX_GLOBAL), t.identifier('location'));

  function isConfirmCallee(callee: NodePath): boolean {
    if (isGlobalIdentifier(callee, new Set(['confirm']))) return true;
    return (
      callee.isMemberExpression() &&
      propertyName(callee.node) === 'confirm' &&
      isGlobalIdentifier(callee.get('object'), CONFIRM_HOLDERS)
    );
  }

  const rewrites = {
    // window.location, document.location, top.location, window.parent.location...
    MemberExpression(path: NodePath) {
      if (propertyName(path.node) !== 'location') return;
      if (!isWindowExpression(path.get('object'))) return;
      path.replaceWith(sandboxLocation());
      path.skip();
    },
    // A bare `location`, read or assigned, when it is the global one.
    Identifier(path: NodePath) {
      if (path.node.name !== 'location') return;
      if (path.scope.getBinding('location')) return;
      const parent = path.parentPath;
      const assigned =
        parent &&
        parent.isAssignmentExpression() &&
        parent.node.left === path.node;
      if (!assigned && !path.isReferencedIdentifier()) return;
      // `{ location }` in an object literal: the value only, written out.
      if (
        parent &&
        parent.isObjectProperty() &&
        parent.node.shorthand &&
        parent.node.value === path.node
      ) {
        parent.node.shorthand = false;
      }
      path.replaceWith(sandboxLocation());
      path.skip();
    },
    CallExpression(path: NodePath) {
      if (!isConfirmCallee(path.get('callee'))) return;
      const fn = path.getFunctionParent();
      if (!fn || fn.node.generator) return;
      if (!fn.node.async) {
        if (!isEventHandler(fn)) return;
        // A handler that uses its event after the question (`e.preventDefault()`)
        // would do so after the event is over once it awaits: left alone.
        if (usesEventAfter(fn, path.node)) return;
        fn.node.async = true;
      }
      path.replaceWith(
        t.awaitExpression(
          t.callExpression(
            t.memberExpression(
              t.identifier(SANDBOX_GLOBAL),
              t.identifier('confirm'),
            ),
            path.node.arguments,
          ),
        ),
      );
      path.skip();
    },
  };

  return {
    name: 'labs-sandbox-rewrite',
    visitor: {
      // Before every other plugin and preset (JSX is still JSX here, so a handler
      // is still recognisable as one).
      Program(path: NodePath) {
        path.traverse(rewrites);
      },
    },
  };
}
