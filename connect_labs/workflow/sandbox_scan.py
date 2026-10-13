"""Which workflows' render code would break in the sandbox frame: the checker.

Before ``LABS_WORKFLOW_SANDBOX`` goes to ``all`` (docs/superpowers/specs/
2026-10-12-page-sandbox-design.md, Phase 2, "Rollout"), every live workflow's
EFFECTIVE render code is read and checked for what the frame does not give it:
see the prop/method table in that spec. The ``workflow_sandbox_scan`` management
command does the enumeration; this module is the static check, kept free of I/O
so it can be tested on strings.

It is a text check, not a JavaScript parser: comments are blanked (keeping line
numbers), then each pattern below is matched. A pattern that sits a few lines
after the guard that makes it unreachable in the frame (``actions.navigate ? ... :
window.location.href = url``, the seed templates' own fallback for the plain run
page, which has no ``actions.navigate``) is reported as ``info``, not ``breaks``.

It models what the frame does to render code before it runs it (spec, "Compile-time
rewrites"; ``components/workflow/sandbox/rewrite.ts``): every reference to the global
``location`` is compiled to the host's navigate, and a ``confirm()`` directly in an
async function or an event handler to the host's question. Those report as ``info``.
A ``confirm()`` anywhere else is still ``breaks`` (the frame answers it No), and an
``EventSource`` is checked like a fetch (the host reads it, for a route it allows).

Severities:

* ``breaks`` -- would fail or stop the frame (a refused fetch or stream, a
  ``confirm`` that answers No, reaching into the page around the frame).
* ``changes`` -- works, differently (storage is in memory only).
* ``info`` -- worth knowing, nothing to fix (a navigation or question compiled to the
  host's, an ``alert`` drawn as a host notice, a dynamic fetch the scan cannot
  resolve, a guarded fallback, a CSRF read the host makes harmless).

Output carries ids, names, pattern ids and line numbers only: never code.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from connect_labs.workflow.sandbox_routes import check_path

BREAKS, CHANGES, INFO = "breaks", "changes", "info"
SEVERITY_ORDER = {BREAKS: 0, CHANGES: 1, INFO: 2}


@dataclass(frozen=True)
class Pattern:
    id: str
    severity: str
    what: str  # what happens in the frame
    fix: str  # the replacement, for --fix-suggestions
    regex: str = ""
    # A token that, appearing in the few lines before a match, means the match is a
    # fallback the frame never reaches.
    guard: str = ""


#: The frame's global ``location``, reached directly or through a window
#: (``window.``, ``document.``, ``top.``, ``window.parent.``...). A property named
#: ``location`` on anything else (``row.location``) is not it.
_LOCATION = r"(?<![\w$.])(?:(?:window|document|self|globalThis|top|parent|frames)\s*\.\s*)*location"

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "location_assign",
        INFO,
        "compiled to the host's navigate: it goes where a link would, when the host allows it",
        "nothing needed; actions.navigate(url) says the same plainly",
        rf"{_LOCATION}(?:\.href)?\s*=(?!=)|{_LOCATION}\.(?:assign|replace)\s*\(",
        guard="actions.navigate",
    ),
    Pattern(
        "location_reload",
        INFO,
        "compiled to the host's navigate to the page's own address",
        "nothing needed; actions.reload() says the same plainly",
        rf"{_LOCATION}\.reload\s*\(",
        guard="actions.reload",
    ),
    Pattern(
        "top_or_parent",
        BREAKS,
        "the frame cannot reach the page around it (cross-origin)",
        "pass what is needed as data, or use an actions.* method",
        r"(?<![\w$.])window\.(?:top|parent)\b(?!\s*\.\s*location\b)"
        r"|(?<![\w$.])(?:top|parent)\.(?:document|window|frames|postMessage|history)\b",
    ),
    Pattern(
        "confirm",
        BREAKS,
        "a confirm() outside an async function or event handler answers No in the frame "
        "(one inside them is compiled to the host's question, and is info)",
        "if (!(await view.confirm(text))) return;  (in an async function)",
        r"(?<![\w$.])(?:(?:window|self|globalThis)\.)?confirm\s*\(",
        guard="view.confirm",
    ),
    Pattern(
        "prompt",
        BREAKS,
        "window.prompt always returns null in the frame (no modals)",
        "an input in the render itself",
        r"(?<![\w$.])(?:window\.)?prompt\s*\(",
    ),
    Pattern(
        "xhr",
        BREAKS,
        "XMLHttpRequest to labs is blocked by the frame's CSP (only fetch goes through the host)",
        "fetch(path, {...})",
        r"\bXMLHttpRequest\b",
    ),
    Pattern(
        "xhr_sync",
        BREAKS,
        "a synchronous XMLHttpRequest blocks and is refused",
        "await fetch(path, {...})",
        r"\.open\s*\([^)\n]*,\s*false\s*\)",
    ),
    Pattern(
        "event_source",
        INFO,
        "an EventSource the host reads for the frame, for an address the route table allows "
        "(one it refuses is fetch_refused); it does not reconnect",
        "nothing needed",
        r"\bnew\s+(?:window\.)?EventSource\s*\(",
    ),
    Pattern(
        "dynamic_import",
        BREAKS,
        "import() of a module is refused by the frame's script-src",
        "use the libraries the shell loads (window.LabsReport, Chart, L, mapboxgl...)",
        r"(?<![\w$.])import\s*\(",
    ),
    Pattern(
        "form_action",
        BREAKS,
        "a form that really submits is refused (form-action 'none')",
        "onSubmit={e => { e.preventDefault(); ... fetch(...) }} and no action attribute",
        r"<form\b[^>]*\baction\s*=|\.submit\s*\(\s*\)",
    ),
    Pattern(
        "jquery_ajax",
        BREAKS,
        "jQuery is not loaded in the frame, and its XHR would be blocked",
        "fetch(path, {...})",
        r"(?<![\w$])(?:\$|jQuery)\.(?:ajax|get|post|getJSON)\s*\(",
    ),
    Pattern(
        "fetch_refused",
        BREAKS,
        "a labs path the sandbox route table refuses",
        "use a route the table allows (components/workflow/sandbox/routes.ts), or an actions.* method",
    ),
    Pattern(
        "fetch_external",
        BREAKS,
        "a fetch to another host is refused by the frame's connect-src",
        "fetch it on the server (a pipeline or an endpoint) and pass the result in",
    ),
    Pattern(
        "fetch_needs_run",
        BREAKS,
        "a run's endpoint, on a page (a page has no run)",
        "keep page state in the render; use a workflow if it needs a run",
    ),
    Pattern(
        "document_cookie",
        INFO,
        'document.cookie reads "" in the frame; the host adds its own CSRF token to every write',
        "nothing needed; it can be dropped",
        r"\bdocument\.cookie\b",
    ),
    Pattern(
        "form_submit",
        CHANGES,
        "a <form> whose submit is not prevented does nothing in the frame",
        "call e.preventDefault() in onSubmit",
    ),
    Pattern(
        "alert",
        INFO,
        "alert() becomes a notice drawn by the host (does not block)",
        "nothing needed; or show the message in the render",
        r"(?<![\w$.])(?:window\.)?alert\s*\(",
    ),
    Pattern(
        "storage",
        CHANGES,
        "localStorage / sessionStorage are in memory only (lost on reload)",
        "keep it in run state (onUpdateState) if it must last",
        r"\b(?:localStorage|sessionStorage)\b",
    ),
    Pattern(
        "csrf_read",
        INFO,
        "reads labs' CSRF token: the frame gets a dummy, the host adds the real one",
        "harmless; can be dropped once the workflow only runs sandboxed",
        r"csrfmiddlewaretoken|\bcsrftoken\b|csrfToken",
    ),
    Pattern(
        "eval",
        INFO,
        "eval / new Function work in the frame; check what they evaluate",
        "avoid evaluating text that was fetched",
        r"(?<![\w$.])eval\s*\(|\bnew\s+Function\s*\(",
    ),
    Pattern(
        "fetch_dynamic",
        INFO,
        "a fetch whose address the scan cannot resolve: unchecked",
        "check by hand against the route table",
    ),
    Pattern(
        "guarded_fallback",
        INFO,
        "a fallback that only runs off the sandbox (the guard before it is always present in the frame)",
        "nothing needed",
    ),
)
PATTERN_BY_ID = {p.id: p for p in PATTERNS}
_COMPILED = tuple((p, re.compile(p.regex)) for p in PATTERNS if p.regex)

_DECLARES = re.compile(r"(?:const|let|var)\s+$")

#: How many lines before a match a guard may sit.
GUARD_WINDOW = 3

#: Hosts a frame may fetch (the Mapbox paths in its connect-src).
_ALLOWED_EXTERNAL = ("https://api.mapbox.com/", "https://events.mapbox.com")


@dataclass(frozen=True)
class Finding:
    pattern: str
    severity: str
    line: int
    detail: str = ""  # a normalised labs PATH and the reason; never code

    def as_dict(self) -> dict:
        return asdict(self)


# -----------------------------------------------------------------------------
# Lexing: blank comments, keep strings and line numbers
# -----------------------------------------------------------------------------

_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")


def strip_comments(code: str) -> str:
    """``code`` with ``//`` and ``/* */`` comments replaced by spaces (newlines kept).

    Strings, template literals and regex literals are left alone, so ``'http://x'``
    is not taken for a comment.
    """
    out = []
    i, n = 0, len(code)
    last_sig = ""  # the last non-space code character, to tell a regex from a division
    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = code.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join(c if c == "\n" else " " for c in code[i:j]))
            i = j
            continue
        if ch in "'\"`":
            j = _string_end(code, i)
            out.append(code[i:j])
            i = j
            last_sig = ch
            continue
        if ch == "/" and (last_sig == "" or last_sig in _REGEX_PRECEDERS):
            j = _regex_end(code, i)
            if j:
                out.append(code[i:j])
                i = j
                last_sig = "/"
                continue
        out.append(ch)
        if not ch.isspace():
            last_sig = ch
        i += 1
    return "".join(out)


def _string_end(code: str, i: int) -> int:
    """The index just past the string literal starting at ``code[i]``."""
    quote, j, n = code[i], i + 1, len(code)
    depth = 0
    while j < n:
        c = code[j]
        if c == "\\":
            j += 2
            continue
        if quote == "`":
            if c == "$" and j + 1 < n and code[j + 1] == "{":
                depth += 1
                j += 2
                continue
            if c == "}" and depth:
                depth -= 1
            elif c == "`" and not depth:
                return j + 1
        elif c == quote or c == "\n":
            return j + 1
        j += 1
    return n


def _regex_end(code: str, i: int) -> int | None:
    j, n, in_class = i + 1, len(code), False
    while j < n:
        c = code[j]
        if c == "\n":
            return None
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and code[j].isalpha():
                j += 1
            return j
        j += 1
    return None


def _line_of(code: str, pos: int) -> int:
    return code.count("\n", 0, pos) + 1


# -----------------------------------------------------------------------------
# Fetch addresses: resolve to a representative path
# -----------------------------------------------------------------------------

_FETCH = re.compile(r"(?<![\w$.])(?:window\.)?fetch\s*\(")
#: Stands for a dynamic part of an address while it is resolved; becomes ``1``.
_DYNAMIC = "\x00"


def _read_arg(code: str, i: int) -> tuple[str, int]:
    """The argument expression starting at ``code[i]``, up to a top-level ``,`` or ``)``."""
    depth, j, n = 0, i, len(code)
    while j < n:
        c = code[j]
        if c in "'\"`":
            j = _string_end(code, j)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return code[i:j], j
            depth -= 1
        elif c == "," and depth == 0:
            return code[i:j], j
        j += 1
    return code[i:], n


def _split_plus(expr: str) -> list[str]:
    parts, depth, start, j, n = [], 0, 0, 0, len(expr)
    while j < n:
        c = expr[j]
        if c in "'\"`":
            j = _string_end(expr, j)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "+" and depth == 0:
            parts.append(expr[start:j])
            start = j + 1
        j += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts]


_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


def _declared_value(code: str, name: str, before: int) -> str | None:
    """The expression last assigned to ``const|let|var name`` before ``before``."""
    decl = re.compile(r"(?<![\w$.])(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*")
    found = None
    for m in decl.finditer(code, 0, before):
        found = m
    if not found:
        return None
    expr, _ = _read_arg(code, found.end())
    expr = expr.split(";")[0] if expr.count(";") else expr
    return expr.strip() or None


def resolve_address(expr: str, code: str, before: int, depth: int = 0) -> str | None:
    """A representative address for a fetch argument, or None when it is dynamic.

    String literals are kept; every other ``+`` operand and every ``${...}`` becomes
    ``1`` (a placeholder id); a bare identifier is followed to its declaration once
    or twice. ``'/audit/api/' + id + '/bulk-data/'`` -> ``/audit/api/1/bulk-data/``.
    """
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1].strip()
    if not expr or depth > 3:
        return None
    if _IDENT.match(expr):
        value = _declared_value(code, expr, before)
        return resolve_address(value, code, before, depth + 1) if value else None
    out = []
    for k, part in enumerate(_split_plus(expr)):
        literal = _literal(part, code, before, depth)
        if literal is not None:
            out.append(literal)
        elif k == 0 and _IDENT.match(part):
            value = _declared_value(code, part, before)
            resolved = resolve_address(value, code, before, depth + 1) if value else None
            if resolved is None:
                return None
            out.append(resolved)
        elif k == 0:
            return None
        elif _IDENT.match(part):
            # A later part named by a constant (`base + id + SUFFIX`) is that constant.
            value = _declared_value(code, part, before)
            resolved = resolve_address(value, code, before, depth + 1) if value else None
            out.append(resolved if resolved is not None and _DYNAMIC not in resolved else _DYNAMIC)
        else:
            out.append(_DYNAMIC)
    address = "".join(out)
    return address or None


def _literal(part: str, code: str, before: int, depth: int) -> str | None:
    if len(part) >= 2 and part[0] in "'\"" and part[-1] == part[0]:
        return part[1:-1]
    if len(part) >= 2 and part[0] == "`" and part[-1] == "`":
        body = part[1:-1]

        def sub(m):
            inner = m.group(1).strip()
            if _IDENT.match(inner):
                value = _declared_value(code, inner, before)
                resolved = resolve_address(value, code, before, depth + 1) if value else None
                if resolved is not None and resolved.startswith(("/", "http")):
                    return resolved
            return _DYNAMIC

        return re.sub(r"\$\{([^}]*)\}", sub, body)
    return None


def _method_of(code: str, after: int) -> str | None:
    """The literal ``method:`` of a fetch's options, ``GET`` when there are none,
    None when it is set but not a literal."""
    rest = code[after:]
    if not rest.startswith(","):
        return "GET"
    options, _ = _read_arg(code, after + 1)
    m = re.search(r"\bmethod\s*:\s*(['\"`])(\w+)\1", options)
    if m:
        return m.group(2).upper()
    if re.search(r"\bmethod\s*[:,}]", options):
        return None
    return "GET"


def representative(address: str) -> str:
    """A resolved address with its dynamic parts made concrete.

    Labs paths end in ``/``, so dynamic parts after the last literal ``/`` of the path
    are a query being built (``'/runs/history/' + qs``), and are dropped; any other
    dynamic part is an id, and becomes ``1``.
    """
    path, sep, query = address.partition("?")
    head, slash, tail = path.rpartition("/")
    if slash and _DYNAMIC in tail:
        path = head + slash
    return (path + sep + query).replace(_DYNAMIC, "1")


def check_fetch_address(address: str, method: str | None, *, is_page: bool) -> tuple[str, str] | None:
    """``(pattern id, detail)`` for one resolved fetch, or None when the host answers it."""
    address = representative(address)
    parts = urlsplit(address)
    if parts.scheme in ("data", "blob"):
        return None
    if parts.scheme or address.startswith("//"):
        if address.startswith(_ALLOWED_EXTERNAL):
            return None
        return "fetch_external", f"host {parts.netloc or '?'}"
    path = parts.path
    if not path.startswith("/"):
        return "fetch_dynamic", "relative address"
    methods = [method] if method else ["GET", "POST"]
    verdicts = [check_path(path, m) for m in methods]
    allowed = [v for v in verdicts if v.allowed]
    if not allowed:
        return "fetch_refused", f"{method or 'GET/POST'} {path}: {verdicts[0].reason}"
    if is_page and all(v.needs_run for v in allowed):
        return "fetch_needs_run", f"{method or 'GET'} {path}"
    if method is None:
        return "fetch_dynamic", f"{path}: method not a literal"
    return None


# -----------------------------------------------------------------------------
# confirm(): where the frame's compile makes it wait for the host
# -----------------------------------------------------------------------------


def blank_strings(code: str) -> str:
    """``code`` (comments already stripped) with every string literal's insides blanked.

    Quotes and newlines are kept, so positions and line numbers do not move and a
    brace in a string is not taken for code.
    """
    out, i, n = [], 0, len(code)
    while i < n:
        ch = code[i]
        if ch in "'\"`":
            j = _string_end(code, i)
            body = code[i + 1 : j - 1] if j - i >= 2 else ""
            out.append(ch + "".join(c if c == "\n" else " " for c in body) + (code[j - 1] if j - i >= 2 else ""))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_BLOCK_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "with"})
_IDENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")

# Read backwards with plain loops, not `...\s*$` regexes: those try every start
# position, and render code is author-written and long.


def _tail_word(text: str) -> tuple[str, str]:
    """The identifier ``text`` ends with (after trailing space), and what is before it."""
    end = len(text)
    while end and text[end - 1].isspace():
        end -= 1
    start = end
    while start and text[start - 1] in _IDENT_CHARS:
        start -= 1
    return text[start:end], text[:start]


def _strip_end(text: str) -> str:
    end = len(text)
    while end and text[end - 1].isspace():
        end -= 1
    return text[:end]


def _on_word(word: str) -> bool:
    return len(word) > 2 and word.startswith("on") and word[2].isupper()


def _is_handler_context(before: str) -> bool:
    """Whether a function written right after ``before`` is an event handler:
    `onClick={`, `onClick:`, or `addEventListener('click',`."""
    b = _strip_end(before)
    if b.endswith("{"):
        b2 = _strip_end(b[:-1])
        if b2.endswith("="):
            return _on_word(_tail_word(b2[:-1])[0])
        return False
    if b.endswith(":"):
        return _on_word(_tail_word(b[:-1])[0])
    if b.endswith(","):
        b2 = _strip_end(b[:-1])
        if b2 and b2[-1] in "'\"`":
            opened = b2.rfind(b2[-1], 0, len(b2) - 1)
            b3 = _strip_end(b2[:opened]) if opened >= 0 else ""
            if b3.endswith("("):
                return _tail_word(b3[:-1])[0] == "addEventListener"
    return False


def _open_paren_before(code: str, close: int) -> int | None:
    depth = 0
    for k in range(close, -1, -1):
        c = code[k]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                return k
    return None


def _function_at(code: str, brace: int) -> tuple[bool, bool] | None:
    """For the ``{`` at ``brace``: None when it opens no function body, else
    ``(is_async, is_handler)``."""
    head = _strip_end(code[:brace])
    if head.endswith("=>"):
        before = _strip_end(head[:-2])
        if before.endswith(")"):
            open_at = _open_paren_before(before, len(before) - 1)
            if open_at is None:
                return None
            before = before[:open_at]
        else:
            _, before = _tail_word(before)
        word, rest = _tail_word(before)
        is_async = word == "async"
        return is_async, _is_handler_context(rest if is_async else before)
    if head.endswith(")"):
        open_at = _open_paren_before(head, len(head) - 1)
        if open_at is None:
            return None
        word, rest = _tail_word(head[:open_at])
        if not word or word in _BLOCK_KEYWORDS:
            return None
        if word == "function":  # function (...) {
            w2, r2 = _tail_word(rest)
            return (True, False) if w2 == "async" else (False, _is_handler_context(rest))
        w2, r2 = _tail_word(rest)
        if w2 == "function":  # function name(...) {
            w3, _ = _tail_word(r2)
            return (True, False) if w3 == "async" else (False, _is_handler_context(r2))
        # A method `name(...) {` (`async name(...) {`): a handler only if an on* one.
        return w2 == "async", _on_word(word)
    return None


def confirm_is_awaited(code: str, pos: int) -> bool:
    """Whether the frame's compile makes the ``confirm(`` at ``pos`` wait for the host:
    its nearest enclosing function is ``async``, or is an event handler (rewrite.ts).

    ``code`` has its comments stripped and its strings blanked. A heuristic on text,
    not a parse: when it cannot tell, it says no, so the scan over-reports rather
    than under.
    """
    depth = 0
    for k in range(pos - 1, -1, -1):
        c = code[k]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth:
                depth -= 1
                continue
            found = _function_at(code, k)
            if found is None:
                continue  # a block or an object: keep looking outward
            is_async, is_handler = found
            return is_async or is_handler
    return False


# -----------------------------------------------------------------------------
# The check
# -----------------------------------------------------------------------------


def scan_render_code(code: str | None, *, is_page: bool = False) -> list[Finding]:
    """Every pattern in ``code`` that would break or change in the frame."""
    if not code:
        return []
    clean = strip_comments(code)
    blank = blank_strings(clean)
    lines = clean.split("\n")
    findings: list[Finding] = []

    def guarded(line_no: int, guard: str) -> bool:
        lo = max(0, line_no - 1 - GUARD_WINDOW)
        return any(guard in ln for ln in lines[lo:line_no])

    for pattern, rx in _COMPILED:
        for m in rx.finditer(clean):
            line = _line_of(clean, m.start())
            if pattern.id == "location_assign" and _DECLARES.search(clean, max(0, m.start() - 12), m.start()):
                continue  # `const location = ...` is a local, not the frame's address
            if pattern.id == "xhr_sync" and "XMLHttpRequest" not in clean:
                continue
            if pattern.guard and guarded(line, pattern.guard):
                findings.append(Finding("guarded_fallback", INFO, line, f"{pattern.id} after {pattern.guard}"))
                continue
            if pattern.id == "confirm":
                if confirm_is_awaited(blank, m.start()):
                    findings.append(Finding("confirm", INFO, line, "compiled to the host's question"))
                else:
                    findings.append(
                        Finding("confirm", BREAKS, line, "not in an async function or event handler: answered No")
                    )
                continue
            if pattern.id == "event_source":
                expr, _ = _read_arg(clean, m.end())
                address = resolve_address(expr, clean, m.start())
                if address is None:
                    findings.append(Finding("event_source", INFO, line, "address not resolved: unchecked"))
                    continue
                verdict = check_fetch_address(address.split("#")[0], "GET", is_page=is_page)
                if verdict and verdict[0] != "fetch_dynamic":
                    pid, detail = verdict
                    findings.append(Finding(pid, PATTERN_BY_ID[pid].severity, line, f"EventSource: {detail}"))
                else:
                    findings.append(Finding("event_source", INFO, line, "read by the host"))
                continue
            findings.append(Finding(pattern.id, pattern.severity, line))

    if re.search(r"<form\b", clean) and "preventDefault" not in clean:
        m = re.search(r"<form\b", clean)
        findings.append(Finding("form_submit", CHANGES, _line_of(clean, m.start())))

    for m in _FETCH.finditer(clean):
        line = _line_of(clean, m.start())
        expr, end = _read_arg(clean, m.end())
        address = resolve_address(expr, clean, m.start())
        if address is None:
            findings.append(Finding("fetch_dynamic", INFO, line))
            continue
        verdict = check_fetch_address(address.split("#")[0], _method_of(clean, end), is_page=is_page)
        if verdict:
            pid, detail = verdict
            findings.append(Finding(pid, PATTERN_BY_ID[pid].severity, line, detail))

    findings.sort(key=lambda f: (f.line, SEVERITY_ORDER[f.severity], f.pattern))
    return findings


def worst(findings: list[Finding]) -> str | None:
    if not findings:
        return None
    return min((f.severity for f in findings), key=SEVERITY_ORDER.__getitem__)
