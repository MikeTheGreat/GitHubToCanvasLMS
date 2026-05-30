#!/usr/bin/env python3
"""
Verify that content from an IMSCC file survived conversion to a Markdown repo.

For each converted resource (pages, assignments, discussions) it picks a random
plain-text fragment from the IMSCC source and checks whether that text appears
anywhere in the output Markdown directory.  Quizzes and assets are skipped
because their content transforms too differently to compare directly.

Usage
-----
    python scripts/check_imscc_coverage.py <imscc-path> <output-dir> [options]

    <imscc-path>   Path to an .imscc zip file OR an already-extracted directory.
    <output-dir>   Path to the Markdown course repo produced by the import command.

Options
-------
    --min-words N       Minimum fragment length in words (default: 10).
                        Shorter fragments risk matching coincidentally.
    --seed N            Random seed so runs are reproducible (default: 42).
    --categories LIST   Comma-separated resource types to check.
                        Default: page,assignment,discussion,syllabus
                        Omit "syllabus" if the syllabus is just a file link.

Examples
--------
    # Check a .imscc file against an already-converted repo:
    python scripts/check_imscc_coverage.py \\
        course-export.imscc ./my-course-repo

    # Check an already-unzipped IMSCC dir, require longer fragments:
    python scripts/check_imscc_coverage.py \\
        ./it-cs142-imscc-unzipped ./Test_Import --min-words 15

    # Try a different random seed if you want a different sample:
    python scripts/check_imscc_coverage.py \\
        course-export.imscc ./my-course-repo --seed 7

Exit codes
----------
    0   All sampled fragments found.
    1   One or more fragments missing (printed to stdout).
    2   Argument / file error.

Notes
-----
Both sides are normalized before comparison: HTML entities are decoded, all
whitespace (including newlines from Pandoc line-reflowing) is collapsed to a
single space, and common typographic substitutions (smart quotes, en/em dashes)
are flattened.  This avoids false "missing" reports from legitimate
transformations while still catching genuinely absent content.
"""
from __future__ import annotations

import argparse
import html as html_mod
import random
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------
# Allow running from the repo root without installing the package.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from github_to_canvas.imscc_import import (  # noqa: E402
    TempEntry,
    _extract_html_body,
    open_imscc,
    parse_imsmanifest,
)

# ---------------------------------------------------------------------------
# Categories that carry readable prose we can meaningfully sample.
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES = {"page", "assignment", "discussion", "syllabus"}


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

