"""Tests for the `publish` subcommand (MkDocs static-site generation).

Most assertions target the pure-Python staging/nav/render helpers, which need
no MkDocs install.  One test exercises ``run_publish`` end-to-end with the
``mkdocs`` subprocess mocked out.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

from github_to_canvas import publish

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Path mapping + site name
# ---------------------------------------------------------------------------

def test_staged_path_flattens_quiz():
    assert publish.staged_path("quizzes/a-quiz/a-quiz.md") == "quizzes/a-quiz.md"
    assert publish.staged_path("pages/syllabus.md") == "pages/syllabus.md"


def test_load_site_name_falls_back_to_repo_dir():
    assert publish.load_site_name(FIXTURES) == FIXTURES.name


def test_load_site_name_reads_course_settings(tmp_path):
    cs_dir = tmp_path / "course_settings"
    cs_dir.mkdir()
    (cs_dir / "course_settings.toml").write_text('title = "Intro to CS"\n')
    assert publish.load_site_name(tmp_path) == "Intro to CS"


# ---------------------------------------------------------------------------
# Content discovery
# ---------------------------------------------------------------------------

def test_discover_published_fixture():
    published = publish.discover_published(FIXTURES)
    assert "Assignments" in published
    assert "Discussions" in published
    assert "Modules" in published
    assert "Pages" in published
    assert "Quizzes" not in published

    assert ("Week 1 Problem Set", "assignments/week1.md") in published["Assignments"]
    assert ("Introduce Yourself", "discussions/week1-intro.md") in published["Discussions"]
    assert ("Week 1: Introduction", "modules/week-1.md") in published["Modules"]
    assert ("Syllabus", "pages/syllabus.md") in published["Pages"]


def test_discover_published_skips_unpublished(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "draft.md").write_text(
        "---\ntitle: Draft\npublished: false\n---\nDraft content\n"
    )
    (tmp_path / "pages" / "live.md").write_text(
        "---\ntitle: Live\npublished: true\n---\nLive content\n"
    )
    published = publish.discover_published(tmp_path)
    titles = [t for t, _ in published.get("Pages", [])]
    assert "Live" in titles
    assert "Draft" not in titles


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def test_build_nav_content_type_sections():
    nav = publish.build_nav(FIXTURES)

    assert nav[0] == {"Home": "index.md"}
    labels = [list(entry.keys())[0] for entry in nav[1:]]
    assert "Assignments" in labels
    assert "Discussions" in labels
    assert "Modules" in labels
    assert "Pages" in labels
    assert "Quizzes" not in labels

    # Assignments section contains the published assignment.
    assignments = next(entry for entry in nav if "Assignments" in entry)
    assert {"Week 1 Problem Set": "assignments/week1.md"} in assignments["Assignments"]

    # Modules section lists the module overview page.
    modules = next(entry for entry in nav if "Modules" in entry)
    assert {"Week 1: Introduction": "modules/week-1.md"} in modules["Modules"]


def test_build_nav_skips_unpublished(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "draft.md").write_text(
        "---\ntitle: Draft\npublished: false\n---\nContent\n"
    )
    nav = publish.build_nav(tmp_path)
    assert len(nav) == 1  # only Home


# ---------------------------------------------------------------------------
# Content staging
# ---------------------------------------------------------------------------

def test_stage_content_strips_frontmatter_and_keeps_existing_h1():
    md = publish.stage_content_markdown(FIXTURES / "pages" / "syllabus.md", FIXTURES)
    assert not md.startswith("---")
    assert "# Course Syllabus" in md
    # Snippet include is expanded inline (link text discarded).
    assert "office-hours.md" not in md
    # Cross-link to the assignment is preserved as a Markdown link.
    assert "../assignments/week1.md" in md


def test_stage_content_prepends_h1_when_missing():
    md = publish.stage_content_markdown(FIXTURES / "discussions" / "week1-intro.md", FIXTURES)
    assert md.startswith("# Introduce Yourself")


def test_stage_content_rewrites_quiz_links(tmp_path):
    (tmp_path / "snippets").mkdir()
    page = tmp_path / "page.md"
    page.write_text("---\ntitle: P\n---\n\nSee [the quiz](../quizzes/a-quiz/a-quiz.md).\n")
    md = publish.stage_content_markdown(page, tmp_path)
    assert "../quizzes/a-quiz.md" in md
    assert "a-quiz/a-quiz.md" not in md


# ---------------------------------------------------------------------------
# Pandoc syntax normalisation
# ---------------------------------------------------------------------------

def test_strip_pandoc_attrs_heading():
    text = '## What is this? {#what-is-this heading="Overview of course"}\n'
    result = publish._strip_pandoc_syntax(text)
    assert result == "## What is this? \n"


def test_strip_pandoc_attrs_image():
    text = '![alt](img.png){#239821515 role="presentation" width="776" height="284"}\n'
    result = publish._strip_pandoc_syntax(text)
    assert result == "![alt](img.png)\n"


def test_strip_pandoc_attrs_link_target():
    text = '[Click here](https://example.com){.external target="_blank"}\n'
    result = publish._strip_pandoc_syntax(text)
    assert result == "[Click here](https://example.com)\n"


def test_strip_pandoc_attrs_span():
    text = '[bold text]{style="background-color: #fff500;"}\n'
    result = publish._strip_pandoc_syntax(text)
    assert result == "bold text\n"


def test_strip_pandoc_span_with_nested_link():
    text = (
        '[Please [watch the video here]'
        '(https://example.com/video))]{style="font-size: 18pt;"}\n'
    )
    result = publish._strip_pandoc_syntax(text)
    assert result == "Please [watch the video here](https://example.com/video))\n"


def test_strip_pandoc_attrs_preserves_code_fences():
    text = '```\n{#id .class key="val"}\n```\n'
    result = publish._strip_pandoc_syntax(text)
    assert result == text


def test_strip_pandoc_attrs_ignores_plain_braces():
    text = "x = {1, 2, 3}\n"
    result = publish._strip_pandoc_syntax(text)
    assert result == text


def test_strip_pandoc_attrs_raw_html_block():
    text = "before\n\n```{=html}\n<!-- comment -->\n```\n\nafter\n"
    result = publish._strip_pandoc_syntax(text)
    assert "```" not in result
    assert "<!-- comment -->" in result
    assert "before" in result
    assert "after" in result


def test_strip_pandoc_escapes_apostrophe():
    result = publish._strip_pandoc_syntax("you\\'ll need this\n")
    assert result == "you'll need this\n"


def test_strip_pandoc_escapes_double_quote():
    result = publish._strip_pandoc_syntax('mentions of \\"hybrid format\\"\n')
    assert result == 'mentions of "hybrid format"\n'


def test_strip_pandoc_trailing_backslash():
    result = publish._strip_pandoc_syntax("end of line\\\nmore text\n")
    assert result == "end of line  \nmore text\n"


def test_strip_pandoc_trailing_backslash_with_spaces():
    result = publish._strip_pandoc_syntax("end of line\\  \nmore text\n")
    assert result == "end of line  \nmore text\n"


def test_strip_pandoc_escapes_preserved_in_code_fence():
    text = "```\nyou\\'ll see \\\" here\\\n```\n"
    result = publish._strip_pandoc_syntax(text)
    assert result == text


# ---------------------------------------------------------------------------
# Quiz study guide
# ---------------------------------------------------------------------------

def test_render_quiz_study_guide():
    guide = publish.render_quiz_study_guide(FIXTURES / "quizzes" / "a-quiz")
    assert guide.startswith("# A Quiz")
    assert "Answer each question carefully." in guide
    assert "## Question 1: What is 2+2?" in guide
    # MCQ choices listed; the correct one (index 2 → "4") is marked.
    assert "- 4 **(correct)**" in guide
    assert "- 3\n" in guide
    # Essay question has a prompt but no answer choices.
    assert "## Question 2: Explain something" in guide


# ---------------------------------------------------------------------------
# Module overview page
# ---------------------------------------------------------------------------

def test_render_module_overview():
    md = publish.render_module_overview(FIXTURES / "modules" / "week-1.md", FIXTURES)
    assert md.startswith("# Week 1: Introduction")
    assert "## Readings" in md
    assert "## Work" in md
    # Links step up out of modules/ to the sibling content dirs.
    assert "[Syllabus](../pages/syllabus.md)" in md
    assert "[Week 1 Assignment](../assignments/week1.md)" in md


# ---------------------------------------------------------------------------
# mkdocs.yml
# ---------------------------------------------------------------------------

def test_render_mkdocs_yml_roundtrips():
    nav = publish.build_nav(FIXTURES)
    text = publish.render_mkdocs_yml("My Course", nav)
    parsed = yaml.safe_load(text)
    assert parsed["site_name"] == "My Course"
    assert parsed["theme"]["name"] == "material"
    assert parsed["theme"]["custom_dir"] == "overrides"
    assert "navigation.indexes" in parsed["theme"]["features"]
    assert parsed["extra_css"] == ["stylesheets/extra.css"]
    assert parsed["nav"][0] == {"Home": "index.md"}


# ---------------------------------------------------------------------------
# Full staging tree
# ---------------------------------------------------------------------------

def test_stage_writes_full_tree(tmp_path):
    info = publish.stage(FIXTURES, tmp_path)

    assert (tmp_path / "mkdocs.yml").exists()
    assert (tmp_path / "overrides" / "main.html").exists()
    assert (tmp_path / "docs" / "index.md").exists()
    assert (tmp_path / "docs" / "stylesheets" / "extra.css").exists()
    # Assets copied wholesale.
    assert (tmp_path / "docs" / "assets" / "images" / "fig.png").exists()
    # All published content staged (including quiz, which is published).
    assert (tmp_path / "docs" / "modules" / "week-1.md").exists()
    assert (tmp_path / "docs" / "pages" / "syllabus.md").exists()
    assert (tmp_path / "docs" / "assignments" / "week1.md").exists()
    assert (tmp_path / "docs" / "discussions" / "week1-intro.md").exists()
    assert not (tmp_path / "docs" / "quizzes").exists()

    assert info["site_name"] == FIXTURES.name
    assert info["module_count"] == 1
    assert set(info["staged_files"]) == {
        "assignments/week1.md",
        "discussions/week1-intro.md",
        "modules/week-1.md",
        "pages/syllabus.md",
    }


def test_stage_excludes_unpublished_content(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pages").mkdir(parents=True)
    (repo / "pages" / "draft.md").write_text(
        "---\ntitle: Draft\npublished: false\n---\nDraft\n"
    )
    (repo / "pages" / "live.md").write_text(
        "---\ntitle: Live\npublished: true\n---\nLive\n"
    )
    staging = tmp_path / "staging"
    info = publish.stage(repo, staging)
    assert "pages/live.md" in info["staged_files"]
    assert "pages/draft.md" not in info["staged_files"]
    assert not (staging / "docs" / "pages" / "draft.md").exists()


# ---------------------------------------------------------------------------
# emit_workflow
# ---------------------------------------------------------------------------

def test_emit_workflow(tmp_path):
    dest = publish.emit_workflow(tmp_path)
    assert dest == tmp_path / ".github" / "workflows" / "publish.yml"
    text = dest.read_text()
    assert "actions/deploy-pages@v4" in text
    assert "actions/upload-pages-artifact@v3" in text
    assert "github-to-canvas publish ." in text
    assert "--deploy" not in text


# ---------------------------------------------------------------------------
# run_publish (mkdocs subprocess mocked)
# ---------------------------------------------------------------------------

def test_run_publish_build(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    monkeypatch.setattr(publish.tempfile, "mkdtemp", lambda prefix="": str(staging))
    calls = []
    monkeypatch.setattr(publish.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or None)

    out = tmp_path / "site"
    publish.run_publish(FIXTURES, out)

    assert (staging / "mkdocs.yml").exists()
    (cmd, kw), = calls
    assert cmd[:3] == [sys.executable, "-m", "mkdocs"]
    assert cmd[3] == "build"
    assert "--site-dir" in cmd
    assert str(out.resolve()) in cmd
    assert kw["cwd"] == str(FIXTURES.resolve())


def test_run_publish_missing_mkdocs_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(publish.tempfile, "mkdtemp", lambda prefix="": str(tmp_path / "s"))

    def _boom(*a, **k):
        raise FileNotFoundError("mkdocs")

    monkeypatch.setattr(publish.subprocess, "run", _boom)
    import pytest
    with pytest.raises(ValueError, match="mkdocs is not installed"):
        publish.run_publish(FIXTURES, tmp_path / "site")
