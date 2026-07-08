"""Course-flag conditional regions: #if / #elif / #else / #endif in Markdown.

Directives are HTML comments alone on their own line::

    <!-- #if in_person_class -->
    Bring your laptop to Room 302.
    <!-- #elif hybrid -->
    Attend in person or on Zoom.
    <!-- #else -->
    Join the Zoom link posted in the module.
    <!-- #endif -->

Conditions are a single flag name, optionally preceded by the word ``not``
(no ``and``/``or``/parentheses in v1). Flags come from the ``[course_flags]``
table in course_settings.toml. A flag that is not defined there is a hard
error in both forms — there is no "undefined means false".

Only the four keywords (plus five reserved misspellings that error with a
"did you mean" hint) are ever interpreted; every other HTML comment —
``<!-- #region -->``, ``<!-- published:false -->``, ordinary comments —
passes through completely untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .convert import split_fenced_segments, warn

# A line that is nothing but an HTML comment (surrounding whitespace allowed).
_COMMENT_LINE_RE = re.compile(r"^\s*<!--(.*)-->\s*$")

_FLAG_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

_KEYWORDS = frozenset({"if", "elif", "else", "endif"})

# Reserved misspellings → the directive the user probably meant.
_MISSPELLINGS = {
    "ifdef": "#if",
    "ifndef": "#if not",
    "elseif": "#elif",
    "elsif": "#elif",
    "fi": "#endif",
}


def _parse_directive_line(line: str) -> tuple[str, str, str] | None:
    """Classify a line: None (not a directive), ("misspelling", word, hint),
    or ("directive", keyword, argument_text)."""
    m = _COMMENT_LINE_RE.match(line)
    if m is None:
        return None
    content = m.group(1).strip()
    if not content.startswith("#"):
        return None
    parts = content.split(None, 1)
    word = parts[0][1:]  # keyword after the '#'
    rest = parts[1].strip() if len(parts) > 1 else ""
    if word in _MISSPELLINGS:
        return ("misspelling", word, _MISSPELLINGS[word])
    if word in _KEYWORDS:
        return ("directive", word, rest)
    return None  # e.g. <!-- #region -->, <!-- #endregion --> — not ours


def _parse_condition(rest: str) -> tuple[bool, str] | str:
    """Parse an #if/#elif argument. Returns (negate, flag_name), or the
    string "missing" / "malformed" on error."""
    tokens = rest.split()
    if not tokens:
        return "missing"
    if tokens[0] == "not":
        if len(tokens) != 2:
            return "malformed"
        negate, name = True, tokens[1]
    elif len(tokens) == 1:
        negate, name = False, tokens[0]
    else:
        return "malformed"
    if not _FLAG_NAME_RE.match(name):
        return "malformed"
    return (negate, name)


def parse_condition(text: str) -> tuple[bool, str] | str:
    """Public entry point to ``_parse_condition``, for callers outside this
    module that need to evaluate a single ``flag_name`` / ``not flag_name``
    string against the same grammar as ``#if`` (e.g. due_dates ``only_if``)."""
    return _parse_condition(text)


@dataclass
class _Frame:
    """One open #if … #endif region on the nesting stack."""

    parent_active: bool  # were we emitting lines when this #if opened?
    active: bool         # is the current arm's content emitted?
    any_taken: bool      # has any arm in this chain been taken yet?
    seen_else: bool
    open_line: str       # the #if line, for unclosed-at-EOF errors


def apply_conditionals(
    text: str,
    flags: dict[str, bool],
    source_desc: str,
    errors: list[str] | None = None,
    *,
    quiet: bool = False,
) -> str | None:
    """Evaluate #if/#elif/#else/#endif directives. Returns the filtered
    text, or None if any directive error occurred (caller must skip the
    file). Fence-aware; non-directive comments untouched.

    Directive lines and lines in false branches are removed entirely (no
    blank line left behind). Fenced code blocks are never scanned for
    directives; each block is kept or dropped wholesale according to the
    conditional state in force when the fence opened.

    Errors are reported through the usual ``warn(msg, errors)`` convention
    unless ``quiet`` is set (for passive probes that must not double-report).
    """
    problems = 0

    def report(msg: str) -> None:
        nonlocal problems
        problems += 1
        if not quiet:
            warn(f"ERROR: {source_desc}: {msg}", errors)

    out: list[str] = []
    stack: list[_Frame] = []

    def _active() -> bool:
        return not stack or stack[-1].active

    def _evaluate(rest: str, kw: str, line: str) -> bool:
        cond = _parse_condition(rest)
        if cond == "missing":
            report(f"'#{kw}' requires a flag name: '{line.strip()}'")
            return False
        if cond == "malformed":
            report(
                f"invalid condition in '{line.strip()}' — expected a single "
                f"flag name, optionally preceded by 'not' (no and/or/parentheses)"
            )
            return False
        negate, name = cond
        if name not in flags:
            report(
                f"undefined course flag '{name}' in '{line.strip()}' — flags "
                f"must be defined under [course_flags] in course_settings.toml"
            )
            return False
        return (not flags[name]) if negate else flags[name]

    for is_fenced, seg in split_fenced_segments(text):
        if is_fenced:
            # Directives inside fenced code blocks are literal example text;
            # the block as a whole follows the enclosing conditional state.
            if _active():
                out.append(seg)
            continue
        for line in seg.splitlines(keepends=True):
            parsed = _parse_directive_line(line)
            if parsed is None:
                if _active():
                    out.append(line)
                continue
            kind, word, rest = parsed
            if kind == "misspelling":
                report(
                    f"unknown directive '#{word}' in '{line.strip()}' — "
                    f"did you mean '{rest}'?"
                )
                continue
            if word == "if":
                cond = _evaluate(rest, "if", line)
                parent = _active()
                stack.append(
                    _Frame(
                        parent_active=parent,
                        active=parent and cond,
                        any_taken=cond,
                        seen_else=False,
                        open_line=line.strip(),
                    )
                )
            elif word == "elif":
                cond = _evaluate(rest, "elif", line)
                if not stack:
                    report(
                        f"'{line.strip()}' without a matching '<!-- #if … -->'"
                    )
                    continue
                frame = stack[-1]
                if frame.seen_else:
                    report(f"'{line.strip()}' after '<!-- #else -->'")
                    continue
                frame.active = frame.parent_active and not frame.any_taken and cond
                frame.any_taken = frame.any_taken or cond
            elif word == "else":
                if rest:
                    report(f"'#else' takes no argument: '{line.strip()}'")
                if not stack:
                    report(
                        f"'{line.strip()}' without a matching '<!-- #if … -->'"
                    )
                    continue
                frame = stack[-1]
                if frame.seen_else:
                    report(f"'{line.strip()}' after '<!-- #else -->'")
                    continue
                frame.active = frame.parent_active and not frame.any_taken
                frame.any_taken = True
                frame.seen_else = True
            elif word == "endif":
                if rest:
                    report(f"'#endif' takes no argument: '{line.strip()}'")
                if not stack:
                    report(
                        f"'{line.strip()}' without a matching '<!-- #if … -->'"
                    )
                    continue
                stack.pop()

    for frame in stack:
        report(
            f"unclosed '{frame.open_line}' — every '#if' needs a matching "
            f"'<!-- #endif -->'"
        )

    if problems:
        return None
    return "".join(out)


