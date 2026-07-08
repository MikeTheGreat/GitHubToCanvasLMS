"""`publish` subcommand: generate a public MkDocs static site from the course repo.

This module is intentionally split into small, pure-Python pieces (nav building,
content staging, study-guide rendering, mkdocs.yml generation) so they can be
unit-tested without MkDocs being installed.  Only ``run_publish`` shells out to
the ``mkdocs`` CLI.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import yaml

from .conditionals import apply_conditionals, resolve_published_if
from .convert import (
    apply_outside_fences,
    expand_frontmatter_snippets,
    preprocess_snippets,
)
from .quiz import parse_question_file, split_quiz_body
from .sync import (
    _content_default_published,
    check_course_flags_coverage,
    load_course_flags,
    parse_frontmatter,
    parse_module_body,
)


def _item_published(
    item: dict, repo: Path, flags: dict[str, bool] | None = None
) -> bool:
    """Resolve a module item's effective published state.

    `parse_module_body` leaves "published" as None for content items with no
    explicit per-item override, deferring to the referenced file's own
    frontmatter (see `_content_default_published`) so the static site's
    visibility matches what `update`/`publish` would actually leave on Canvas.
    """
    published = item.get("published", True)
    if published is None:
        return _content_default_published(repo, item["local_path"], repo / "snippets", flags)
    return bool(published)


# ---------------------------------------------------------------------------
# Static assets (CSS + theme override)
# ---------------------------------------------------------------------------

EXTRA_CSS = """\
:root {
  --md-primary-fg-color: #2D3B45;        /* Canvas sidebar charcoal */
  --md-primary-fg-color--light: #3d4f5c;
  --md-primary-fg-color--dark:  #1e2a31;
  --md-accent-fg-color: #E66000;         /* Canvas default institution orange */
}

/* Header bar */
[data-md-color-primary="custom"] .md-header {
  background-color: #2D3B45;
  color: #ffffff;
}

/* Nav sidebar title: hide site-name text, show "Materials:" instead */
.md-nav--primary > .md-nav__title {
  background-color: #2D3B45;
  color: transparent;
  font-size: 0;
  padding: 0.8rem;
}
.md-nav--primary > .md-nav__title::before {
  content: "Materials:";
  display: block;
  color: #ffffff;
  font-size: 0.9rem;
  font-weight: 700;
}
.md-nav--primary > .md-nav__title > * {
  font-size: 1rem;
  color: #ffffff;
}

/* Category labels in uppercase */
.md-nav__item--section > .md-nav__link {
  text-transform: uppercase;
}

/* Active leaf link: colored left-border indicator */
.md-nav__item:not(.md-nav__item--nested) > .md-nav__link--active {
  border-left: 3px solid #E66000;
  padding-left: calc(0.6rem - 3px);
  color: #E66000;
  font-weight: 600;
}

/* Remove highlight and extra space from category items */
.md-nav__item--active > .md-nav__link {
  border-left: 0;
  padding-left: 0;
  color: var(--md-default-fg-color);
  font-weight: 700;
}
"""

OVERRIDES_MAIN = """\
{% extends "base.html" %}
"""

THEME_FEATURES = [
    "navigation.sections",
    "navigation.indexes",
    "navigation.top",
    "toc.integrate",
    "search.highlight",
    "search.suggest",
]

QUIZ_NOT_PUBLISHED_PAGE = "quiz-not-published"

QUIZ_NOT_PUBLISHED_MD = """\
# Content Not Published

The item you just clicked on is not available on this site.

Quizzes, exams, and similar assessments are not published here
in order to protect the integrity of the assessment.

