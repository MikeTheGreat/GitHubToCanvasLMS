# Rename Plan: `github-to-canvas` → `markdown-to-canvas`

**Status:** proposal only. No code has been changed.
**Revision:** rewritten 2026-08-01 after a fact-checking pass — every count, line
number, and environment claim below was re-verified against the working tree.
See §10 for what changed from the first draft and why.

---

## 1. Summary of what changes

| Kind | Old | New |
| --- | --- | --- |
| Distribution / PyPI-style name | `github-to-canvas` | `markdown-to-canvas` |
| Python package (import name) | `github_to_canvas` | `markdown_to_canvas` |
| Source directory | `src/github_to_canvas/` | `src/markdown_to_canvas/` |
| CLI executable | `github-to-canvas` | `markdown-to-canvas` |
| Click completion env var | `_GITHUB_TO_CANVAS_COMPLETE` | `_MARKDOWN_TO_CANVAS_COMPLETE` |
| README H1 | `# github-to-canvas` | `# markdown-to-canvas` *(unchanged in kind — stays the command)* |
| ARCHITECTURE / CLAUDE doc titles | `GitHubToCanvasLMS` | `MarkdownToCanvasLMS` |
| GitHub repo | `MikeTheGreat/GitHubToCanvasLMS` | `MikeTheGreat/MarkdownToCanvasLMS` |
| Local directory | `.../Tech/GitHubToCanvasLMS/GitHubToCanvasLMS` | `.../Tech/MarkdownToCanvasLMS` *(flattened one level)* |
| Version | `0.1.0` | `0.2.0` |

> **The `LMS` suffix is kept on the repo, the local directory, and the doc
> titles; it is *not* used in the CLI or distribution name.** This is exactly
> the split that exists today (repo `GitHubToCanvasLMS` / command
> `github-to-canvas`), so it introduces no new inconsistency. See §4.3 for why.

### Three things that turned out to be non-problems

1. **No on-disk data format is named after the tool.** Verified by grepping
   `src/` for `__file__`, `importlib.resources`, `appdirs`/`platformdirs`,
   `Path.home()`, and XDG paths: there is no package data, no
   `__file__`-relative resource, and no app-named config directory. Every
   artifact the tool reads or writes in a *course repo* is named after
   **Canvas**, not after this tool:

   - `.canvas-manifest.toml` (the manifest)
   - `.canvasignore` (the ignore file)
   - `canvas.toml`, `course_settings/*.toml`, `module_order.toml`
   - `CANVAS_API_TOKEN` (the env var / `.env` key)

   The *only* tool-named artifact anywhere outside a course repo is the shell
   completion file (§7.2). **No user's course repo needs to be migrated.**

2. **`src/` uses only relative imports.** Every module in
   `src/github_to_canvas/` imports its siblings as `from .config import ...`.
   Verified: 0 absolute self-imports in `src/`. Renaming the package directory
   requires **zero edits inside the source files' import statements**.

3. **A token-level find/replace is provably safe** — see §3. The first draft
   rated this the highest risk in the operation; it is not.

The rename is therefore: rename one directory, run one `sed` over 26 files,
make 7 edits by hand, and do the out-of-tree environment work.

---

## 2. Occurrence inventory (verified against `git ls-files`)

### `github_to_canvas` (snake_case, the import name) — 318 occurrences

Almost entirely `monkeypatch.setattr("github_to_canvas.X.Y", ...)` strings and
`from github_to_canvas.X import Y` lines in tests. Purely mechanical.

| Count | File |
| ---: | --- |
| 213 | `tests/test_sync.py` |
| 43 | `tests/test_due_dates.py` |
| 13 | `tests/test_imscc_convert.py` |
| 8 | `tests/test_conditionals.py` |
| 8 | `tests/test_check_all.py` |
| 7 | `tests/test_settings_sections.py` |
| 5 | `tests/test_imscc_temp_manifest.py` |
| 4 | `tests/test_imscc_import.py` |
| 3 | `pyproject.toml` |
| 2 | `tests/test_config.py` |
| 2 | `ARCHITECTURE.md` (L373, L539 — `src/` path references) |
| 1 each | `tests/test_quiz.py`, `test_publish.py`, `test_orphans.py`, `test_mv.py`, `test_manifest.py`, `test_link_rewrite.py`, `test_imscc_link_rewrite.py`, `test_ignore.py`, `test_convert.py`, `scripts/check_imscc_coverage.py` |

