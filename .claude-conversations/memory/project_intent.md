---
name: project-intent
description: Core purpose and design of the GitHubToCanvasLMS tool — Markdown-in-GitHub to Canvas LMS upload pipeline
metadata: 
  node_type: memory
  type: project
  originSessionId: b7b53c96-f32d-4582-90c5-9fc260a3562b
---

This project builds a tool to manage Canvas LMS course content via Markdown files in GitHub.

**Workflow:** GitHub repo (Markdown + assets) → git clone locally → conversion tool → Canvas API upload

**Why:** The user wants a plain-text, version-controlled source of truth for course content (pages, assignments, discussions), with automated publishing to Canvas LMS.

**How to apply:** All design decisions should serve this pipeline. The tool should be a Python `uv` tool (installable via `uvx`), use Pandoc for Markdown→HTML conversion (HTML fragments, not standalone documents), and the Canvas REST API for uploading. Config (API token, base URL, course ID) via `.toml` file and/or env vars.

Key tech choices recorded in CLAUDE.md: Python 3.11+, uv, pypandoc, click or argparse, tomllib.

**Canvas API approach:** Use the `canvasapi` library (ucfopen/canvasapi) as the primary interface. Fall back to raw REST (httpx/requests) only when canvasapi doesn't expose the needed functionality.