class _TextCollector(HTMLParser):
    """Collect text nodes, ignoring tags."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html_str: str) -> str:
    """Extract plain text from an HTML fragment."""
    collector = _TextCollector()
    collector.feed(html_str)
    return collector.get_text()


def _normalize(text: str) -> str:
    """Decode entities, flatten typographic chars, collapse whitespace."""
    text = html_mod.unescape(text)
    # Flatten smart quotes and dashes so Pandoc's --smart doesn't cause misses.
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "--")
    text = text.replace(" ", " ")   # non-breaking space
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_imscc_text(entry: TempEntry, imscc_dir: Path) -> str:
    """Return normalized plain text for a resource."""
    if entry.category == "syllabus":
        # The syllabus HTML lives at a fixed location regardless of imscc_path.
        src = imscc_dir / "course_settings" / "syllabus.html"
        if not src.exists():
            return ""
        raw = src.read_text(encoding="utf-8", errors="replace")
        return _normalize(_strip_html(_extract_html_body(raw)))

    if entry.category in ("page", "assignment"):
        src = imscc_dir / entry.imscc_path
        if not src.exists():
            return ""
        raw = src.read_text(encoding="utf-8", errors="replace")
        return _normalize(_strip_html(_extract_html_body(raw)))

    if entry.category == "discussion":
        src = imscc_dir / entry.imscc_path
        if not src.exists():
            return ""
        try:
            tree = ET.parse(src)
            root = tree.getroot()
            ns = root.tag.split("}")[0][1:] if root.tag.startswith("{") else ""
            text_el = (
                root.find(f"{{{ns}}}text") if ns else root.find("text")
            )
            body_html = (text_el.text or "") if text_el is not None else ""
        except ET.ParseError:
            return ""
        return _normalize(_strip_html(body_html))

    return ""


# ---------------------------------------------------------------------------
# Fragment sampling
# ---------------------------------------------------------------------------

def _pick_fragment(normalized_text: str, min_words: int, rng: random.Random) -> str | None:
    """Return a random run of min_words consecutive words, or None if too short."""
    words = normalized_text.split()
    if len(words) < min_words:
        return None
    start = rng.randint(0, len(words) - min_words)
    return " ".join(words[start : start + min_words])


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _load_markdown_corpus(output_dir: Path) -> str:
    """Read and normalize all .md files into one searchable string."""
    parts: list[str] = []
    for md_file in sorted(output_dir.rglob("*.md")):
        raw = md_file.read_text(encoding="utf-8", errors="replace")
        parts.append(_normalize(raw))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Context helper
# ---------------------------------------------------------------------------

def _context_around(full_text: str, fragment: str, context_words: int = 20) -> str:
    """Show fragment with up to context_words words of surrounding text."""
    words = full_text.split()
    frag_words = fragment.split()
    n = len(frag_words)

    for i in range(len(words) - n + 1):
        if words[i : i + n] == frag_words:
            before = " ".join(words[max(0, i - context_words) : i])
            after = " ".join(words[i + n : i + n + context_words])
            return f"...{before} >>>>{fragment}<<<< {after}..."

    return f">>>>{fragment}<<<<"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("imscc", help="IMSCC .zip file or extracted directory")
    p.add_argument("output_dir", help="Converted Markdown course directory")
    p.add_argument(
        "--min-words", type=int, default=10, metavar="N",
        help="Minimum fragment length in words (default: 10)",
    )
    p.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    p.add_argument(
        "--categories",
        default=",".join(sorted(DEFAULT_CATEGORIES)),
        help=(
            "Comma-separated resource types to check "
            f"(default: {','.join(sorted(DEFAULT_CATEGORIES))})"
        ),
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    imscc_path = Path(args.imscc)
    output_dir = Path(args.output_dir)
    categories = set(c.strip() for c in args.categories.split(",") if c.strip())
    rng = random.Random(args.seed)

    if not imscc_path.exists():
        print(f"ERROR: IMSCC path not found: {imscc_path}", file=sys.stderr)
        sys.exit(2)
    if not output_dir.is_dir():
        print(f"ERROR: Output dir not found: {output_dir}", file=sys.stderr)
        sys.exit(2)

    imscc_dir, tmp = open_imscc(imscc_path)
    try:
        print("Parsing IMSCC manifest...")
        temp_manifest = parse_imsmanifest(imscc_dir)

        checkable = sorted(
            (e for e in temp_manifest.values() if e.category in categories),
            key=lambda e: (e.category, e.title.lower()),
        )
        skipped_categories = {
            e.category for e in temp_manifest.values() if e.category not in categories
        }
        if skipped_categories:
            print(f"Skipping categories: {', '.join(sorted(skipped_categories))}")

        print(f"Building Markdown corpus from: {output_dir}")
        corpus = _load_markdown_corpus(output_dir)

        print(f"\nChecking {len(checkable)} resources...\n")

        results_found: list[TempEntry] = []
        results_missing: list[tuple[TempEntry, str, str]] = []
        results_short: list[TempEntry] = []

        for entry in checkable:
            text = _get_imscc_text(entry, imscc_dir)
            fragment = _pick_fragment(text, args.min_words, rng)

            if fragment is None:
                results_short.append(entry)
                print(f"  [SKIP] {entry.category}: {entry.title!r}  (< {args.min_words} words)")
                continue

            if fragment in corpus:
                results_found.append(entry)
                print(f"  [ OK ] {entry.category}: {entry.title!r}")
            else:
                results_missing.append((entry, fragment, text))
                print(f"  [MISS] {entry.category}: {entry.title!r}")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        total = len(checkable)
        print(f"\n{'='*60}")
        print(f"Results: {len(results_found)} OK  |  "
              f"{len(results_missing)} MISSING  |  "
              f"{len(results_short)} skipped (too short)  |  "
              f"{total} total checked")
        print(f"{'='*60}")

        if results_missing:
            print(f"\n{len(results_missing)} MISSING fragment(s):\n")
            for entry, fragment, full_text in results_missing:
                print(f"  Type:    {entry.category}")
                print(f"  Title:   {entry.title}")
                print(f"  Source:  {entry.imscc_path}")
                print(f"  Output:  {entry.local_path or '(none)'}")
                print(f"  Context: {_context_around(full_text, fragment)}")
                print()
            sys.exit(1)

    finally:
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    main()
