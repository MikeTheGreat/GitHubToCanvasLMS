"""Course-flag conditionals: unit tests for the #if/#elif/#else/#endif pass,
flag loading/validation, flag-aware staleness, plus integration tests for the
sync and publish pipelines (mocked canvasapi)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import tomli_w

from github_to_canvas import manifest as manifest_lib
from github_to_canvas import publish
from github_to_canvas.conditionals import (
    apply_conditionals,
    find_referenced_flags,
    find_referenced_flags_in_frontmatter,
    resolve_published_if,
)
from github_to_canvas.config import Config
from github_to_canvas.convert import preprocess_snippets
from github_to_canvas.mv import run_mv
from github_to_canvas.sync import (
    check_course_flags_coverage,
    load_course_flags,
    run_sync,
)

FLAGS = {"in_person_class": True, "hybrid": False}


def _apply(text: str, flags=None, errors=None) -> str | None:
    return apply_conditionals(text, FLAGS if flags is None else flags, "pages/x.md", errors)


# ---------------------------------------------------------------------------
# apply_conditionals — evaluation matrix
# ---------------------------------------------------------------------------


class TestEvaluation:
    def test_if_true_keeps_content(self) -> None:
        text = "before\n<!-- #if in_person_class -->\nkept\n<!-- #endif -->\nafter\n"
        assert _apply(text) == "before\nkept\nafter\n"

    def test_if_false_drops_content(self) -> None:
        text = "before\n<!-- #if hybrid -->\ndropped\n<!-- #endif -->\nafter\n"
        assert _apply(text) == "before\nafter\n"

    def test_if_not_true_drops_content(self) -> None:
        text = "<!-- #if not in_person_class -->\ndropped\n<!-- #endif -->\n"
        assert _apply(text) == ""

    def test_if_not_false_keeps_content(self) -> None:
        text = "<!-- #if not hybrid -->\nkept\n<!-- #endif -->\n"
        assert _apply(text) == "kept\n"

    def test_else_taken_when_if_false(self) -> None:
        text = (
            "<!-- #if hybrid -->\nA\n<!-- #else -->\nB\n<!-- #endif -->\n"
        )
        assert _apply(text) == "B\n"

    def test_else_skipped_when_if_true(self) -> None:
        text = (
            "<!-- #if in_person_class -->\nA\n<!-- #else -->\nB\n<!-- #endif -->\n"
        )
        assert _apply(text) == "A\n"

    def test_elif_chain_first_true_wins(self) -> None:
        flags = {"a": True, "b": True, "c": True}
        text = (
            "<!-- #if a -->\nA\n<!-- #elif b -->\nB\n<!-- #elif c -->\nC\n"
            "<!-- #else -->\nD\n<!-- #endif -->\n"
        )
        assert apply_conditionals(text, flags, "x") == "A\n"

    def test_elif_chain_middle_arm(self) -> None:
        flags = {"a": False, "b": True, "c": True}
        text = (
            "<!-- #if a -->\nA\n<!-- #elif b -->\nB\n<!-- #elif c -->\nC\n"
            "<!-- #else -->\nD\n<!-- #endif -->\n"
        )
        assert apply_conditionals(text, flags, "x") == "B\n"

    def test_elif_chain_falls_through_to_else(self) -> None:
        flags = {"a": False, "b": False, "c": False}
        text = (
            "<!-- #if a -->\nA\n<!-- #elif b -->\nB\n<!-- #elif c -->\nC\n"
            "<!-- #else -->\nD\n<!-- #endif -->\n"
        )
        assert apply_conditionals(text, flags, "x") == "D\n"

    def test_elif_with_not(self) -> None:
        flags = {"a": False, "b": True}
        text = "<!-- #if a -->\nA\n<!-- #elif not b -->\nB\n<!-- #endif -->\nend\n"
        assert apply_conditionals(text, flags, "x") == "end\n"

    @pytest.mark.parametrize(
        "outer,inner,expected",
        [
            (True, True, "outer-start\ninner\nouter-end\n"),
            (True, False, "outer-start\nouter-end\n"),
            (False, True, ""),
            (False, False, ""),
        ],
    )
    def test_nesting_two_deep(self, outer: bool, inner: bool, expected: str) -> None:
        flags = {"o": outer, "i": inner}
        text = (
            "<!-- #if o -->\nouter-start\n"
            "<!-- #if i -->\ninner\n<!-- #endif -->\n"
            "outer-end\n<!-- #endif -->\n"
        )
        assert apply_conditionals(text, flags, "x") == expected

    def test_nested_else_inside_false_outer_stays_dropped(self) -> None:
        flags = {"o": False, "i": False}
        text = (
            "<!-- #if o -->\n"
            "<!-- #if i -->\nA\n<!-- #else -->\nB\n<!-- #endif -->\n"
            "<!-- #endif -->\nend\n"
        )
        assert apply_conditionals(text, flags, "x") == "end\n"

    def test_three_deep_nesting(self) -> None:
        flags = {"a": True, "b": True, "c": False}
        text = (
            "<!-- #if a -->\n1\n<!-- #if b -->\n2\n<!-- #if c -->\n3\n"
            "<!-- #endif -->\n<!-- #endif -->\n<!-- #endif -->\n"
        )
        assert apply_conditionals(text, flags, "x") == "1\n2\n"

    def test_no_directives_text_unchanged(self) -> None:
        text = "# Heading\n\nSome **bold** text.\n"
        assert _apply(text) == text


# ---------------------------------------------------------------------------
# apply_conditionals — removal semantics
# ---------------------------------------------------------------------------


class TestRemoval:
    def test_no_blank_line_left_behind(self) -> None:
        text = "para one\n<!-- #if hybrid -->\ngone\n<!-- #endif -->\npara two\n"
        # Adjacent lines merge — no blank line where the directives were.
        assert _apply(text) == "para one\npara two\n"

    def test_tight_list_survives(self) -> None:
        text = (
            "- Always shown\n"
            "<!-- #if in_person_class -->\n"
            "- In-person only item\n"
            "<!-- #endif -->\n"
            "- Also always shown\n"
        )
        assert _apply(text) == (
            "- Always shown\n- In-person only item\n- Also always shown\n"
        )

    def test_tight_list_survives_when_item_excluded(self) -> None:
        text = (
            "- Always shown\n"
            "<!-- #if hybrid -->\n"
            "- Hybrid only item\n"
            "<!-- #endif -->\n"
            "- Also always shown\n"
        )
        assert _apply(text) == "- Always shown\n- Also always shown\n"


# ---------------------------------------------------------------------------
# apply_conditionals — fence-awareness
# ---------------------------------------------------------------------------


class TestFences:
    def test_directive_inside_fence_is_literal(self) -> None:
        text = "```\n<!-- #if hybrid -->\nexample\n<!-- #endif -->\n```\n"
        assert _apply(text) == text

    def test_fenced_block_in_false_branch_dropped_wholesale(self) -> None:
        text = (
            "<!-- #if hybrid -->\n"
            "```python\ncode\n```\n"
            "<!-- #endif -->\nend\n"
        )
        assert _apply(text) == "end\n"

    def test_fenced_block_in_true_branch_kept_wholesale(self) -> None:
        text = (
            "<!-- #if in_person_class -->\n"
            "```python\n<!-- #endif -->\n```\n"
            "<!-- #endif -->\nend\n"
        )
        # The #endif inside the fence is literal; the real #endif closes the if.
        assert _apply(text) == "```python\n<!-- #endif -->\n```\nend\n"

    def test_directive_lookalike_in_raw_attribute_block_untouched(self) -> None:
        text = "```{=html}\n<!-- #if hybrid -->\nhidden note\n```\nend\n"
        assert _apply(text) == text


# ---------------------------------------------------------------------------
# apply_conditionals — pass-through of non-directive comments
# ---------------------------------------------------------------------------


class TestPassThrough:
    @pytest.mark.parametrize(
        "line",
        [
            "<!-- #region My Region -->",
            "<!-- #endregion -->",
            "<!-- published:false -->",
            "<!-- an ordinary comment -->",
            "<!-- TODO: fix this -->",
        ],
    )
    def test_non_directive_comments_untouched(self, line: str) -> None:
        text = f"before\n{line}\nafter\n"
        assert _apply(text) == text

    def test_directive_lookalike_not_alone_on_line_untouched(self) -> None:
        text = "some text <!-- #if in_person_class --> more text\n"
        assert _apply(text) == text

    def test_comment_not_starting_with_hash_untouched(self) -> None:
        text = "<!-- if in_person_class -->\n"
        assert _apply(text) == text


# ---------------------------------------------------------------------------
# apply_conditionals — errors (§7 table)
# ---------------------------------------------------------------------------


class TestErrors:
    @pytest.mark.parametrize(
        "text,fragment",
        [
            # Undefined flag, taken branch
            ("<!-- #if no_such_flag -->\nx\n<!-- #endif -->\n", "undefined course flag 'no_such_flag'"),
            # Undefined flag in a NOT-taken branch is still an error
            (
                "<!-- #if hybrid -->\n<!-- #if no_such_flag -->\nx\n<!-- #endif -->\n<!-- #endif -->\n",
                "undefined course flag 'no_such_flag'",
            ),
            # Undefined flag in an elif arm that is never evaluated
            (
                "<!-- #if in_person_class -->\nx\n<!-- #elif no_such_flag -->\ny\n<!-- #endif -->\n",
                "undefined course flag 'no_such_flag'",
            ),
            # Reserved misspellings
            ("<!-- #ifdef x -->\n", "did you mean '#if'"),
            ("<!-- #ifndef x -->\n", "did you mean '#if not'"),
            ("<!-- #elseif x -->\n", "did you mean '#elif'"),
            ("<!-- #elsif x -->\n", "did you mean '#elif'"),
            ("<!-- #fi -->\n", "did you mean '#endif'"),
            # Missing argument
            ("<!-- #if -->\nx\n<!-- #endif -->\n", "'#if' requires a flag name"),
            ("<!-- #if in_person_class -->\nx\n<!-- #elif -->\ny\n<!-- #endif -->\n", "'#elif' requires a flag name"),
            # Unexpected argument
            ("<!-- #if in_person_class -->\nx\n<!-- #endif foo -->\n", "'#endif' takes no argument"),
            ("<!-- #if in_person_class -->\nx\n<!-- #else foo -->\ny\n<!-- #endif -->\n", "'#else' takes no argument"),
            # Malformed argument
            ("<!-- #if not -->\nx\n<!-- #endif -->\n", "invalid condition"),
            ("<!-- #if 2cool -->\nx\n<!-- #endif -->\n", "invalid condition"),
            ("<!-- #if a or b -->\nx\n<!-- #endif -->\n", "invalid condition"),
            ("<!-- #if not not hybrid -->\nx\n<!-- #endif -->\n", "invalid condition"),
            # elif/else/endif with no open #if
            ("<!-- #endif -->\n", "without a matching"),
            ("<!-- #else -->\n", "without a matching"),
            ("<!-- #elif hybrid -->\n", "without a matching"),
            # #elif / second #else after #else
            (
                "<!-- #if hybrid -->\nA\n<!-- #else -->\nB\n<!-- #elif in_person_class -->\nC\n<!-- #endif -->\n",
                "after '<!-- #else -->'",
            ),
            (
                "<!-- #if hybrid -->\nA\n<!-- #else -->\nB\n<!-- #else -->\nC\n<!-- #endif -->\n",
                "after '<!-- #else -->'",
            ),
            # Unclosed #if at end of file
            ("<!-- #if in_person_class -->\nx\n", "unclosed"),
        ],
    )
    def test_error_returns_none_and_names_file_and_problem(
        self, text: str, fragment: str, capsys: pytest.CaptureFixture
    ) -> None:
        errors: list[str] = []
        assert _apply(text, errors=errors) is None
        assert errors, "error should be recorded in the accumulator"
        combined = "\n".join(errors)
        assert "pages/x.md" in combined  # names the file
        assert fragment in combined      # names the problem
        assert "ERROR" in combined
        # Also printed as it happens (warn convention)
        assert fragment in capsys.readouterr().out

    def test_multiple_errors_all_reported(self) -> None:
        errors: list[str] = []
        text = "<!-- #endif -->\n<!-- #if nope -->\nx\n"
        assert _apply(text, errors=errors) is None
        assert len(errors) == 3  # stray endif, undefined flag, unclosed if

    def test_quiet_mode_suppresses_output(self, capsys: pytest.CaptureFixture) -> None:
        errors: list[str] = []
        result = apply_conditionals(
            "<!-- #endif -->\n", FLAGS, "pages/x.md", errors, quiet=True
        )
        assert result is None
        assert errors == []
        assert capsys.readouterr().out == ""

    def test_error_message_includes_line_content(self) -> None:
        errors: list[str] = []
        _apply("<!-- #if no_such_flag -->\nx\n<!-- #endif -->\n", errors=errors)
        assert any("<!-- #if no_such_flag -->" in e for e in errors)


# ---------------------------------------------------------------------------
# find_referenced_flags
# ---------------------------------------------------------------------------


class TestFindReferencedFlags:
    def test_all_branches_counted(self) -> None:
        text = (
            "<!-- #if a -->\nx\n<!-- #elif not b -->\ny\n<!-- #else -->\nz\n<!-- #endif -->\n"
            "<!-- #if c -->\nw\n<!-- #endif -->\n"
        )
        assert find_referenced_flags(text) == {"a", "b", "c"}

    def test_fenced_directives_ignored(self) -> None:
        text = "```\n<!-- #if fenced_flag -->\n```\n<!-- #if real_flag -->\nx\n<!-- #endif -->\n"
        assert find_referenced_flags(text) == {"real_flag"}

    def test_malformed_directives_contribute_nothing(self) -> None:
        text = "<!-- #if -->\n<!-- #if 2cool -->\n<!-- #if a or b -->\n<!-- #ifdef x -->\n"
        assert find_referenced_flags(text) == set()

    def test_endif_and_else_contribute_nothing(self) -> None:
        text = "<!-- #if a -->\nx\n<!-- #else -->\ny\n<!-- #endif -->\n"
        assert find_referenced_flags(text) == {"a"}

    def test_never_errors_on_garbage(self) -> None:
        assert find_referenced_flags("<!-- #endif -->\n<!-- #fi -->\n") == set()


# ---------------------------------------------------------------------------
# resolve_published_if / find_referenced_flags_in_frontmatter
# ---------------------------------------------------------------------------


class TestResolvePublishedIf:
    def test_no_published_if_returns_literal_published(self) -> None:
        assert resolve_published_if({"published": True}, FLAGS, "x") is True
        assert resolve_published_if({"published": False}, FLAGS, "x") is False
        assert resolve_published_if({}, FLAGS, "x") is False

    def test_true_flag(self) -> None:
        assert resolve_published_if({"published_if": "in_person_class"}, FLAGS, "x") is True

    def test_false_flag(self) -> None:
        assert resolve_published_if({"published_if": "hybrid"}, FLAGS, "x") is False

    def test_not_negates(self) -> None:
        assert resolve_published_if({"published_if": "not hybrid"}, FLAGS, "x") is True
        assert resolve_published_if(
            {"published_if": "not in_person_class"}, FLAGS, "x"
        ) is False

    def test_combined_with_published_is_error(self) -> None:
        errors: list[str] = []
        result = resolve_published_if(
            {"published": True, "published_if": "hybrid"}, FLAGS, "pages/x.md", errors
        )
        assert result is None
        assert any("cannot be combined" in e for e in errors)
        assert any("pages/x.md" in e for e in errors)

    def test_undefined_flag_is_error(self) -> None:
        errors: list[str] = []
        result = resolve_published_if(
            {"published_if": "no_such_flag"}, FLAGS, "pages/x.md", errors
        )
        assert result is None
        assert any("undefined course flag 'no_such_flag'" in e for e in errors)

    def test_malformed_condition_is_error(self) -> None:
        errors: list[str] = []
        result = resolve_published_if(
            {"published_if": "a and b"}, FLAGS, "x", errors
        )
        assert result is None
        assert any("invalid 'published_if' value" in e for e in errors)

    def test_missing_value_is_error(self) -> None:
        errors: list[str] = []
        result = resolve_published_if({"published_if": ""}, FLAGS, "x", errors)
        assert result is None
        assert any("requires a flag name" in e for e in errors)

    def test_non_string_value_is_error(self) -> None:
        errors: list[str] = []
        result = resolve_published_if({"published_if": True}, FLAGS, "x", errors)
        assert result is None
        assert any("must be a flag name string" in e for e in errors)

    def test_forbid_is_error(self) -> None:
        errors: list[str] = []
        result = resolve_published_if(
            {"published_if": "hybrid"}, FLAGS, "announcements/x.md", errors,
            forbid=True, forbid_reason="announcements",
        )
        assert result is None
        assert any("not supported for announcements" in e for e in errors)

    def test_forbid_without_published_if_is_fine(self) -> None:
        result = resolve_published_if(
            {"published": True}, FLAGS, "announcements/x.md", forbid=True,
        )
        assert result is True

    def test_quiet_suppresses_reporting(self, capsys: pytest.CaptureFixture) -> None:
        errors: list[str] = []
        result = resolve_published_if(
            {"published_if": "no_such_flag"}, FLAGS, "x", errors, quiet=True
        )
        assert result is None
        assert errors == []
        assert capsys.readouterr().out == ""


class TestFindReferencedFlagsInFrontmatter:
    def test_finds_published_if_flag(self) -> None:
        assert find_referenced_flags_in_frontmatter(
            {"published_if": "in_person_class"}
        ) == {"in_person_class"}

    def test_finds_negated_flag(self) -> None:
        assert find_referenced_flags_in_frontmatter(
            {"published_if": "not hybrid"}
        ) == {"hybrid"}

    def test_no_published_if_key(self) -> None:
        assert find_referenced_flags_in_frontmatter({"title": "X"}) == set()

    def test_malformed_contributes_nothing(self) -> None:
        assert find_referenced_flags_in_frontmatter({"published_if": "a and b"}) == set()
        assert find_referenced_flags_in_frontmatter({"published_if": 5}) == set()


# ---------------------------------------------------------------------------
# load_course_flags
# ---------------------------------------------------------------------------


class TestLoadCourseFlags:
    def _write_settings(self, tmp_path: Path, content: str) -> Path:
        cs = tmp_path / "course_settings"
        cs.mkdir(exist_ok=True)
        (cs / "course_settings.toml").write_text(content)
        return tmp_path

    def test_no_settings_file(self, tmp_path: Path) -> None:
        assert load_course_flags(tmp_path) == {}

    def test_no_course_flags_table(self, tmp_path: Path) -> None:
        repo = self._write_settings(tmp_path, 'title = "Course"\n')
        assert load_course_flags(repo) == {}

    def test_reads_flags(self, tmp_path: Path) -> None:
        repo = self._write_settings(
            tmp_path, "[course_flags]\nin_person_class = true\nhybrid = false\n"
        )
        assert load_course_flags(repo) == {"in_person_class": True, "hybrid": False}

    def test_preloaded_settings_dict(self, tmp_path: Path) -> None:
        settings = {"course_flags": {"a": True}}
        assert load_course_flags(tmp_path, settings) == {"a": True}

    def test_non_boolean_value_is_fatal(self, tmp_path: Path) -> None:
        repo = self._write_settings(tmp_path, '[course_flags]\nquarter = "fall"\n')
        with pytest.raises(ValueError, match="must be a TOML boolean"):
            load_course_flags(repo)

    def test_integer_value_is_fatal(self, tmp_path: Path) -> None:
        repo = self._write_settings(tmp_path, "[course_flags]\nweeks = 10\n")
        with pytest.raises(ValueError, match="must be a TOML boolean"):
            load_course_flags(repo)

    def test_invalid_flag_name_is_fatal(self, tmp_path: Path) -> None:
        repo = self._write_settings(tmp_path, '[course_flags]\n"2cool" = true\n')
        with pytest.raises(ValueError, match="invalid course flag name"):
            load_course_flags(repo)


# ---------------------------------------------------------------------------
# Snippet-level conditionals (preprocess_snippets flags param)
# ---------------------------------------------------------------------------


class TestSnippetConditionals:
    def _repo(self, tmp_path: Path, snippet_text: str) -> tuple[Path, Path]:
        repo = tmp_path / "repo"
        (repo / "snippets").mkdir(parents=True)
        (repo / "pages").mkdir()
        (repo / "snippets" / "s.md").write_text(snippet_text)
        page = repo / "pages" / "p.md"
        page.write_text("[snippet](../snippets/s.md)\n")
        return repo, page

    def test_snippet_content_filtered(self, tmp_path: Path) -> None:
        repo, page = self._repo(
            tmp_path,
            "always\n<!-- #if hybrid -->\nhybrid only\n<!-- #endif -->\n",
        )
        result = preprocess_snippets(
            page.read_text(), page, repo / "snippets", flags=FLAGS
        )
        assert "always" in result
        assert "hybrid only" not in result

    def test_snippet_directive_error_leaves_ref_unexpanded(self, tmp_path: Path) -> None:
        repo, page = self._repo(tmp_path, "<!-- #if no_such -->\nx\n<!-- #endif -->\n")
        errors: list[str] = []
        result = preprocess_snippets(
            page.read_text(), page, repo / "snippets", errors, flags=FLAGS
        )
        assert "[snippet](../snippets/s.md)" in result  # left unexpanded
        assert any("no_such" in e for e in errors)
        assert any("snippets/s.md" in e for e in errors)  # names the snippet

    def test_no_flags_means_no_conditional_processing(self, tmp_path: Path) -> None:
        repo, page = self._repo(
            tmp_path, "<!-- #if hybrid -->\nhybrid only\n<!-- #endif -->\n"
        )
        result = preprocess_snippets(page.read_text(), page, repo / "snippets")
        assert "hybrid only" in result  # directives passed through untouched

    def test_unbalanced_across_snippet_boundary_is_error(self, tmp_path: Path) -> None:
        """An #if opened in the page cannot be closed inside a snippet."""
        repo, page = self._repo(tmp_path, "<!-- #endif -->\n")
        page.write_text("<!-- #if in_person_class -->\n[snippet](../snippets/s.md)\n")
        errors: list[str] = []
        # The page's own pass errors on the unclosed #if...
        assert apply_conditionals(page.read_text(), FLAGS, "pages/p.md", errors) is None
        assert any("unclosed" in e for e in errors)
        # ...and the snippet's independent pass errors on the stray #endif.
        snippet_errors: list[str] = []
        result = preprocess_snippets(
            "[snippet](../snippets/s.md)\n", page, repo / "snippets",
            snippet_errors, flags=FLAGS,
        )
        assert "[snippet](../snippets/s.md)" in result
        assert any("without a matching" in e for e in snippet_errors)


