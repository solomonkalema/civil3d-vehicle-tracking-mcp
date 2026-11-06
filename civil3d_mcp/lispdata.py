"""Parse values printed by AutoLISP (vl-prin1-to-string) into Python.

Bulk reads run as LISP inside Civil 3D and come back as one printed list.
That is much faster than reading objects one at a time over COM, and a single
call cannot be refused halfway through a loop.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+)')


def _tokens(text: str):
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"cannot parse LISP output near: {text[pos:pos + 40]!r}")
        yield m.group(1)
        pos = m.end()


def _atom(tok: str):
    if tok.startswith('"'):
        s = tok[1:-1]
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)
    up = tok.upper()
    if up == "NIL":
        return None
    if up == "T":
        return True
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok  # symbol, e.g. an entity name <Entity name: ...> fragment


def parse(text: str):
    """Parse one printed LISP value. Dotted pairs come back as 2-item lists."""
    stack: list[list] = [[]]
    for tok in _tokens(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            if len(done) == 3 and done[1] == ".":
                done = [done[0], done[2]]
            stack[-1].append(done)
        else:
            stack[-1].append(_atom(tok))
    top = stack[0]
    return top[0] if len(top) == 1 else top


def paren_balance(expr: str) -> tuple[int, str | None]:
    """Bracket depth of an AutoLISP expression, ignoring strings and ; comments.

    Returns (depth, problem). depth 0 and problem None means it is safe to send.
    An unbalanced expression sent to Civil 3D leaves the command line at a '((((_>'
    continuation prompt; Civil 3D then refuses COM and the server stalls (20 Oct 2026,
    20 Oct 2026). So it is checked here, before anything is sent.
    """
    depth = 0
    i, n = 0, len(expr)
    in_str = False
    while i < n:
        ch = expr[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == ";":
            j = expr.find("\n", i)
            i = n if j < 0 else j
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return depth, f"a ')' at character {i} closes more than was opened"
        i += 1
    if in_str:
        return depth, "a string is not closed (odd number of double quotes)"
    if depth > 0:
        return depth, f"{depth} '(' not closed"
    return 0, None
