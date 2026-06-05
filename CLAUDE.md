# GitHubToCanvasLMS

A tool for managing Canvas LMS course content through Markdown files stored in a GitHub repository. The workflow converts Markdown (and supporting assets) into HTML fragments and uploads them to a Canvas LMS instance via the Canvas API.

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — full system behaviour, file formats, CLI options, sync algorithm, import subcommand, configuration reference
- **[TESTING.md](TESTING.md)** — testing strategy, layers, fixtures, and what to assert on
- **[TODO.md](TODO.md)** — planned and possible future features

## Repository Structure

```text
course-repo/           ← the user's course content repo (separate from this tool)
├── pages/
├── assignments/
├── discussions/
├── quizzes/
│   └── my-quiz/
│       ├── my-quiz.md          # quiz-level file: frontmatter + ordered question list
│       └── questions/
├── modules/
├── snippets/
└── assets/

github-to-canvas/      ← this tool repo
├── CLAUDE.md
├── INTERNAL_DOCUMENTATION.md
├── TODO.md
├── pyproject.toml
├── src/
│   └── github_to_canvas/
│       ├── __init__.py
│       ├── cli.py           # entry point / argument parsing
│       ├── convert.py       # Markdown → HTML via Pandoc
│       ├── canvas_api.py    # Canvas upload logic via canvasapi library
│       ├── config.py        # config file handling (API token, base URL, course ID)
│       ├── quiz.py          # quiz/question file parsing
│       ├── sync.py          # main sync pipeline
│       ├── manifest.py      # .canvas-manifest.toml read/write
│       ├── link_rewrite.py  # post-Pandoc HTML link rewriting
│       └── imscc_import.py  # import subcommand: .imscc → local Markdown repo
└── tests/
    ├── fixtures/            ← mini course repo covering all test cases
    │   ├── pages/
    │   ├── assignments/
    │   ├── discussions/
    │   ├── quizzes/
    │   │   └── a-quiz/      ← quiz fixture with MCQ + essay questions
    │   ├── modules/
    │   ├── snippets/
    │   ├── assets/
    │   └── imscc/           ← synthetic IMSCC fixture for import tests
    │       └── g_quiz_1/    ← quiz fixture: assessment_meta.xml + QTI questions XML
    ├── test_convert.py      # unit tests: snippet preprocessing, Pandoc output
    ├── test_link_rewrite.py # unit tests: HTML link/img rewriting logic
    ├── test_manifest.py     # unit tests: manifest read/write/flush
    ├── test_quiz.py         # unit tests: quiz/question file parsing
    ├── test_sync.py         # integration tests: full pipeline with mocked canvasapi
    ├── test_imscc_import.py # integration tests: full import pipeline
    └── test_imscc_convert.py # unit tests: IMSCC XML parsing, link rewriting, slugification
```

## Tech Stack

- **Language**: Python 3.11+
- **Package manager**: `uv` (tool distribution via `uvx` / `uv tool install`)
- **Markdown conversion**: Pandoc (system install) via `pypandoc`
- **Canvas API client**: `canvasapi` (ucfopen/canvasapi) — Python wrapper around the Canvas REST API
- **CLI**: `click` or `argparse`
- **Config**: `tomllib` (stdlib) for `.toml` config files

## Key Design Decisions

- **Source of truth**: GitHub repo containing `.md` files and supporting assets (images, etc.)
- **Conversion**: Pandoc for Markdown → HTML conversion (produces clean HTML fragments suitable for Canvas)
- **Delivery**: Command-line tool, packaged as a `uv` tool for easy installation and running via `uvx`
- **Canvas content types**: Pages, Assignments, Discussion Forums, Quizzes (Classic), Modules

## Canvas API Notes

- Use the [`canvasapi`](https://github.com/ucfopen/canvasapi) Python library rather than raw HTTP calls
- `canvasapi` wraps the Canvas REST API with Python objects (`course.get_pages()`, `course.create_assignment()`, etc.)
- Canvas REST API docs (for reference): `<base_url>/doc/api/live`
- Content types and their `canvasapi` entry points:
  - Pages: `course.get_page()` / `course.create_page()`
  - Assignments: `course.get_assignment()` / `course.create_assignment()`
  - Discussion Topics: `course.get_discussion_topic()` / `course.create_discussion_topic()`
  - Modules: `course.get_module()` / `course.create_module()`
  - Module items: `module.get_module_items()` / `module.create_module_item()` / `module_item.delete()`
- HTML body field name varies by content type (`body`, `description`, `message`) — check `canvasapi` docs per object type
- Module item `type` values: `Page`, `Assignment`, `Discussion`, `Quiz`, `File`, `ExternalUrl`, `SubHeader`
- Sync content (pages/assignments/discussions/quizzes) before syncing modules — modules reference content by Canvas ID
- Quizzes: `course.get_quiz()` / `course.create_quiz()` / `quiz.edit()` / `quiz.get_questions()` / `quiz.create_question()` / `quiz_question.delete()`

## Pandoc Notes

- Invoke as a subprocess or via `pypandoc`
- Use `--from markdown+smart` for smart punctuation
- Output `--to html5` for clean fragments
- Avoid `--standalone` so Canvas gets only the body fragment, not a full HTML document
- Math support: `pandoc --mathml` if course content includes equations
  - CanvasLMS will remove any JS so we must use static content that is screen-reader accessible

## Testing Strategy

See **[TESTING.md](TESTING.md)** for the full testing strategy, layer breakdown, fixture descriptions, and what to assert on.