# ---------------------------------------------------------------------------
# Flag-aware staleness (manifest.needs_sync / flag_change)
# ---------------------------------------------------------------------------


class TestFlagStaleness:
    _FUTURE = "2999-12-31T00:00:00+00:00"

    def _entry(self, flags_used: dict | None) -> dict:
        entry = {"canvas_id": 1, "canvas_type": "page", "last_synced": self._FUTURE}
        if flags_used is not None:
            entry["flags_used"] = flags_used
        return entry

    def test_unchanged_flags_not_stale(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("x")
        manifest = {"pages/p.md": self._entry({"a": True})}
        assert not manifest_lib.needs_sync(
            manifest, "pages/p.md", f, current_flags={"a": True}
        )

    def test_changed_flag_value_is_stale(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("x")
        manifest = {"pages/p.md": self._entry({"a": True})}
        assert manifest_lib.needs_sync(
            manifest, "pages/p.md", f, current_flags={"a": False}
        )

    def test_deleted_flag_is_stale(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("x")
        manifest = {"pages/p.md": self._entry({"a": True})}
        assert manifest_lib.needs_sync(manifest, "pages/p.md", f, current_flags={})

    def test_no_flags_used_table_ignores_flags(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("x")
        manifest = {"pages/p.md": self._entry(None)}
        assert not manifest_lib.needs_sync(
            manifest, "pages/p.md", f, current_flags={"a": True}
        )

    def test_none_current_flags_skips_check(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("x")
        manifest = {"pages/p.md": self._entry({"a": True})}
        assert not manifest_lib.needs_sync(manifest, "pages/p.md", f)

    def test_verbose_prints_change_reason(self, tmp_path: Path, capsys) -> None:
        f = tmp_path / "p.md"
        f.write_text("x")
        manifest = {"pages/p.md": self._entry({"a": True})}
        manifest_lib.needs_sync(
            manifest, "pages/p.md", f, current_flags={"a": False}, verbose=True
        )
        assert "re-syncing: flag 'a' changed true → false" in capsys.readouterr().out

    def test_verbose_prints_deleted_reason(self, tmp_path: Path, capsys) -> None:
        f = tmp_path / "p.md"
        f.write_text("x")
        manifest = {"pages/p.md": self._entry({"a": True})}
        manifest_lib.needs_sync(
            manifest, "pages/p.md", f, current_flags={}, verbose=True
        )
        assert "flag 'a' deleted from course_settings.toml" in capsys.readouterr().out

    def test_flag_change_helper(self) -> None:
        entry = self._entry({"a": True, "b": False})
        assert manifest_lib.flag_change(entry, {"a": True, "b": False}) is None
        assert manifest_lib.flag_change(entry, {"a": False, "b": False}) == ("a", True, False)
        assert manifest_lib.flag_change(entry, {"b": False}) == ("a", True, None)


# ---------------------------------------------------------------------------
# Unused-flag warning (check_course_flags_coverage)
# ---------------------------------------------------------------------------


class TestFlagsCoverage:
    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "pages").mkdir(parents=True)
        (repo / "snippets").mkdir()
        (repo / "pages" / "p.md").write_text(
            "---\ntitle: P\n---\n<!-- #if used_flag -->\nx\n<!-- #endif -->\n"
        )
        (repo / "snippets" / "s.md").write_text(
            "<!-- #if snippet_flag -->\ny\n<!-- #endif -->\n"
        )
        return repo

    def test_unused_flag_warns_once(self, tmp_path: Path, capsys) -> None:
        repo = self._repo(tmp_path)
        flags = {"used_flag": True, "snippet_flag": False, "unused_flag": True}
        check_course_flags_coverage(flags, repo)
        out = capsys.readouterr().out
        assert out.count("course flag 'unused_flag' is defined") == 1
        assert "'used_flag'" not in out
        # A flag referenced only inside a snippet counts as used.
        assert "'snippet_flag'" not in out

    def test_no_flags_no_output(self, tmp_path: Path, capsys) -> None:
        check_course_flags_coverage({}, self._repo(tmp_path))
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Integration: sync pipeline with mocked canvasapi
# ---------------------------------------------------------------------------

COURSE_ID = 999
_FLAGS_TOML = "[course_flags]\nin_person_class = true\nhybrid = false\n"


def _config() -> Config:
    return Config(base_url="https://school.instructure.com", course_id=COURSE_ID, api_token="tok")


def _mock_page(page_id: int, url: str) -> MagicMock:
    p = MagicMock()
    p.page_id = page_id
    p.url = url
    p.html_url = f"https://school.instructure.com/courses/1/pages/{url}"
    p.edit.return_value = p
    return p


def _mock_quiz(quiz_id: int) -> MagicMock:
    q = MagicMock()
    q.id = quiz_id
    q.html_url = f"https://school.instructure.com/courses/1/quizzes/{quiz_id}"
    q.published = False
    q.edit.return_value = q
    q.get_questions.return_value = []
    created = []

    def _create_question(question):
        cq = MagicMock()
        cq.id = 1000 + len(created)
        created.append(question)
        return cq

    q.create_question.side_effect = _create_question
    return q


def _mock_module(canvas_id: int) -> MagicMock:
    m = MagicMock()
    m.id = canvas_id
    m.edit.return_value = m
    m.get_module_items.return_value = []
    items = []

    def _create_item(module_item):
        mi = MagicMock()
        mi.id = 200 + len(items)
        items.append(module_item)
        return mi

    m.create_module_item.side_effect = _create_item
    return m


@pytest.fixture
def mock_course(mocker) -> MagicMock:
    mock_canvas_cls = mocker.patch("github_to_canvas.canvas_api.Canvas")
    course = MagicMock()
    mock_canvas_cls.return_value.get_course.return_value = course
    return course


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _basic_repo(tmp_path: Path, flags_toml: str = _FLAGS_TOML) -> Path:
    """Course repo with one flagged page and one flag-free page."""
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", flags_toml)
    _write(
        root / "pages" / "flagged.md",
        "---\ntitle: Flagged\npublished: true\n---\n\n"
        "<!-- #if in_person_class -->\nBring your laptop to Room 302.\n"
        "<!-- #else -->\nJoin the Zoom link.\n<!-- #endif -->\n",
    )
    _write(
        root / "pages" / "plain.md",
        "---\ntitle: Plain\npublished: true\n---\n\nNothing conditional here.\n",
    )
    return root


def _make_old(path: Path) -> None:
    import os

    os.utime(path, (0.0, 0.0))


def test_conditional_page_content_filtered(mock_course, tmp_path) -> None:
    root = _basic_repo(tmp_path)
    pages = {}

    def _create_page(wiki_page):
        p = _mock_page(len(pages) + 1, wiki_page["title"].lower())
        pages[wiki_page["title"]] = wiki_page
        return p

    mock_course.create_page.side_effect = _create_page

    had_errors = run_sync(_config(), root)

    assert had_errors is False
    body = pages["Flagged"]["body"]
    assert "Bring your laptop to Room 302." in body
    assert "Join the Zoom link" not in body


def test_flags_used_recorded_and_omitted(mock_course, tmp_path) -> None:
    root = _basic_repo(tmp_path)
    mock_course.create_page.side_effect = [_mock_page(1, "flagged"), _mock_page(2, "plain")]

    run_sync(_config(), root)

    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert manifest["pages/flagged.md"]["flags_used"] == {"in_person_class": True}
    assert "flags_used" not in manifest["pages/plain.md"]


def test_snippet_contributed_flags_recorded_and_applied(mock_course, tmp_path) -> None:
    root = _basic_repo(tmp_path)
    _write(
        root / "snippets" / "cond.md",
        "<!-- #if hybrid -->\nHybrid note.\n<!-- #endif -->\nShared text.\n",
    )
    _write(
        root / "pages" / "with-snippet.md",
        "---\ntitle: WithSnippet\npublished: true\n---\n\n[s](../snippets/cond.md)\n",
    )
    pages = {}

    def _create_page(wiki_page):
        pages[wiki_page["title"]] = wiki_page
        return _mock_page(len(pages), wiki_page["title"].lower())

    mock_course.create_page.side_effect = _create_page

    run_sync(_config(), root)

    body = pages["WithSnippet"]["body"]
    assert "Shared text." in body
    assert "Hybrid note." not in body  # hybrid = false
    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert manifest["pages/with-snippet.md"]["flags_used"] == {"hybrid": False}


def test_flag_flip_resyncs_only_referencing_files(mock_course, tmp_path, capsys) -> None:
    root = _basic_repo(tmp_path)
    mock_course.create_page.side_effect = [_mock_page(1, "flagged"), _mock_page(2, "plain")]

    run_sync(_config(), root)

    # Flip the flag; make all content older than last_synced so only the
    # flag check can trigger a re-sync.
    _write(
        root / "course_settings" / "course_settings.toml",
        "[course_flags]\nin_person_class = false\nhybrid = false\n",
    )
    _make_old(root / "pages" / "flagged.md")
    _make_old(root / "pages" / "plain.md")

    real_page = _mock_page(1, "flagged")
    mock_course.get_page.return_value = real_page
    mock_course.create_page.side_effect = None
    mock_course.create_page.reset_mock()

    capsys.readouterr()  # discard first-run output
    run_sync(_config(), root, verbose=True)

    out = capsys.readouterr().out
    assert "re-syncing: flag 'in_person_class' changed true → false" in out
    assert "Processing: pages/flagged.md" in out
    assert "Skipping (up-to-date): pages/plain.md" in out
    # The re-uploaded body now takes the else branch.
    body = real_page.edit.call_args[1]["wiki_page"]["body"]
    assert "Join the Zoom link" in body
    assert "Bring your laptop" not in body
    # flags_used refreshed to the new value.
    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert manifest["pages/flagged.md"]["flags_used"] == {"in_person_class": False}


def test_deleted_flag_goes_stale_and_errors(mock_course, tmp_path, capsys) -> None:
    root = _basic_repo(tmp_path)
    mock_course.create_page.side_effect = [_mock_page(1, "flagged"), _mock_page(2, "plain")]

    run_sync(_config(), root)

    _write(root / "course_settings" / "course_settings.toml", 'title = "No flags"\n')
    _make_old(root / "pages" / "flagged.md")
    _make_old(root / "pages" / "plain.md")
    mock_course.get_page.return_value = _mock_page(1, "flagged")

    capsys.readouterr()
    had_errors = run_sync(_config(), root, verbose=True)

    out = capsys.readouterr().out
    assert "re-syncing: flag 'in_person_class' deleted from course_settings.toml" in out
    assert "undefined course flag 'in_person_class'" in out
    assert had_errors is True


def test_undefined_flag_skips_file_others_still_sync(mock_course, tmp_path, capsys) -> None:
    root = _basic_repo(tmp_path)
    _write(
        root / "pages" / "typo.md",
        "---\ntitle: Typo\npublished: true\n---\n\n"
        "<!-- #if in_persn_class -->\nx\n<!-- #endif -->\n",
    )
    created = []

    def _create_page(wiki_page):
        created.append(wiki_page["title"])
        return _mock_page(len(created), wiki_page["title"].lower())

    mock_course.create_page.side_effect = _create_page

    had_errors = run_sync(_config(), root)

    out = capsys.readouterr().out
    assert had_errors is True
    assert "undefined course flag 'in_persn_class'" in out
    assert "Skipping upload due to errors: pages/typo.md" in out
    # The other pages synced fine.
    assert sorted(created) == ["Flagged", "Plain"]
    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert "pages/typo.md" not in manifest


def test_conditional_module_item_excluded(mock_course, tmp_path) -> None:
    root = _basic_repo(tmp_path)
    _write(
        root / "modules" / "week-1.md",
        "---\ntitle: Week 1\n---\n\n"
        "- [Plain](../pages/plain.md)\n"
        "<!-- #if hybrid -->\n"
        "- [Flagged](../pages/flagged.md)\n"
        "<!-- #endif -->\n",
    )
    mock_course.create_page.side_effect = [_mock_page(1, "flagged"), _mock_page(2, "plain")]
    module = _mock_module(66)
    mock_course.create_module.return_value = module

    run_sync(_config(), root)

    item_calls = module.create_module_item.call_args_list
    titles = [c[1]["module_item"].get("title") for c in item_calls]
    assert "Plain" in titles
    assert "Flagged" not in titles  # hybrid = false → item excluded
    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert manifest["modules/week-1.md"]["flags_used"] == {"hybrid": False}


def test_conditional_quiz_question_excluded(mock_course, tmp_path) -> None:
    root = _basic_repo(tmp_path)
    _write(
        root / "quizzes" / "q1" / "q1.md",
        "---\ntitle: Quiz 1\npublished: false\n---\n\nIntro.\n\n"
        "1. [Always](questions/always.md)\n"
        "<!-- #if hybrid -->\n"
        "2. [Hybrid only](questions/hybrid-only.md)\n"
        "<!-- #endif -->\n",
    )
    _write(
        root / "quizzes" / "q1" / "questions" / "always.md",
        "---\ntitle: Always\nquestion_type: essay_question\npoints_possible: 1\n---\n\nQ?\n",
    )
    _write(
        root / "quizzes" / "q1" / "questions" / "hybrid-only.md",
        "---\ntitle: Hybrid only\nquestion_type: essay_question\npoints_possible: 1\n---\n\nQ?\n",
    )
    mock_course.create_page.side_effect = [_mock_page(1, "flagged"), _mock_page(2, "plain")]
    quiz = _mock_quiz(42)
    mock_course.create_quiz.return_value = quiz
    mock_course.get_quiz.return_value = quiz

    run_sync(_config(), root)

    question_names = [
        c[1]["question"]["question_name"]
        for c in quiz.create_question.call_args_list
    ]
    assert question_names == ["Always"]  # existing deletion path handles the rest
    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    # flags_used covers the quiz .md and all question files (one unit).
    assert manifest["quizzes/q1/q1.md"]["flags_used"] == {"hybrid": False}


def test_unused_flag_warning_in_run_sync(mock_course, tmp_path, capsys) -> None:
    root = _basic_repo(
        tmp_path,
        "[course_flags]\nin_person_class = true\nhybrid = false\nunused_flag = true\n",
    )
    mock_course.create_page.side_effect = [_mock_page(1, "flagged"), _mock_page(2, "plain")]

    had_errors = run_sync(_config(), root)

    out = capsys.readouterr().out
    assert out.count("course flag 'unused_flag' is defined") == 1
    assert "course flag 'in_person_class' is defined" not in out
    assert had_errors is False  # warning only — never an error


# ---------------------------------------------------------------------------
# Integration: published_if frontmatter
# ---------------------------------------------------------------------------


def test_published_if_true_publishes_page(mock_course, tmp_path) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "pages" / "p.md",
        "---\ntitle: P\npublished_if: in_person_class\n---\n\nBody.\n",
    )
    pages = {}
    mock_course.create_page.side_effect = lambda wiki_page: (
        pages.__setitem__("P", wiki_page) or _mock_page(1, "p")
    )

    had_errors = run_sync(_config(), root)

    assert had_errors is False
    assert pages["P"]["published"] is True


def test_published_if_false_leaves_page_unpublished(mock_course, tmp_path) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "pages" / "p.md",
        "---\ntitle: P\npublished_if: hybrid\n---\n\nBody.\n",
    )
    pages = {}
    mock_course.create_page.side_effect = lambda wiki_page: (
        pages.__setitem__("P", wiki_page) or _mock_page(1, "p")
    )

    had_errors = run_sync(_config(), root)

    assert had_errors is False
    assert pages["P"]["published"] is False


def test_published_if_not_negates(mock_course, tmp_path) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "pages" / "p.md",
        "---\ntitle: P\npublished_if: not hybrid\n---\n\nBody.\n",
    )
    pages = {}
    mock_course.create_page.side_effect = lambda wiki_page: (
        pages.__setitem__("P", wiki_page) or _mock_page(1, "p")
    )

    run_sync(_config(), root)

    assert pages["P"]["published"] is True


def test_published_if_combined_with_published_is_error(mock_course, tmp_path, capsys) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "pages" / "bad.md",
        "---\ntitle: Bad\npublished: true\npublished_if: hybrid\n---\n\nBody.\n",
    )
    _write(
        root / "pages" / "good.md",
        "---\ntitle: Good\npublished: true\n---\n\nBody.\n",
    )
    mock_course.create_page.side_effect = [_mock_page(1, "good")]

    had_errors = run_sync(_config(), root)

    out = capsys.readouterr().out
    assert had_errors is True
    assert "cannot be combined" in out
    assert "Skipping upload due to errors: pages/bad.md" in out
    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert "pages/bad.md" not in manifest
    assert "pages/good.md" in manifest