Plus the directory name `src/github_to_canvas/` itself.

### `github-to-canvas` (kebab-case, the command / dist name) — 62 occurrences

| Count | File | Notes |
| ---: | --- | --- |
| 41 | `README.md` | command examples + `Usage:` help blocks + H1 + TOC |
| 6 | `ARCHITECTURE.md` | command examples |
| 5 | `src/github_to_canvas/publish.py` | L793, 794, 796 (GH Actions scaffold) + L866, 875 (error messages) |
| 3 | `src/github_to_canvas/cli.py` | L60 error msg, L149 docstring, L157 `prog_name` |
| 2 | `pyproject.toml` | `name`, `[project.scripts]` |
| 2 | `install.sh` | |
| 1 | `uv.lock` | regenerated, not hand-edited |
| 1 | `tests/test_publish.py:462` | asserts scaffold text |
| 1 | `CLAUDE.md:35` | the `gg` alias note |

**All 62 change.** None is a protected reference (§3).

### `GitHubToCanvasLMS` (repo name) — 9 occurrences

| File | Line(s) | What it is |
| --- | --- | --- |
| `README.md` | 123, 135, 144, 145 | clone / `uvx --from` URLs + `cd` |
| `install.sh` | 1 | absolute local path (**doubled** — see §5 Step 3) |
| `src/github_to_canvas/publish.py` | 794 | URL inside the emitted GH Actions workflow |
| `ARCHITECTURE.md` | 1 | doc title |
| `CLAUDE.md` | 38 | doc title |

### `_GITHUB_TO_CANVAS_COMPLETE` — 1 occurrence

`src/github_to_canvas/cli.py:158`.

---

## 3. What must **NOT** change — and why `sed` is safe anyway

These are references to GitHub the *service*, not to this tool.

**The key finding:** every line in the repo containing the string `github`
(case-insensitive) falls into exactly one of two buckets, with nothing left
over:

- **21 protected lines** — listed below. **None of them contains any of the four
  rename tokens.**
- **6 tagline lines** — the "GitHub repo" → "Git repo" softening in §4.5.

Because no protected line contains a rename token, swapping the four *full
tokens* cannot touch a protected line. The danger the first draft worried about
only ever existed for a naive `s/github/markdown/`, which nobody would run.
This is verifiable after the fact by diffing the 21 lines below — a check that
hand-editing cannot offer, because it leaves no baseline.

**`src/github_to_canvas/publish.py`** (14 lines)

- L763 comment "GitHub Actions workflow scaffold"
- L806 `name: github-pages` — a GitHub Actions *environment* name
- L809 "Deploy to GitHub Pages"
- L815, 816 `_github_pages_url()` and its docstring
- L827, 828 the `github\.com[:/]...` remote-parsing comment and regex
- L832 `.github.io` URL construction
- L836, 837 `emit_workflow()` docstring and `.github/workflows/publish.yml` path
- L842, 843, 844, 851 "GitHub" prose and `https://<your-username>.github.io/<repo-name>/`

Within `WORKFLOW_YML`, only L793/794 (`Install` + `pip install`) and L796
(`run:`) change — and all three contain rename tokens, so `sed` handles them.

**`src/github_to_canvas/cli.py`** (1 line)

- L272 docstring: "Write a GitHub Actions workflow for publishing to GitHub Pages."

**`tests/test_publish.py`** (1 line)

- L458 `assert dest == tmp_path / ".github" / "workflows" / "publish.yml"`

**Docs** (5 lines)