def resolve_published_if(
    frontmatter: dict,
    flags: dict[str, bool],
    source_desc: str,
    errors: list[str] | None = None,
    *,
    quiet: bool = False,
    forbid: bool = False,
    forbid_reason: str = "",
) -> bool | None:
    """Resolve the effective ``published`` boolean for a content file.

    Honors an optional ``published_if: flag_name`` (or ``published_if: not
    flag_name``) frontmatter key, which computes ``published`` from a course
    flag instead of a literal ``true``/``false``. Same condition syntax as
    ``#if`` (single flag name, optionally preceded by ``not``).

    Returns ``None`` if any problem occurred (caller must skip the file):
    ``published_if`` combined with an explicit ``published`` key (ambiguous),
    a malformed/missing condition, an undefined flag, or ``forbid=True`` (set
    by callers — announcements — for which Canvas has no way to unpublish
    once posted, so the whole feature is disallowed there).

    When ``published_if`` is absent, simply returns
    ``frontmatter.get("published", False)``.
    """
    if "published_if" not in frontmatter:
        return bool(frontmatter.get("published", False))

    def report(msg: str) -> None:
        if not quiet:
            warn(f"ERROR: {source_desc}: {msg}", errors)

    if forbid:
        report(
            f"'published_if' is not supported for {forbid_reason} "
            f"— use a literal 'published:' instead"
        )
        return None
    if "published" in frontmatter:
        report(
            "'published_if' cannot be combined with an explicit 'published' "
            "key — remove one"
        )
        return None
    value = frontmatter["published_if"]
    if not isinstance(value, str):
        report(
            f"'published_if' must be a flag name string, optionally preceded "
            f"by 'not' (e.g. 'published_if: my_flag'), got {value!r}"
        )
        return None
    cond = _parse_condition(value)
    if cond == "missing":
        report("'published_if' requires a flag name")
        return None
    if cond == "malformed":
        report(
            f"invalid 'published_if' value {value!r} — expected a single "
            f"flag name, optionally preceded by 'not' (no and/or/parentheses)"
        )
        return None
    negate, name = cond
    if name not in flags:
        report(
            f"undefined course flag '{name}' in 'published_if' — flags must "
            f"be defined under [course_flags] in course_settings.toml"
        )
        return None
    return (not flags[name]) if negate else flags[name]


def find_referenced_flags_in_frontmatter(frontmatter: dict) -> set[str]:
    """Lexical probe: the flag name referenced by ``published_if``, if any.

    Never errors (passive, like ``find_referenced_flags``) — a malformed
    value contributes nothing.
    """
    value = frontmatter.get("published_if")
    if not isinstance(value, str):
        return set()
    cond = _parse_condition(value)
    if isinstance(cond, tuple):
        return {cond[1]}
    return set()


def find_referenced_flags(text: str) -> set[str]:
    """Lexical probe: every flag name appearing in any directive, in any
    branch, taken or not. Fence-aware. Never errors (passive, like
    find_referenced_snippets). Malformed directives contribute nothing."""
    found: set[str] = set()
    for is_fenced, seg in split_fenced_segments(text):
        if is_fenced:
            continue
        for line in seg.splitlines():
            parsed = _parse_directive_line(line)
            if parsed is None or parsed[0] != "directive":
                continue
            _, word, rest = parsed
            if word not in ("if", "elif"):
                continue
            cond = _parse_condition(rest)
            if isinstance(cond, tuple):
                found.add(cond[1])
    return found