Please check your course's learning management system (e.g. Canvas)
for access to this content.
"""


# ---------------------------------------------------------------------------
# Course name
# ---------------------------------------------------------------------------

def load_site_name(repo: Path) -> str:
    """Course name from course_settings.toml, falling back to the repo dir name."""
    settings_path = repo / "course_settings" / "course_settings.toml"
    if settings_path.exists():
        with settings_path.open("rb") as fh:
            settings = tomllib.load(fh)
        for key in ("title", "name", "course_code"):
            value = settings.get(key)
            if value:
                return str(value)
    return repo.name


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------

# A quiz lives at quizzes/<slug>/<slug>.md in the repo but is flattened to a
# single study-guide file at quizzes/<slug>.md in the published site.
_QUIZ_PATH_RE = re.compile(r"quizzes/([^/)]+)/\1\.md")

# Matches any path starting with quizzes/ (for detecting quiz content items).
_QUIZ_DIR_PREFIX = "quizzes/"


def staged_path(local_path: str) -> str:
    """Map a repo-relative content path to its path within the staged docs/ dir."""
    return _QUIZ_PATH_RE.sub(r"quizzes/\1.md", local_path)


def _rewrite_quiz_links(text: str) -> str:
    """Rewrite Markdown links pointing at quizzes to the not-published placeholder.

    Preserves the ``../`` prefix so the relative path stays correct from the
    source file's location.
    """
    return re.sub(
        r"(\[[^\]]*\])\(((?:\.\./)*?)quizzes/[^)]*\.md\)",
        rf"\1(\2{QUIZ_NOT_PUBLISHED_PAGE}.md)",
        text,
    )


# ---------------------------------------------------------------------------
# Pandoc → MkDocs Markdown normalisation
# ---------------------------------------------------------------------------

# Matches Pandoc raw-HTML blocks:  ```{=html}\n<content>\n```  →  <content>
_RAW_HTML_BLOCK_RE = re.compile(
    r"^```\{=html\}\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL
)

# Matches Pandoc spans, including one level of nested [link](url):
#   [visible text]{#id .cls key="val"}  →  visible text
_PANDOC_SPAN_RE = re.compile(
    r"\[((?:[^\[\]]|\[[^\]]*\](?:\([^)]*\))?)*)\]"
    r"\{(?=[^}]*(?:#[\w-]|\.[\w-]|\w+=\"))[^}]+\}"
)

# Matches any remaining Pandoc attribute block:  {#id .cls key="val"}
_PANDOC_ATTR_RE = re.compile(
    r"\{(?=[^}]*(?:#[\w-]|\.[\w-]|\w+=\"))[^}]+\}"
)

# Backslash at end of line (Pandoc hard break) → two trailing spaces
_TRAILING_BACKSLASH_RE = re.compile(r"\\[ \t]*$", re.MULTILINE)


def _strip_pandoc_syntax(text: str) -> str:
    """Normalise Pandoc-flavoured Markdown into MkDocs-compatible Markdown.

    Handles raw-HTML blocks, attribute blocks, Pandoc spans, and backslash
    escapes while leaving real fenced code blocks untouched.
    """
    text = _RAW_HTML_BLOCK_RE.sub(r"\1", text)

    def _clean(segment: str) -> str:
        lines: list[str] = []
        for line in segment.splitlines(keepends=True):
            line = _PANDOC_SPAN_RE.sub(r"\1", line)
            line = _PANDOC_ATTR_RE.sub("", line)
            line = line.replace("\\'", "'").replace('\\"', '"')
            line = _TRAILING_BACKSLASH_RE.sub("  ", line)
            lines.append(line)
        return "".join(lines)

    return apply_outside_fences(text, _clean)


# ---------------------------------------------------------------------------
# Content discovery
# ---------------------------------------------------------------------------

CONTENT_DIRS = ["assignments", "discussions", "modules", "pages"]


def _published_title(
    md_file: Path, repo: Path, flags: dict[str, bool] | None = None
) -> str | None:
    """Return the title of ``md_file`` if it is published, else None.

    Reads the file once. As before, the ``published``/``published_if`` flag
    is read from the snippet-expanded frontmatter while the title comes from
    the raw frontmatter. A ``published_if`` problem (undefined flag,
    ambiguous with ``published``) is resolved quietly — it is loudly
    reported when the file syncs on its own via `update` — and treated as
    not published."""
    frontmatter, body = parse_frontmatter(md_file.read_text())
    title = frontmatter.get("title", md_file.stem)
    expanded, _ = expand_frontmatter_snippets(frontmatter, body, md_file, repo / "snippets")
    source_desc = md_file.relative_to(repo).as_posix() if md_file.is_relative_to(repo) else md_file.name
    if resolve_published_if(expanded, flags or {}, source_desc, quiet=True) is not True:
        return None
    return title


def discover_published(
    repo: Path, flags: dict[str, bool] | None = None
) -> dict[str, list[tuple[str, str]]]:
    """Discover all published content grouped by content type.

    Returns ``{type_label: [(title, repo_relative_path), ...]}`` for each
    content-type directory that contains at least one published item.
    Items within each type are sorted alphabetically by filename.

    This is a test/debug helper; product code uses ``_discover_type`` per type.
    """
    result: dict[str, list[tuple[str, str]]] = {}
    for content_type in CONTENT_DIRS:
        items = _discover_type(repo, content_type, flags)
        if items:
            label = content_type.replace("_", " ").title()
            result[label] = items
    return result


# ---------------------------------------------------------------------------
# Selective discovery (Syllabus / Assignments / Modules only)
# ---------------------------------------------------------------------------

def _discover_type(
    repo: Path, content_type: str, flags: dict[str, bool] | None = None
) -> list[tuple[str, str]]:
    """Discover published items of a single content type."""
    content_dir = repo / content_type
    if not content_dir.exists():
        return []
    items: list[tuple[str, str]] = []
    for md_file in sorted(content_dir.rglob("*.md")):
        title = _published_title(md_file, repo, flags)
        if title is None:
            continue
        rel = md_file.relative_to(repo).as_posix()
        items.append((title, rel))
    return items


def _find_syllabus(
    repo: Path, flags: dict[str, bool] | None = None
) -> tuple[str, str] | None:
    """Find the published syllabus page.

    Checks ``course_settings/syllabus.md`` first, then falls back to any
    published page whose filename contains "syllabus".
    """
    candidates: list[Path] = []

    cs_syllabus = repo / "course_settings" / "syllabus.md"
    if cs_syllabus.exists():
        candidates.append(cs_syllabus)

    pages_dir = repo / "pages"
    if pages_dir.exists():
        candidates.extend(
            f for f in sorted(pages_dir.rglob("*.md"))
            if "syllabus" in f.stem.lower()
        )

    for md_file in candidates:
        title = _published_title(md_file, repo, flags)
        if title is None:
            continue
        rel = md_file.relative_to(repo).as_posix()
        return (title, rel)
    return None


# ---------------------------------------------------------------------------
# Reachability traversal
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(([^)\s]+)")


def extract_local_refs(text: str, source_file: Path, repo: Path) -> set[str]:
    """Extract repo-relative paths of local files referenced from Markdown."""
    refs: set[str] = set()
    for m in _MD_LINK_RE.finditer(text):
        href = m.group(1)
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if href.startswith("<") and href.endswith(">"):
            href = href[1:-1]
        href = href.split("#")[0]
        if not href:
            continue
        resolved = (source_file.parent / href).resolve()
        try:
            rel = resolved.relative_to(repo.resolve()).as_posix()
        except ValueError:
            continue
        if (repo / rel).exists():
            refs.add(rel)
    return refs


def collect_reachable(
    repo: Path, seed_paths: set[str], flags: dict[str, bool] | None = None
) -> set[str]:
    """BFS from seed paths to find all locally-referenced content.

    Excludes anything under ``quizzes/`` or ``snippets/`` (snippets are
    inlined during staging, not linked). When ``flags`` is given, course-flag
    conditionals are applied before extracting refs, so content referenced
    only from a false branch is not pulled into the public site. Directive
    errors are silent here (quiet probe) — the same file errors loudly when
    staged.
    """
    visited: set[str] = set()
    queue = list(seed_paths)

    while queue:
        repo_rel = queue.pop(0)
        if repo_rel in visited:
            continue
        if repo_rel.startswith(("quizzes/", "snippets/")):
            continue
        visited.add(repo_rel)

        src = repo / repo_rel
        if not src.exists() or src.suffix != ".md":
            continue

        _, body = parse_frontmatter(src.read_text())
        if flags is not None:
            filtered = apply_conditionals(body, flags, repo_rel, quiet=True)
            if filtered is not None:
                body = filtered

        if repo_rel.startswith("modules/"):
            for item in parse_module_body(body, src, repo):
                if not _item_published(item, repo, flags):
                    continue
                if item["type"] == "content" and item["local_path"] not in visited:
                    queue.append(item["local_path"])
        else:
            for ref in extract_local_refs(body, src, repo):
                if ref not in visited:
                    queue.append(ref)

    return visited


# ---------------------------------------------------------------------------
# Navigation building
# ---------------------------------------------------------------------------

def discover_modules(repo: Path) -> list[Path]:
    """Return module .md files in alphabetical order (matching the sync order)."""
    modules_dir = repo / "modules"
    if not modules_dir.exists():
        return []
    return sorted(modules_dir.glob("*.md"))


def build_nav(repo: Path, flags: dict[str, bool] | None = None) -> list[Any]:
    """Build the MkDocs nav tree: Syllabus, Assignments, Modules.

    Only these three categories appear in the navigation.  Other content
    (pages, discussions) is published only when reachable from a nav item.
    Quizzes are explicitly excluded.
    """
    nav: list[Any] = [{"Home": "index.md"}]

    syllabus = _find_syllabus(repo, flags)
    if syllabus:
        title, path = syllabus
        nav.append({"Syllabus": [{title: staged_path(path)}]})

    assignments = _discover_type(repo, "assignments", flags)
    if assignments:
        children = [{t: staged_path(p)} for t, p in assignments]
        nav.append({"Assignments": children})

    modules = _discover_type(repo, "modules", flags)
    if modules:
        children = [{t: staged_path(p)} for t, p in modules]
        nav.append({"Modules": children})

    return nav


# ---------------------------------------------------------------------------
# Content staging
# ---------------------------------------------------------------------------

_LEADING_H1_RE = re.compile(r"^\s*#\s+", re.MULTILINE)


def _has_leading_h1(body: str) -> bool:
    for line in body.splitlines():
        if not line.strip():
            continue
        return line.lstrip().startswith("# ")
    return False


def stage_content_markdown(
    md_path: Path,
    repo: Path,
    flags: dict[str, bool] | None = None,
    errors: list[str] | None = None,
) -> str | None:
    """Return the published Markdown for a page/assignment/discussion file.

    Strips the tool-specific frontmatter, evaluates course-flag conditionals
    (when ``flags`` is given), expands snippet includes, rewrites quiz links
    to the flattened study-guide path, and ensures the document has an H1
    heading (used as the page title). Returns None on a conditional-directive
    error (reported via ``errors``); the caller must skip the file.
    """
    frontmatter, body = parse_frontmatter(md_path.read_text())
    frontmatter, body = expand_frontmatter_snippets(frontmatter, body, md_path, repo / "snippets")
    if flags is not None:
        source_desc = md_path.relative_to(repo).as_posix() if md_path.is_relative_to(repo) else md_path.name
        body = apply_conditionals(body, flags, source_desc, errors)
        if body is None:
            return None
    body = preprocess_snippets(body, md_path, repo / "snippets", errors, flags=flags)
    body = apply_outside_fences(body, _rewrite_quiz_links)
    body = _strip_pandoc_syntax(body)
    title = frontmatter.get("title", md_path.stem)
    if not _has_leading_h1(body):
        body = f"# {title}\n\n{body.lstrip()}"
    return body.rstrip() + "\n"


def render_quiz_study_guide(
    quiz_folder: Path, flags: dict[str, bool] | None = None
) -> str:
    """Render a quiz folder as a single readable Markdown study guide.

    Shows the quiz description, then each question with its prompt and answer
    choices.  Correct choices are marked; manually-graded questions show none.
    """
    quiz_md = quiz_folder / f"{quiz_folder.name}.md"
    frontmatter, body = parse_frontmatter(quiz_md.read_text())
    title = frontmatter.get("title", quiz_folder.name)

    if flags is not None:
        filtered = apply_conditionals(body, flags, quiz_md.name, quiet=True)
        if filtered is not None:
            body = filtered

    # Description = quiz body minus the numbered question-link list.
    # Shared with the update pipeline so both agree on what counts as
    # a question.
    desc, question_files = split_quiz_body(body, quiz_md)

    out: list[str] = [f"# {title}", ""]
    if desc:
        out.append(desc)
        out.append("")

    for n, q_path in enumerate(question_files, start=1):
        if not q_path.exists():
            continue
        q = parse_question_file(q_path, flags=flags)
        out.append(f"## Question {n}: {q['title']}")
        out.append("")
        if q.get("question_text"):
            # question_text is HTML; Markdown passes raw HTML through unchanged.
            out.append(q["question_text"].strip())
            out.append("")
        answers = q.get("answers") or []
        if answers:
            for a in answers:
                mark = " **(correct)**" if a.get("weight", 0) > 0 else ""
                out.append(f"- {a['text']}{mark}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _render_module_li(title: str, href: str, indent: int) -> str:
    style = f' style="margin-left: {indent * 2}em"' if indent else ""
    return f'<li{style}><a href="{href}">{title}</a></li>'


def render_module_overview(
    module_md: Path,
    repo: Path,
    flags: dict[str, bool] | None = None,
    errors: list[str] | None = None,
) -> str | None:
    """Render a module .md file as a clickable overview/index page.

    Returns None on a conditional-directive error (reported via ``errors``);
    the caller must skip the file.
    """
    frontmatter, body = parse_frontmatter(module_md.read_text())
    title = frontmatter.get("title", module_md.stem)
    if flags is not None:
        source_desc = module_md.relative_to(repo).as_posix() if module_md.is_relative_to(repo) else module_md.name
        body = apply_conditionals(body, flags, source_desc, errors)
        if body is None:
            return None
    items = parse_module_body(body, module_md, repo)

    out: list[str] = [f"# {title}", ""]
    in_list = False
    for item in items:
        if not _item_published(item, repo, flags):
            continue
        indent = item.get("indent", 0)
        if item["type"] == "SubHeader":
            if indent == 0:
                if in_list:
                    out.append("</ul>")
                    out.append("")
                    in_list = False
                out.append(f"## {item['title']}")
                out.append("")
            else:
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                style = f' style="margin-left: {indent * 2}em"' if indent else ""
                out.append(f'<li{style}><strong>{item["title"]}</strong></li>')
        else:
            if not in_list:
                out.append("<ul>")
                in_list = True
            if item["type"] == "ExternalUrl":
                out.append(_render_module_li(item["title"], item["url"], indent))
            elif item["type"] == "content":
                if item["local_path"].startswith(_QUIZ_DIR_PREFIX):
                    target = f"../../{QUIZ_NOT_PUBLISHED_PAGE}/"
                else:
                    target = "../" + staged_path(item["local_path"])
                out.append(_render_module_li(item["title"], target, indent))
    if in_list:
        out.append("</ul>")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# mkdocs.yml
# ---------------------------------------------------------------------------

def mkdocs_config(site_name: str, nav: list[Any]) -> dict[str, Any]:
    """Build the mkdocs.yml configuration dict."""
    return {
        "site_name": site_name,
        "theme": {
            "name": "material",
            "custom_dir": "overrides",
            "palette": {"primary": "custom"},
            "features": list(THEME_FEATURES),
        },
        "extra_css": ["stylesheets/extra.css"],
        "nav": nav,
    }


def render_mkdocs_yml(site_name: str, nav: list[Any]) -> str:
    return yaml.safe_dump(
        mkdocs_config(site_name, nav), sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def _load_front_page(repo: Path) -> str | None:
    """Return the repo-relative path named as ``front_page`` in course_settings.toml."""
    settings_path = repo / "course_settings" / "course_settings.toml"
    if not settings_path.exists():
        return None
    with settings_path.open("rb") as fh:
        return tomllib.load(fh).get("front_page")


def _render_index(
    site_name: str,
    repo: Path,
    syllabus: tuple[str, str] | None,
    nav_sections: dict[str, list[tuple[str, str]]],
    flags: dict[str, bool] | None = None,
    errors: list[str] | None = None,
) -> str:
    front_page_path = _load_front_page(repo)
    front_page_body = ""
    if front_page_path:
        fp = repo / front_page_path
        if fp.exists() and fp.suffix == ".md":
            front_page_body = stage_content_markdown(fp, repo, flags, errors) or ""

    if front_page_body:
        return front_page_body

    out = [
        f"# {site_name}",
        "",
        f"Welcome to **{site_name}**. Use the navigation on the left to browse "
        "the course content.",
        "",
    ]
    if syllabus:
        title, path = syllabus
        out.append(f"- [{title}]({staged_path(path)})")
        out.append("")
    for label, items in nav_sections.items():
        out.append(f"## {label}")
        out.append("")
        for title, repo_path in items:
            out.append(f"- [{title}]({staged_path(repo_path)})")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Staging orchestration
# ---------------------------------------------------------------------------

def stage(repo: Path, staging_dir: Path) -> dict[str, Any]:
    """Write the full MkDocs staging tree (mkdocs.yml + docs/ + overrides/).

    Returns a small info dict (site_name, content counts, staged file paths)
    for logging and tests.

    Only Syllabus, Assignments, and Modules appear in the nav.  Other content
    (pages, discussions, assets) is staged when reachable from a nav item.
    Quizzes are explicitly excluded.
    """
    docs = staging_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "stylesheets").mkdir(parents=True, exist_ok=True)
    (staging_dir / "overrides").mkdir(parents=True, exist_ok=True)

    site_name = load_site_name(repo)
    flags = load_course_flags(repo)
    errors: list[str] = []

    syllabus = _find_syllabus(repo, flags)
    assignments = _discover_type(repo, "assignments", flags)
    modules = _discover_type(repo, "modules", flags)

    nav = build_nav(repo, flags)

    seed_paths: set[str] = set()
    if syllabus:
        seed_paths.add(syllabus[1])
    for _, path in assignments:
        seed_paths.add(path)
    for _, path in modules:
        seed_paths.add(path)

    reachable = collect_reachable(repo, seed_paths, flags)

    nav_sections: dict[str, list[tuple[str, str]]] = {}
    if assignments:
        nav_sections["Assignments"] = assignments
    if modules:
        nav_sections["Modules"] = modules

    (docs / "stylesheets" / "extra.css").write_text(EXTRA_CSS)
    (staging_dir / "overrides" / "main.html").write_text(OVERRIDES_MAIN)
    (docs / f"{QUIZ_NOT_PUBLISHED_PAGE}.md").write_text(QUIZ_NOT_PUBLISHED_MD)
    (docs / "index.md").write_text(
        _render_index(site_name, repo, syllabus, nav_sections, flags, errors)
    )

    staged_files: list[str] = []
    for repo_rel in sorted(reachable):
        src = repo / repo_rel
        if not src.exists():
            print(f"  WARNING: content not found, skipping: {repo_rel}")
            continue

        dest_rel = staged_path(repo_rel)
        dest = docs / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if src.suffix != ".md":
            shutil.copy2(src, dest)
        else:
            if repo_rel.startswith("modules/"):
                staged = render_module_overview(src, repo, flags, errors)
            else:
                staged = stage_content_markdown(src, repo, flags, errors)
            if staged is None:
                print(f"  Skipping (conditional-directive errors): {repo_rel}")
                continue
            dest.write_text(staged)

        staged_files.append(dest_rel)
        print(f"  Staging: {dest_rel}")

    (staging_dir / "mkdocs.yml").write_text(render_mkdocs_yml(site_name, nav))

    check_course_flags_coverage(flags, repo)

    return {
        "site_name": site_name,
        "module_count": len(modules),
        "staged_files": staged_files,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# GitHub Actions workflow scaffold
# ---------------------------------------------------------------------------

WORKFLOW_YML = """\
name: Publish course site

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install Pandoc
        run: sudo apt-get update && sudo apt-get install -y pandoc
      - name: Install github-to-canvas
        run: pip install "github-to-canvas[publish] @ git+https://github.com/MikeTheGreat/GitHubToCanvasLMS"
      - name: Build site
        run: github-to-canvas publish .
      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