- `README.md:1767` "In GitHub/VSCode *preview*, ..."
- `ARCHITECTURE.md:523` `https://squidfunk.github.io/mkdocs-material/`
- `ARCHITECTURE.md:524` "optionally deploys it to GitHub Pages"
- `ARCHITECTURE.md:537` `.github/workflows/publish.yml`
- `CLAUDE.md:56` `https://github.com/ucfopen/canvasapi`

Clone URLs in `README.md` change to the *new repo name* but keep the
`github.com` host — handled by the `GitHubToCanvasLMS` token swap.

---

## 4. Naming decisions — RESOLVED

**Why rename at all:** the current name describes the *transport* (GitHub)
rather than the *job*. Uploading Markdown to Canvas is why the tool exists;
IMSCC import and static-site publishing are useful secondary functions. The
name should say so.

1. **Import name: `markdown_to_canvas`.** ✅
2. **CLI + distribution name: `markdown-to-canvas`.** ✅ No short `md2canvas`
   alias — the `gg` shell alias already covers ergonomics, and
   `ofloveandhate/markdown2canvas` already exists on GitHub in this space.
   Neither `markdown-to-canvas` nor `github-to-canvas` exists on PyPI (both
   404), so there is no distribution-name conflict either way.

3. **Prose names — two forms, matching today's arrangement.** ✅
   - `README.md` H1 stays the **command form**: `# markdown-to-canvas`. That is
     what it is today (`# github-to-canvas`), and it is what a reader actually
     types. It also keeps the TOC anchor a plain token swap
     (`- [markdown-to-canvas](#markdown-to-canvas)`), with no manual anchor
     surgery.
   - `ARCHITECTURE.md` / `CLAUDE.md` titles become **`MarkdownToCanvasLMS`**,
     matching the repo and directory, as they do today.

   **The `LMS` suffix is retained on repo / directory / doc titles** because
   "Markdown + Canvas" is a crowded namespace split across three different
   products called Canvas. A GitHub search for repos named `markdown`+`canvas`
   returns, by stars: Obsidian Canvas tools (including
   `obsidian-convert-markdown-to-canvas`, literally this name), HTML5
   `<canvas>` renderers, and — only 2 of the top 10 — Canvas LMS tools. `LMS`
   is the single token that says "Instructure, not Obsidian".

4. **Backwards-compatible `github-to-canvas` command: no.** ✅ The old command
   stops existing; §7.1 removes the stale install so it can't be used by
   accident.