def test_published_if_undefined_flag_is_error(mock_course, tmp_path, capsys) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "pages" / "bad.md",
        "---\ntitle: Bad\npublished_if: no_such_flag\n---\n\nBody.\n",
    )

    had_errors = run_sync(_config(), root)

    out = capsys.readouterr().out
    assert had_errors is True
    assert "undefined course flag 'no_such_flag'" in out
    mock_course.create_page.assert_not_called()


def test_published_if_forbidden_for_announcements(mock_course, tmp_path, capsys) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "announcements" / "a.md",
        "---\ntitle: A\npublished_if: in_person_class\n---\n\nBody.\n",
    )

    had_errors = run_sync(_config(), root)

    out = capsys.readouterr().out
    assert had_errors is True
    assert "not supported for announcements" in out
    for call in mock_course.create_discussion_topic.call_args_list:
        assert not call.kwargs.get("is_announcement")


def test_published_if_flags_used_recorded_and_flag_flip_republishes(
    mock_course, tmp_path, capsys
) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "pages" / "p.md",
        "---\ntitle: P\npublished_if: in_person_class\n---\n\nBody.\n",
    )
    real_page = _mock_page(1, "p")
    mock_course.create_page.return_value = real_page

    run_sync(_config(), root)

    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert manifest["pages/p.md"]["flags_used"] == {"in_person_class": True}
    real_page.edit.assert_not_called()

    # Flip the flag; the page's own mtime is unchanged, so only the recorded
    # flags_used mismatch should trigger a re-sync.
    _write(
        root / "course_settings" / "course_settings.toml",
        "[course_flags]\nin_person_class = false\nhybrid = false\n",
    )
    _make_old(root / "pages" / "p.md")
    mock_course.get_page.return_value = real_page

    capsys.readouterr()
    run_sync(_config(), root, verbose=True)

    out = capsys.readouterr().out
    assert "re-syncing: flag 'in_person_class' changed true → false" in out
    assert real_page.edit.call_args[1]["wiki_page"]["published"] is False
    manifest = manifest_lib.load(root / ".canvas-manifest.toml")
    assert manifest["pages/p.md"]["flags_used"] == {"in_person_class": False}