"""


def _github_pages_url(repo: Path) -> str | None:
    """Derive the GitHub Pages URL from the repo's git remote, or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        url = result.stdout.strip()
    except FileNotFoundError:
        return None
    if not url:
        return None
    # https://github.com/OWNER/REPO.git or git@github.com:OWNER/REPO.git
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None
    owner, repo_name = m.group(1), m.group(2)
    return f"https://{owner}.github.io/{repo_name}/"


def emit_workflow(repo: Path) -> Path:
    """Write a starter GitHub Actions workflow into the course repo."""
    dest = repo / ".github" / "workflows" / "publish.yml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(WORKFLOW_YML)
    print(f"Wrote workflow: {dest}")
    print()
    print("Before the workflow will run, you need to go to your GitHub")
    print("repo's Settings > Pages and set the source to \"GitHub Actions\".")
    pages_url = _github_pages_url(repo)
    if pages_url:
        print()
        print(f"Once deployed, your site will be at: {pages_url}")
    else:
        print()
        print("Once deployed, your site will be at:")
        print("  https://<your-username>.github.io/<repo-name>/")
    return dest


# ---------------------------------------------------------------------------
# mkdocs invocation
# ---------------------------------------------------------------------------

def _run_mkdocs(args: list[str], staging_dir: Path, cwd: Path) -> None:
    # We invoke mkdocs as `python -m mkdocs`, so a missing install surfaces as a
    # CalledProcessError ("No module named mkdocs"), not FileNotFoundError. Detect
    # the module up front so the friendly install hint is actually reachable.
    if importlib.util.find_spec("mkdocs") is None:
        raise ValueError(
            "mkdocs is not installed. Install the publish extra with "
            "`uv tool install github-to-canvas[publish]` (or `pip install mkdocs mkdocs-material`)."
        )
    cmd = [sys.executable, "-m", "mkdocs", *args, "-f", str(staging_dir / "mkdocs.yml")]
    print(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=str(cwd), check=True)
    except FileNotFoundError:
        raise ValueError(
            "mkdocs is not installed. Install the publish extra with "
            "`uv tool install github-to-canvas[publish]` (or `pip install mkdocs mkdocs-material`)."
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"mkdocs failed with exit code {exc.returncode}.")