5. **Soften the taglines: yes.** ✅ Drop "GitHub" from the descriptions, since
   GitHub-specificity is what the rename is disclaiming. These are the **only 6
   lines** where a bare "GitHub" changes:

   | Location | Before | After |
   | --- | --- | --- |
   | `src/.../cli.py:99` group docstring | "...from a Markdown GitHub repo." | "...from a Markdown repo." |
   | `pyproject.toml:8` `description` | "Sync Markdown from a GitHub repo to Canvas LMS" | "Sync Markdown from a repo to Canvas LMS" |
   | `ARCHITECTURE.md:5` | "...stored in a GitHub repository." | "...stored in a Git repository." |
   | `ARCHITECTURE.md:11` diagram | "GitHub repo (Markdown + assets)" | "Git repo (Markdown + assets)" |
   | `CLAUDE.md:40` | "...stored in a GitHub repository." | "...stored in a Git repository." |
   | `CLAUDE.md:48` | "**Source of truth**: GitHub repo containing `.md` files" | "**Source of truth**: Git repo containing `.md` files" |

   `README.md:5` ("Write your course content as Markdown files in a Git
   repository") is already GitHub-free — no change.

   The softening is "GitHub → Git", not "GitHub → nothing" — the tool does still
   assume a git working tree (`find_repo_root`, `mv`'s git awareness,
   `_github_pages_url` reading `git remote`).

6. **Version: bump `0.1.0` → `0.2.0`.** ✅ Renaming the distribution is a
   breaking change for the one real consumer (§8), and the bump keeps
   `uv tool list` unambiguous if the old and new installs ever coexist.

7. **Local directory: flatten to a single level.** ✅

   ```text
   BEFORE  /home/mike/Dropbox/Personal/Tech/GitHubToCanvasLMS/GitHubToCanvasLMS/{src,tests,...}
   AFTER   /home/mike/Dropbox/Personal/Tech/MarkdownToCanvasLMS/{src,tests,...}
   ```

8. **Ordering: the move happens FIRST, not last.** ✅ See §5 Step 0 for why.

---

## 5. Execution plan

Ordered so the directory move — the one action `git` cannot revert — happens
first and is proven clean in isolation, before any renaming begins.

### Step 0 — move + flatten the local directory (by hand, before anything else)

The first draft put this last, to keep a path problem from being mistaken for a
rename bug. Moving it first separates the two failure modes just as cleanly,
in the opposite order, and avoids three problems the first draft accepted:
`install.sh` is never left knowingly wrong, no Claude Code session gets moved
out from under itself, and all the out-of-tree state work is grouped together
at a point where you are fresh.

**0a. Confirm the parent holds only the repo.**

```bash
ls -A /home/mike/Dropbox/Personal/Tech/GitHubToCanvasLMS
```

Expected output: just `GitHubToCanvasLMS`. **If anything else is listed, stop.**
On 2026-08-01 this directory briefly also held `Test_Import/`,
`it-cs142-imscc-unzipped/`, and a ~23 MB `.imscc` export; those were gone by
12:41. `Test_Import/` and `it-cs142-imscc-unzipped/` are covered by
`.gitignore`, but the `.imscc` file is **not**, so flattening on top of it would
drop a 23 MB untracked file into `git status`.

**0b. Quit Claude Code entirely.** Its transcript directory lives inside the
tree being moved.

**0c. Rename the repo on GitHub** to `MarkdownToCanvasLMS`, in the browser.
This is safe to do now: GitHub redirects the old URL, and `pyproject.toml` still
declares the old distribution name, so the IT-CS_115 workflow (§8) keeps
working until Step 6 is pushed.

**0d. Move and flatten.**

```bash
cd /home/mike/Dropbox/Personal/Tech
mv GitHubToCanvasLMS/GitHubToCanvasLMS MarkdownToCanvasLMS
rmdir GitHubToCanvasLMS        # fails loudly if not empty — that's the safety net
```

`rmdir` (not `rm -rf`) is deliberate: it refuses to delete a non-empty
directory, so nothing can be destroyed by accident.

**0e. Repoint the git remote.**

```bash
cd /home/mike/Dropbox/Personal/Tech/MarkdownToCanvasLMS
git remote set-url origin https://github.com/MikeTheGreat/MarkdownToCanvasLMS.git
git remote -v && git status    # verify remote, and that the tree is still clean
```

**0f. Repoint Claude Code's project state.**
`~/.claude/projects/<slug>` is not a real directory for this project — it is a
**symlink into the repo**. All 103 MB of transcripts *and* the `memory/`
directory physically live in `.claude-conversations/` inside the repo, which is
gitignored and moves with `mv`. Nothing needs copying; only the symlink is
recreated:

```bash
cd /home/mike/.claude/projects
ln -s /home/mike/Dropbox/Personal/Tech/MarkdownToCanvasLMS/.claude-conversations \
      -- -home-mike-Dropbox-Personal-Tech-MarkdownToCanvasLMS
rm -- -home-mike-Dropbox-Personal-Tech-GitHubToCanvasLMS-GitHubToCanvasLMS
rm -- -home-mike-Dropbox-Personal-Tech-GitHubToCanvasLMS   # pre-existing dangling link
readlink -e -home-mike-Dropbox-Personal-Tech-MarkdownToCanvasLMS   # must print a path
```

The `--` matters in **both** `ln` and `rm`: these filenames start with `-` and
would otherwise be parsed as options. (GNU coreutils 9.4 on this machine
supports `--` for both.) The second `rm` clears a symlink that points at the old
*parent* directory and is already dangling today — pre-existing debris from a
session run one level up.

`~/.claude.json` needs **no** action: it has `projects` entries keyed by both
old paths, but both are empty (`allowedTools: 0`, `hasTrustDialogAccepted:
false`, no MCP servers). Nothing there is worth migrating.

**0g. Drop the stale editable install and prove the move alone is clean.**

```bash
cd /home/mike/Dropbox/Personal/Tech/MarkdownToCanvasLMS
rm -rf .venv                   # holds _editable_impl_github_to_canvas.pth
uv sync && uv run pytest
```

Removing `.venv` up front is cheaper than diagnosing a stale
`github_to_canvas-0.1.0.dist-info` later. Restart Claude Code here if you want
it for the remaining steps.

**Dropbox caution:** this is a Dropbox-synced folder and the move includes the
~103 MB `.claude-conversations/` directory. Dropbox will re-index and re-upload.
Let it settle before assuming anything is wrong; ideally move when Dropbox is
otherwise idle.

**A green run here means the move is clean.** Everything after this point is the
rename, and any failure is a rename bug.

### Step 1 — rename the package directory

```bash
git mv src/github_to_canvas src/markdown_to_canvas
```

No file *contents* change here (relative imports, §1.2).

### Step 2 — swap all four tokens

`git grep -l` selects only tracked text files that actually contain a token —
26 files, no binaries, and `uv.lock` excluded because Step 5 regenerates it.

```bash
git grep -lE 'github_to_canvas|github-to-canvas|GitHubToCanvasLMS|GITHUB_TO_CANVAS' \
    -- . ':!uv.lock' \
  | xargs sed -i \
      -e 's/github_to_canvas/markdown_to_canvas/g' \
      -e 's/github-to-canvas/markdown-to-canvas/g' \
      -e 's/GitHubToCanvasLMS/MarkdownToCanvasLMS/g' \
      -e 's/GITHUB_TO_CANVAS/MARKDOWN_TO_CANVAS/g'
```

This covers, among others: `pyproject.toml`'s `name`, `[project.scripts]`,
`[tool.hatch.build.targets.wheel]` packages path and
`[tool.ruff.lint.per-file-ignores]` key; `cli.py`'s L60 error message, L149
docstring, L157 `prog_name` and L158 `complete_var`; `publish.py`'s L793/794/796
workflow lines and L866/875 error messages; `README.md`'s H1, TOC entry, 41
command examples and 4 repo URLs; and all 318 test occurrences.

### Step 3 — the 7 hand edits

**Six tagline lines** (§4.5) — "GitHub repo" → "Git repo" /
"GitHub repository" → "Git repository":
`src/markdown_to_canvas/cli.py:99`, `pyproject.toml:8`, `ARCHITECTURE.md:5`,
`ARCHITECTURE.md:11`, `CLAUDE.md:40`, `CLAUDE.md:48`.

**One path that `sed` gets wrong** — `install.sh:1`. The token swap turns the
*doubled* old path into a doubled new path, which is plausible but wrong. Final
content:

```bash
uv tool install --force --reinstall "markdown-to-canvas[publish] @ /home/mike/Dropbox/Personal/Tech/MarkdownToCanvasLMS"
markdown-to-canvas install-completion
```

**Plus the version bump** in `pyproject.toml:7`: `version = "0.2.0"`.

### Step 4 — checkpoint: run the suite

```bash
uv sync && uv run pytest
```

Expect green. Then verify the token swap left the protected lines alone:

```bash
git diff -U0 -- src/ tests/ | grep '^[-+].*[Gg]it[Hh]ub' | grep -viE 'markdown[_-]to[_-]canvas|MarkdownToCanvasLMS|MARKDOWN_TO_CANVAS'
```

This should print **only** the `cli.py:99` tagline pair. Any other line means a
protected reference was touched.

### Step 5 — regenerate the lock

```bash
uv lock
```

`uv.lock` picks up both the new name and `0.2.0`. Do not hand-edit it.

### Step 6 — final verification

```bash
grep -rniE 'github[_-]to[_-]canvas|GitHubToCanvasLMS' \
  --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.claude-conversations \
  --exclude=RENAME_PLAN.md .
```

Note `uv.lock` is deliberately **not** excluded — after Step 5 it is exactly
where you want confirmation the regeneration worked. Every surviving hit should
be in `.claude/settings.local.json` (untracked; §7.5). Anything else is a miss.

Then smoke-test the real CLI:

```bash
uv run markdown-to-canvas --help
uv run markdown-to-canvas update --help
```

The `Usage:` line should read `markdown-to-canvas`. Click derives that from
`sys.argv[0]`, so this proves the `[project.scripts]` entry point rewired
correctly. (The explicit `prog_name` at `cli.py:157` is separate — it only
affects the generated completion script.)

### Step 7 — TODO.md and the memory file

- Add a TODO.md entry: *the emitted `publish.yml` installs from
  `git+https://.../<repo>` with no ref, so every course repo's CI tracks this
  repo's `HEAD` — which is the only reason a rename can break a course site.
  Pinning to a release tag would fix it, but needs a release process that
  doesn't exist yet.*
- Update `.claude-conversations/memory/project_intent.md` (its `description:`
  names "the GitHubToCanvasLMS tool") and the matching line in
  `.claude-conversations/memory/MEMORY.md` ("Markdown-in-GitHub"). Unlike the
  `.jsonl` transcripts, these are **live context loaded into every session**, so
  §7.7's "leave it as a historical record" does not apply to them.

### Step 8 — push, then fix the one affected course repo

See §8. Nothing outside this repo breaks until the rename reaches `main`, so
push and fix IT-CS_115 in the same sitting.

### Step 9 — environment cleanup

See §7. Then delete this file.

---

## 6. Rollback

`git reset --hard` covers Steps 1–7 only. Because the move now happens first,
**the very first action is the one git cannot revert.** Each out-of-tree action
and its undo:

| Action | Undo |
| --- | --- |
| Step 0d directory move | `cd /home/mike/Dropbox/Personal/Tech && mkdir GitHubToCanvasLMS && mv MarkdownToCanvasLMS GitHubToCanvasLMS/GitHubToCanvasLMS` |
| Step 0c GitHub repo rename | Rename back in Settings; GitHub redirects both ways |
| Step 0e git remote | `git remote set-url origin https://github.com/MikeTheGreat/GitHubToCanvasLMS.git` |
| Step 0f symlink swap | Recreate the old-slug symlink; the transcripts themselves never moved out of the repo |
| Step 0g `rm -rf .venv` | `uv sync` rebuilds it |
| Steps 1–7 code + doc edits | `git reset --hard` (does **not** remove untracked files, including this one) |
| Step 8 push | `git revert` or force-push; the IT-CS_115 workflow commit reverts separately |
| Step 9 `uv tool uninstall` | `./install.sh` from the restored path |

The genuinely awkward one is Step 8 — once pushed, IT-CS_115's CI is coupled to
it. That is why Step 8 comes last and is verified immediately (§8).

---

## 7. Environment cleanup — outside the repo, easy to forget

The rename is not "done" until these happen.

1. **Uninstall the old uv tool.** Confirmed currently installed:

   ```
   $ uv tool list
   github-to-canvas v0.1.0
   - github-to-canvas
   ```

   Run `uv tool uninstall github-to-canvas` after `./install.sh` succeeds with
   the new name. Otherwise both commands exist and you may keep invoking a stale
   one.

2. **Delete the stale shell completion file.** Confirmed present:
   `~/.local/share/bash-completion/completions/github-to-canvas`
   (`install-completion` writes the new one but never removes the old.)
   Zsh/fish equivalents (`~/.zfunc/_github-to-canvas`,
   `~/.config/fish/completions/github-to-canvas.fish`) do not exist on this
   machine. This is the **only** tool-named file anywhere outside a repo (§1.1).

3. **Update your `gg` alias** in your shell rc (`~/.bashrc` / `~/.zshrc` — not in
   this repo) to expand to `markdown-to-canvas`.

4. **Stale editable install in `.venv/`** — handled proactively by the
   `rm -rf .venv` in Step 0g.

5. **`.claude/settings.local.json`** (untracked) has 25 `allow` entries, 9 of
   which contain the old absolute path, plus one `additionalDirectories` entry.
   Like `install.sh`, a plain token swap gives the **doubled** path, so the
   flatten must be applied by hand. Purely cosmetic — the alternative is a few
   extra permission prompts until they re-accumulate.

6. **Claude Code project history — handled in Step 0f.** For this project
   `~/.claude/projects/<slug>` is a symlink into the repo, and the transcripts
   plus `memory/` live in the gitignored `.claude-conversations/` *inside* the
   repo. `mv` carries them along; nothing is stranded. `~/.claude.json` needs no
   action (both entries are empty).

7. **`.claude-conversations/*.jsonl`** contains transcripts full of the old name
   and old paths. Historical record — leave as-is. The `memory/` files inside it
   are the exception (Step 7).

---

## 8. GitHub repo rename and the one affected course repo

Repo: `https://github.com/MikeTheGreat/GitHubToCanvasLMS` → owner
`MikeTheGreat`, repo `GitHubToCanvasLMS`. New value:
**`MikeTheGreat/MarkdownToCanvasLMS`** ✅ — matches the local directory.

Hardcoded in exactly 4 tracked places plus the git remote, all handled by the
`GitHubToCanvasLMS` token swap (Step 2) or Step 0e:

| Location | What it is |
| --- | --- |
| `src/markdown_to_canvas/publish.py:794` | `pip install ... @ git+URL` **inside `WORKFLOW_YML`** — written into *course* repos by `--emit-workflow` |
| `README.md:123` | `uv tool install git+https://github.com/...` |
| `README.md:135` | `uvx --from git+https://github.com/...` |
| `README.md:144-145` | `git clone https://github.com/...` + `cd GitHubToCanvasLMS` |
| `git remote origin` | not a file — Step 0e |

`publish.py:794` is the one that matters, because it escapes this repo.

### Exactly one course repo is affected

A sweep of `~/Dropbox/Work`, `~/Dropbox/Personal`, `~/Desktop`, and
`~/ObsidianViaGit` for `.github/workflows/*.yml` containing `github-to-canvas`
found exactly one:

**`/home/mike/Dropbox/Work/Courses/_IT_CS_115/IT-CS-115-mpanitz-repo/.github/workflows/publish.yml`**
→ GitHub `MikeTheGreat/IT-CS_115`. Committed (`57ecad8`), triggers on every push
to `main`, and also has `workflow_dispatch`.

**How it breaks — two corrections to the first draft:**

1. It fails at the **`pip install` step, not the `run:` step.** pip resolves
   `"github-to-canvas[publish] @ git+https://..."`, builds the metadata, finds
   the name is now `markdown-to-canvas`, and hard-errors on the mismatch. There
   is no partial success.

2. **The trigger is pushing the renamed `pyproject.toml`, not renaming the
   repo.** GitHub's redirect keeps the *URL* resolving indefinitely; it is the
   *metadata name* that breaks. So this fails the moment the rename reaches
   `main`, even if the repo were never renamed — and renaming the repo alone
   (Step 0c) breaks nothing.

**Fix, immediately after Step 8's push:**

```bash
markdown-to-canvas publish /home/mike/Dropbox/Work/Courses/_IT_CS_115/IT-CS-115-mpanitz-repo --emit-workflow
```

`emit_workflow()` overwrites unconditionally, so this is safe to re-run. Commit
and push, then **trigger it via `workflow_dispatch` and confirm green.** The
emitted workflow is the only artifact that escapes this repo, so it is the one
thing a green `pytest` cannot cover.

---

## 9. Risk assessment

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Find/replace clobbers legitimate GitHub Actions / Pages / `github.com` references | **None** — verified absent | No protected line contains a rename token (§3); Step 4 diffs them to confirm |
| Course-repo data migration needed | **None** — verified absent | All artifacts are Canvas-named; no package data or app-named config dir (§1.1) |
| Broken imports inside `src/` | **None** — verified absent | All relative imports (§1.2) |
| Claude Code history/memory lost on directory move | **None** — it lives inside the repo and moves with it | Step 0f recreates one symlink |
| IT-CS_115's workflow breaks | **Medium** — one course site stops deploying | §8: re-emit, push, and verify via `workflow_dispatch` in the same sitting |
| `sed` produces a plausible-but-wrong doubled path | Medium | Exactly 2 places: `install.sh` (Step 3) and `.claude/settings.local.json` (§7.5) |
| Missed `monkeypatch.setattr` target string → test silently patches nothing and passes | Low | Single unambiguous token; Step 4 runs the suite, Step 6 greps for leftovers |
| Old `github-to-canvas` binary stays on PATH | Low — confusing, not destructive | §7.1 |
| Directory move is not git-revertible | Low | §6 lists the manual undo; it happens first, in isolation, and is proven by Step 0g |
| Flattening drops stray parent-level files into the repo | Low *(parent verified empty)* | Step 0a re-checks with `ls -A`; `rmdir` refuses if non-empty |
| Dropbox re-sync churn (~103 MB `.claude-conversations/` moves) | Low | Step 0d caution |

**Overall:** lower risk than the raw count suggests. Of 390 occurrences, 389 are
mechanical swaps of unambiguous tokens; the judgement calls are 6 tagline lines,
1 path, and 1 version number, all enumerated in §5 Step 3.

---

## 10. What changed from the first draft

Recorded so the reasoning isn't lost:

- **`LMS` is kept** on repo / directory / doc titles (was: dropped everywhere).
  A GitHub search showed "Markdown + Canvas" is a crowded namespace spanning
  Obsidian Canvas, HTML5 `<canvas>`, and Canvas LMS; `LMS` is the only
  disambiguating token. §4.3
- **README H1 stays the command form**, not CamelCase. This preserves the split
  the docs already have and removes the TOC-anchor gotcha entirely. §4.3
- **The move happens first**, not last. §5 Step 0
- **`sed` is used repo-wide**, not just on `tests/`, because §3 proves it is
  safe — and it is *more* verifiable than hand-editing, since it leaves a
  baseline to diff. The old §8 rated this the highest risk in the operation;
  that rating was wrong.
- **§8's failure analysis corrected twice** — `pip install` not `run:`, and the
  push not the repo rename.
- **Version bump to 0.2.0** added. §4.6
- **§6 rollback section** added; the old one-line "Fallback" covered only the
  code edits.
- **`~/.claude.json` investigated** and found to need no action.
- **`memory/` files carved out** of "leave `.claude-conversations` alone" — they
  are live context, not history. §5 Step 7
- **TODO.md note** added about the unpinned emitted workflow. §5 Step 7
- **`uv.lock` no longer excluded** from the Step 6 verification grep.
- **`ln`/`rm` on the leading-dash symlink names now use `--`** in both commands;
  the first draft omitted it on `ln`, where it would have failed.

---

## 11. Decisions checklist

All resolved:

- [x] Names: `markdown-to-canvas` (CLI/dist) / `markdown_to_canvas` (package) /
      `MarkdownToCanvasLMS` (repo, directory, doc titles) /
      `# markdown-to-canvas` (README H1)
- [x] Short `md2canvas` alias? — **no**
- [x] Backwards-compatible `github-to-canvas` command? — **no**
- [x] Soften "GitHub repo" → "Git repo" in descriptions? — **yes** (§4.5)
- [x] Version bump? — **yes, 0.2.0** (§4.6)
- [x] Local directory — **flatten to `.../Tech/MarkdownToCanvasLMS`**, as Step 0
- [x] Edit method — **repo-wide `sed` of 4 tokens + 7 hand edits** (§3, §5)
- [x] Claude Code history across the move — **one symlink swap**, Step 0f

Still yours to do, outside this repo:

- [ ] Rename the repo on GitHub to `MarkdownToCanvasLMS` (Step 0c)
- [ ] Update the `gg` shell alias in your rc file (§7.3)
- [ ] Push, then re-emit + push + `workflow_dispatch`-verify IT-CS_115 (§8)
- [ ] Delete this file once the rename is done and committed