def test_published_if_module_item_default(mock_course, tmp_path) -> None:
    """A module item with no explicit published override defers to the
    referenced page's `published_if` (via _content_default_published)."""
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "pages" / "on.md",
        "---\ntitle: On\npublished_if: in_person_class\n---\n\nBody.\n",
    )
    _write(
        root / "pages" / "off.md",
        "---\ntitle: Off\npublished_if: hybrid\n---\n\nBody.\n",
    )
    _write(
        root / "modules" / "week-1.md",
        "---\ntitle: Week 1\n---\n\n"
        "- [On](../pages/on.md)\n"
        "- [Off](../pages/off.md)\n",
    )
    mock_course.create_page.side_effect = [_mock_page(1, "on"), _mock_page(2, "off")]
    module = _mock_module(66)
    mock_course.create_module.return_value = module

    run_sync(_config(), root)

    item_calls = module.create_module_item.call_args_list
    by_title = {c[1]["module_item"]["title"]: c[1]["module_item"] for c in item_calls}
    assert by_title["On"]["published"] is True
    assert by_title["Off"]["published"] is False


def test_published_if_quiz(mock_course, tmp_path) -> None:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "quizzes" / "q1" / "q1.md",
        "---\ntitle: Quiz 1\npublished_if: in_person_class\n---\n\nIntro.\n",
    )
    quiz = _mock_quiz(42)
    mock_course.create_quiz.return_value = quiz
    mock_course.get_quiz.return_value = quiz

    run_sync(_config(), root)

    assert quiz.edit.call_args[1]["quiz"]["published"] is True