def run_publish(
    course_dir: Path,
    output_dir: Path,
) -> None:
    """Top-level entry point for the `publish` subcommand."""
    repo = Path(course_dir).resolve()
    if not repo.is_dir():
        raise ValueError(f"Course directory not found: {course_dir}")

    staging_dir = Path(tempfile.mkdtemp(prefix="g2c-publish-"))
    print(f"Staging site in: {staging_dir}")
    try:
        info = stage(repo, staging_dir)
        print(f"Site: {info['site_name']}  ({info['module_count']} module(s), "
              f"{len(info['staged_files'])} content file(s))")

        errors = info.get("errors", [])
        if errors:
            print(f"\nThe following errors occurred while staging ({len(errors)} total):")
            for msg in errors:
                print(f"  {msg.strip()}")
            raise ValueError(
                f"{len(errors)} error(s) while staging content; fix them and re-run publish."
            )

        out = Path(output_dir).resolve()
        _run_mkdocs(["build", "--site-dir", str(out)], staging_dir, cwd=repo)
        print(f"Built static site: {out}")
    finally:
        # The staging tree is a throwaway scaffold for `mkdocs build`; the real
        # output lives in `out`. Remove it so we don't leak a temp dir per run.
        shutil.rmtree(staging_dir, ignore_errors=True)