def test_mv_preserves_flags_used(tmp_path) -> None:
    root = _basic_repo(tmp_path)
    manifest_path = root / ".canvas-manifest.toml"
    with manifest_path.open("wb") as f:
        tomli_w.dump(
            {
                "pages/flagged.md": {
                    "canvas_id": 1,
                    "canvas_type": "page",
                    "canvas_url": "flagged",
                    "last_synced": "2025-01-01T00:00:00+00:00",
                    "flags_used": {"in_person_class": True},
                }
            },
            f,
        )

    run_mv(root / "pages" / "flagged.md", root / "pages" / "renamed.md")

    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)
    assert "pages/flagged.md" not in manifest
    assert manifest["pages/renamed.md"]["flags_used"] == {"in_person_class": True}


# ---------------------------------------------------------------------------
# Integration: publish staging respects flags
# ---------------------------------------------------------------------------


def _publish_repo(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    _write(root / "course_settings" / "course_settings.toml", _FLAGS_TOML)
    _write(
        root / "assignments" / "hw1.md",
        "---\ntitle: HW 1\npublished: true\n---\n\n"
        "<!-- #if in_person_class -->\nSubmit on paper in class.\n"
        "<!-- #else -->\nSubmit online.\n<!-- #endif -->\n",
    )
    _write(
        root / "modules" / "week-1.md",
        "---\ntitle: Week 1\npublished: true\n---\n\n"
        "- [HW 1](../assignments/hw1.md)\n"
        "<!-- #if hybrid -->\n"
        "- [Hybrid page](../pages/hybrid-only.md)\n"
        "<!-- #endif -->\n",
    )
    _write(
        root / "pages" / "hybrid-only.md",
        "---\ntitle: Hybrid Only\npublished: true\n---\n\nHybrid content.\n",
    )
    return root


def test_publish_stage_filters_flagged_content(tmp_path) -> None:
    root = _publish_repo(tmp_path)
    staging = tmp_path / "staging"

    info = publish.stage(root, staging)

    assert info["errors"] == []
    staged_hw = (staging / "docs" / "assignments" / "hw1.md").read_text()
    assert "Submit on paper in class." in staged_hw
    assert "Submit online." not in staged_hw
    # The false-branch module item is gone from the overview...
    overview = (staging / "docs" / "modules" / "week-1.md").read_text()
    assert "Hybrid page" not in overview
    # ...and the page it pointed at is not reachable, so it is not staged.
    assert not (staging / "docs" / "pages" / "hybrid-only.md").exists()


def test_publish_stage_reports_directive_errors(tmp_path, capsys) -> None:
    root = _publish_repo(tmp_path)
    _write(
        root / "assignments" / "broken.md",
        "---\ntitle: Broken\npublished: true\n---\n\n"
        "<!-- #if no_such_flag -->\nx\n<!-- #endif -->\n",
    )
    staging = tmp_path / "staging"

    info = publish.stage(root, staging)

    out = capsys.readouterr().out
    assert any("no_such_flag" in e for e in info["errors"])
    assert "Skipping (conditional-directive errors): assignments/broken.md" in out
    assert not (staging / "docs" / "assignments" / "broken.md").exists()
    # The healthy assignment still staged.
    assert (staging / "docs" / "assignments" / "hw1.md").exists()


def test_publish_stage_respects_published_if(tmp_path) -> None:
    root = _publish_repo(tmp_path)
    _write(
        root / "assignments" / "hw2.md",
        "---\ntitle: HW 2\npublished_if: hybrid\n---\n\nOnly for hybrid offerings.\n",
    )
    staging = tmp_path / "staging"

    info = publish.stage(root, staging)

    assert info["errors"] == []
    # hybrid = false in _FLAGS_TOML, so HW 2 is excluded from discovery/nav
    # and never staged.
    assert not (staging / "docs" / "assignments" / "hw2.md").exists()
    mkdocs_yml = (staging / "mkdocs.yml").read_text()
    assert "HW 2" not in mkdocs_yml
    assert "HW 1" in mkdocs_yml
