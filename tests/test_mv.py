"""Tests for the mv (move/rename) command."""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
import tomli_w

from github_to_canvas.mv import (
    build_path_map,
    compute_course_settings_updates,
    compute_file_updates,
    compute_manifest_updates,
    compute_module_order_updates,
    find_repo_root,
    run_mv,
    transform_links,
    validate_move,
)


def _make_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a minimal course repo with course_settings.toml."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "course_settings").mkdir()
    (repo / "course_settings" / "course_settings.toml").write_text("")
    if files:
        for rel_path, content in files.items():
            p = repo / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return repo


def _make_manifest(repo: Path, entries: dict) -> Path:
    manifest_path = repo / ".canvas-manifest.toml"
    with manifest_path.open("wb") as f:
        tomli_w.dump(entries, f)
    return manifest_path


# ---------------------------------------------------------------------------
# find_repo_root
# ---------------------------------------------------------------------------


class TestFindRepoRoot:
    def test_finds_root_from_subdir(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "pages").mkdir()
        assert find_repo_root(repo / "pages") == repo

    def test_finds_root_from_deep_subdir(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        deep = repo / "assets" / "images" / "sub"
        deep.mkdir(parents=True)
        assert find_repo_root(deep) == repo

    def test_finds_root_from_file(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/foo.md": "# Foo"})
        assert find_repo_root(repo / "pages" / "foo.md") == repo

    def test_returns_none_when_no_repo(self, tmp_path: Path) -> None:
        assert find_repo_root(tmp_path) is None


# ---------------------------------------------------------------------------
# validate_move
# ---------------------------------------------------------------------------


class TestValidateMove:
    def test_rejects_missing_source(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        with pytest.raises(ValueError, match="Source does not exist"):
            validate_move(repo / "pages/nope.md", repo / "pages/dest.md", repo)

    def test_rejects_existing_destination(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/a.md": "# A",
            "pages/b.md": "# B",
        })
        with pytest.raises(ValueError, match="Destination already exists"):
            validate_move(repo / "pages/a.md", repo / "pages/b.md", repo)

    def test_rejects_cross_type_move(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/a.md": "# A"})
        (repo / "assignments").mkdir()
        with pytest.raises(ValueError, match="Cannot move across content types"):
            validate_move(repo / "pages/a.md", repo / "assignments/a.md", repo)

    def test_rejects_source_outside_repo(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        outside = tmp_path / "outside.md"
        outside.write_text("x")
        with pytest.raises(ValueError, match="outside the course repo"):
            validate_move(outside, repo / "pages/dest.md", repo)

    def test_rejects_nonexistent_dest_parent(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/a.md": "# A"})
        with pytest.raises(ValueError, match="Destination parent directory does not exist"):
            validate_move(repo / "pages/a.md", repo / "pages/no-such-dir/a.md", repo)

    def test_accepts_valid_move(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/a.md": "# A"})
        validate_move(repo / "pages/a.md", repo / "pages/b.md", repo)

    def test_accepts_move_to_existing_subdir(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/a.md": "# A"})
        (repo / "pages" / "sub").mkdir()
        validate_move(repo / "pages/a.md", repo / "pages/sub/a.md", repo)


# ---------------------------------------------------------------------------
# build_path_map
# ---------------------------------------------------------------------------


class TestBuildPathMap:
    def test_single_file_rename(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/old.md": "# Old"})
        pm = build_path_map(repo / "pages/old.md", repo / "pages/new.md", repo)
        assert pm == {"pages/old.md": "pages/new.md"}

    def test_directory_rename(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "assets/Old/a.png": "img-a",
            "assets/Old/b.png": "img-b",
            "assets/Old/sub/c.png": "img-c",
        })
        pm = build_path_map(repo / "assets/Old", repo / "assets/new", repo)
        assert pm == {
            "assets/Old/a.png": "assets/new/a.png",
            "assets/Old/b.png": "assets/new/b.png",
            "assets/Old/sub/c.png": "assets/new/sub/c.png",
        }

    def test_quiz_folder_rename_renames_inner_md(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "quizzes/old-quiz/old-quiz.md": "---\ntitle: Old Quiz\n---",
            "quizzes/old-quiz/questions/q1.md": "Q1",
        })
        pm = build_path_map(
            repo / "quizzes/old-quiz", repo / "quizzes/new-quiz", repo,
        )
        assert pm["quizzes/old-quiz/old-quiz.md"] == "quizzes/new-quiz/new-quiz.md"
        assert pm["quizzes/old-quiz/questions/q1.md"] == "quizzes/new-quiz/questions/q1.md"

    def test_question_bank_folder_rename_renames_inner_toml(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "question_banks/old-bank/old-bank.toml": 'bank_title = "Old"',
            "question_banks/old-bank/questions/q1.md": "Q1",
        })
        pm = build_path_map(
            repo / "question_banks/old-bank", repo / "question_banks/new-bank", repo,
        )
        assert pm["question_banks/old-bank/old-bank.toml"] == "question_banks/new-bank/new-bank.toml"

    def test_move_file_to_subdirectory(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/a.md": "# A"})
        (repo / "pages" / "sub").mkdir()
        pm = build_path_map(repo / "pages/a.md", repo / "pages/sub/a.md", repo)
        assert pm == {"pages/a.md": "pages/sub/a.md"}


# ---------------------------------------------------------------------------
# transform_links
# ---------------------------------------------------------------------------


class TestTransformLinks:
    def test_updates_link_to_moved_file(self) -> None:
        content = "See [page](../pages/old.md) for details."
        path_map = {"pages/old.md": "pages/new.md"}
        result = transform_links(content, "modules", "modules", path_map)
        assert "../pages/new.md" in result
        assert "../pages/old.md" not in result

    def test_updates_image_ref_to_moved_asset(self) -> None:
        content = "![fig](../assets/Old/img.png)"
        path_map = {"assets/Old/img.png": "assets/new/img.png"}
        result = transform_links(content, "pages", "pages", path_map)
        assert "../assets/new/img.png" in result

    def test_adjusts_outbound_links_when_file_moves(self) -> None:
        content = "Link to [hw](../assignments/hw1.md)"
        result = transform_links(content, "pages", "pages/sub", {})
        assert "../../assignments/hw1.md" in result

    def test_leaves_external_urls_alone(self) -> None:
        content = "[link](https://example.com/pages/old.md)"
        path_map = {"pages/old.md": "pages/new.md"}
        result = transform_links(content, "pages", "pages", path_map)
        assert result == content

    def test_leaves_anchors_alone(self) -> None:
        content = "[section](#heading)"
        result = transform_links(content, "pages", "pages", {})
        assert result == content

    def test_updates_snippet_ref(self) -> None:
        content = "url is $../snippets/inline/COURSE_ID.md$/modules"
        path_map = {"snippets/inline/COURSE_ID.md": "snippets/inline/COURSE_REF.md"}
        result = transform_links(content, "pages", "pages", path_map)
        assert "$../snippets/inline/COURSE_REF.md$" in result

    def test_preserves_url_encoding(self) -> None:
        content = "![img](../assets/My%20Image.png)"
        path_map = {"assets/My Image.png": "assets/renamed-image.png"}
        result = transform_links(content, "pages", "pages", path_map)
        assert "../assets/renamed-image.png" in result

    def test_handles_link_with_title(self) -> None:
        content = '[Syllabus](../assets/syl.docx "Syllabus File")'
        path_map = {"assets/syl.docx": "assets/syllabus.docx"}
        result = transform_links(content, "pages", "pages", path_map)
        assert "../assets/syllabus.docx" in result
        assert '"Syllabus File"' in result

    def test_handles_angle_bracket_url(self) -> None:
        content = "[Course Syllabus](<../assets/syllabus/file.docx>)"
        path_map = {"assets/syllabus/file.docx": "assets/syllabus/file-v2.docx"}
        result = transform_links(content, "course_settings", "course_settings", path_map)
        assert "<../assets/syllabus/file-v2.docx>" in result
        assert "file.docx>" not in result.replace("file-v2.docx>", "")

    def test_no_change_when_nothing_moved(self) -> None:
        content = "See [page](../pages/foo.md)"
        result = transform_links(content, "modules", "modules", {})
        assert result == content

    def test_does_not_normalize_unrelated_links(self) -> None:
        """Links with ./ prefix or non-canonical paths should not be 'cleaned up'."""
        content = "See [notes](./01-class-notes.md) and [zoom](../pages/course_info/zoom.md)"
        path_map = {"assets/old.png": "assets/new.png"}
        result = transform_links(content, "assignments/sub", "assignments/sub", path_map)
        assert result == content

    def test_both_file_moved_and_target_moved(self) -> None:
        content = "Link to [hw](../assignments/old.md)"
        path_map = {
            "pages/origin.md": "pages/sub/origin.md",
            "assignments/old.md": "assignments/new.md",
        }
        result = transform_links(content, "pages", "pages/sub", path_map)
        assert "../../assignments/new.md" in result

    def test_updates_html_href_link(self) -> None:
        content = '<a href="../assets/syllabus/file.docx">Download</a>'
        path_map = {"assets/syllabus/file.docx": "assets/docs/file.docx"}
        result = transform_links(content, "course_settings", "course_settings", path_map)
        assert "../assets/docs/file.docx" in result
        assert "../assets/syllabus/file.docx" not in result

    def test_updates_html_img_src(self) -> None:
        content = '<img src="../assets/img.png" alt="logo"/>'
        path_map = {"assets/img.png": "assets/images/img.png"}
        result = transform_links(content, "course_settings", "course_settings", path_map)
        assert "../assets/images/img.png" in result
        assert "../assets/img.png" not in result

    def test_leaves_external_html_href_alone(self) -> None:
        content = '<a href="https://example.com">External</a>'
        path_map = {"assets/x.pdf": "assets/y.pdf"}
        result = transform_links(content, "course_settings", "course_settings", path_map)
        assert result == content


# ---------------------------------------------------------------------------
# compute_manifest_updates
# ---------------------------------------------------------------------------


class TestComputeManifestUpdates:
    def test_renames_top_level_key(self) -> None:
        manifest = {
            "pages/old.md": {"canvas_id": 123, "canvas_type": "page", "last_synced": "2025-01-01T00:00:00+00:00"},
        }
        path_map = {"pages/old.md": "pages/new.md"}
        result = compute_manifest_updates(manifest, path_map)
        assert "pages/new.md" in result
        assert "pages/old.md" not in result
        assert result["pages/new.md"]["canvas_id"] == 123

    def test_updates_canvas_item_ids_in_modules(self) -> None:
        manifest = {
            "modules/week1.md": {
                "canvas_id": 999,
                "canvas_type": "module",
                "last_synced": "2025-01-01T00:00:00+00:00",
                "canvas_item_ids": {
                    "pages/old.md": 101,
                    "assignments/hw1.md": 202,
                },
            },
            "pages/old.md": {"canvas_id": 101, "canvas_type": "page", "last_synced": "2025-01-01T00:00:00+00:00"},
        }
        path_map = {"pages/old.md": "pages/new.md"}
        result = compute_manifest_updates(manifest, path_map)
        item_ids = result["modules/week1.md"]["canvas_item_ids"]
        assert "pages/new.md" in item_ids
        assert "pages/old.md" not in item_ids
        assert item_ids["pages/new.md"] == 101
        assert item_ids["assignments/hw1.md"] == 202

    def test_leaves_unrelated_entries_alone(self) -> None:
        manifest = {
            "pages/other.md": {"canvas_id": 456, "canvas_type": "page", "last_synced": "2025-01-01T00:00:00+00:00"},
        }
        path_map = {"pages/old.md": "pages/new.md"}
        result = compute_manifest_updates(manifest, path_map)
        assert result == manifest


# ---------------------------------------------------------------------------
# compute_module_order_updates
# ---------------------------------------------------------------------------


class TestComputeModuleOrderUpdates:
    def test_updates_renamed_module(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        order_path = repo / "course_settings" / "module_order.toml"
        with order_path.open("wb") as f:
            tomli_w.dump({"order": ["intro.md", "old-module.md", "outro.md"]}, f)

        path_map = {"modules/old-module.md": "modules/new-module.md"}
        result = compute_module_order_updates(repo, path_map)
        assert result == ["intro.md", "new-module.md", "outro.md"]

    def test_returns_none_when_no_change(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        order_path = repo / "course_settings" / "module_order.toml"
        with order_path.open("wb") as f:
            tomli_w.dump({"order": ["intro.md", "outro.md"]}, f)

        path_map = {"pages/foo.md": "pages/bar.md"}
        assert compute_module_order_updates(repo, path_map) is None

    def test_returns_none_when_no_file(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        assert compute_module_order_updates(repo, {"modules/a.md": "modules/b.md"}) is None


class TestComputeCourseSettingsUpdates:
    def test_updates_renamed_dashboard_image(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        settings_path = repo / "course_settings" / "course_settings.toml"
        with settings_path.open("wb") as f:
            tomli_w.dump({"title": "X", "dashboard_image": "assets/course_settings/old-logo.png"}, f)

        path_map = {"assets/course_settings/old-logo.png": "assets/course_settings/new-logo.png"}
        assert compute_course_settings_updates(repo, path_map) == {
            "dashboard_image": "assets/course_settings/new-logo.png"
        }

    def test_updates_renamed_front_page(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        settings_path = repo / "course_settings" / "course_settings.toml"
        with settings_path.open("wb") as f:
            tomli_w.dump({"title": "X", "front_page": "pages/old-home.md"}, f)

        path_map = {"pages/old-home.md": "pages/home.md"}
        assert compute_course_settings_updates(repo, path_map) == {"front_page": "pages/home.md"}

    def test_updates_both_fields_at_once(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        settings_path = repo / "course_settings" / "course_settings.toml"
        with settings_path.open("wb") as f:
            tomli_w.dump(
                {
                    "front_page": "pages/Old Home.md",
                    "dashboard_image": "assets/course_settings/Old Logo.png",
                },
                f,
            )

        path_map = {
            "pages/Old Home.md": "pages/old-home.md",
            "assets/course_settings/Old Logo.png": "assets/course_settings/old-logo.png",
        }
        assert compute_course_settings_updates(repo, path_map) == {
            "front_page": "pages/old-home.md",
            "dashboard_image": "assets/course_settings/old-logo.png",
        }

    def test_returns_none_when_no_change(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        settings_path = repo / "course_settings" / "course_settings.toml"
        with settings_path.open("wb") as f:
            tomli_w.dump({"dashboard_image": "assets/course_settings/logo.png"}, f)

        path_map = {"pages/foo.md": "pages/bar.md"}
        assert compute_course_settings_updates(repo, path_map) is None

    def test_returns_none_when_no_path_fields_set(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        settings_path = repo / "course_settings" / "course_settings.toml"
        with settings_path.open("wb") as f:
            tomli_w.dump({"title": "X"}, f)

        assert compute_course_settings_updates(repo, {"a": "b"}) is None


# ---------------------------------------------------------------------------
# compute_file_updates
# ---------------------------------------------------------------------------


class TestComputeFileUpdates:
    def test_finds_link_in_module_file(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/old.md": "# Old Page",
            "modules/week1.md": "---\ntitle: Week 1\n---\n- [Page](../pages/old.md)\n",
        })
        path_map = {"pages/old.md": "pages/new.md"}
        updates = compute_file_updates(repo, path_map)
        assert "modules/week1.md" in updates
        _, new_content = updates["modules/week1.md"]
        assert "../pages/new.md" in new_content
        assert "../pages/old.md" not in new_content

    def test_no_updates_when_nothing_references_moved_file(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/old.md": "# No links here",
            "pages/other.md": "# Also no links",
        })
        path_map = {"pages/old.md": "pages/new.md"}
        updates = compute_file_updates(repo, path_map)
        assert len(updates) == 0

    def test_updates_moved_files_outbound_links(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/moveme.md": "See [hw](../assignments/hw1.md)",
            "assignments/hw1.md": "# HW1",
        })
        path_map = {"pages/moveme.md": "pages/sub/moveme.md"}
        updates = compute_file_updates(repo, path_map)
        assert "pages/sub/moveme.md" in updates
        _, new_content = updates["pages/sub/moveme.md"]
        assert "../../assignments/hw1.md" in new_content

    def test_finds_html_href_in_syllabus(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "course_settings/syllabus.md": '<a href="../assets/syllabus/file.docx">Download</a>',
        })
        path_map = {"assets/syllabus/file.docx": "assets/docs/file.docx"}
        updates = compute_file_updates(repo, path_map)
        assert "course_settings/syllabus.md" in updates
        _, new_content = updates["course_settings/syllabus.md"]
        assert "../assets/docs/file.docx" in new_content
        assert "../assets/syllabus/file.docx" not in new_content

    def test_finds_html_img_in_syllabus(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "course_settings/syllabus.md": '<img src="../assets/banner.png" alt="banner"/>',
        })
        path_map = {"assets/banner.png": "assets/images/banner.png"}
        updates = compute_file_updates(repo, path_map)
        assert "course_settings/syllabus.md" in updates
        _, new_content = updates["course_settings/syllabus.md"]
        assert "../assets/images/banner.png" in new_content
        assert "../assets/banner.png" not in new_content


# ---------------------------------------------------------------------------
# run_mv (integration tests)
# ---------------------------------------------------------------------------


class TestRunMv:
    def test_rename_file_updates_manifest_and_links(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/old-page.md": "# Old Page\n\nContent here.",
            "modules/week1.md": "---\ntitle: Week 1\n---\n- [Old Page](../pages/old-page.md)\n",
        })
        _make_manifest(repo, {
            "pages/old-page.md": {
                "canvas_id": 100,
                "canvas_type": "page",
                "last_synced": "2025-01-01T00:00:00+00:00",
            },
        })

        run_mv(repo / "pages/old-page.md", repo / "pages/new-page.md")

        assert not (repo / "pages/old-page.md").exists()
        assert (repo / "pages/new-page.md").exists()

        manifest_path = repo / ".canvas-manifest.toml"
        with manifest_path.open("rb") as f:
            manifest = tomllib.load(f)
        assert "pages/new-page.md" in manifest
        assert "pages/old-page.md" not in manifest
        assert manifest["pages/new-page.md"]["canvas_id"] == 100

        module_content = (repo / "modules/week1.md").read_text()
        assert "../pages/new-page.md" in module_content
        assert "../pages/old-page.md" not in module_content

    def test_rename_directory_updates_all_refs(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "assets/OldDir/img.png": b"PNG".decode(),
            "pages/page1.md": "![fig](../assets/OldDir/img.png)",
        })
        _make_manifest(repo, {
            "assets/OldDir/img.png": {
                "canvas_id": 200,
                "canvas_type": "file",
                "last_synced": "2025-01-01T00:00:00+00:00",
                "canvas_url": "https://example.com/files/200",
            },
        })

        run_mv(repo / "assets/OldDir", repo / "assets/new-dir")

        assert not (repo / "assets/OldDir").exists()
        assert (repo / "assets/new-dir/img.png").exists()

        with (repo / ".canvas-manifest.toml").open("rb") as f:
            manifest = tomllib.load(f)
        assert "assets/new-dir/img.png" in manifest
        assert "assets/OldDir/img.png" not in manifest

        page_content = (repo / "pages/page1.md").read_text()
        assert "../assets/new-dir/img.png" in page_content

    def test_noop_makes_no_changes(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/a.md": "# A",
            "modules/m.md": "- [A](../pages/a.md)\n",
        })
        _make_manifest(repo, {
            "pages/a.md": {"canvas_id": 1, "canvas_type": "page", "last_synced": "2025-01-01T00:00:00+00:00"},
        })

        run_mv(repo / "pages/a.md", repo / "pages/b.md", noop=True)

        assert (repo / "pages/a.md").exists()
        assert not (repo / "pages/b.md").exists()
        module_content = (repo / "modules/m.md").read_text()
        assert "../pages/a.md" in module_content

    def test_move_to_subdirectory_adjusts_outbound_links(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/moveme.md": "Link to [hw](../assignments/hw1.md)",
            "assignments/hw1.md": "# HW1",
        })
        (repo / "pages" / "sub").mkdir()

        run_mv(repo / "pages/moveme.md", repo / "pages/sub/moveme.md")

        content = (repo / "pages/sub/moveme.md").read_text()
        assert "../../assignments/hw1.md" in content

    def test_updates_module_order_toml(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "modules/old-mod.md": "---\ntitle: Old\n---\n",
        })
        order_path = repo / "course_settings" / "module_order.toml"
        with order_path.open("wb") as f:
            tomli_w.dump({"order": ["intro.md", "old-mod.md", "outro.md"]}, f)

        run_mv(repo / "modules/old-mod.md", repo / "modules/new-mod.md")

        with order_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["order"] == ["intro.md", "new-mod.md", "outro.md"]

    def test_quiz_folder_rename(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "quizzes/old-quiz/old-quiz.md": "---\ntitle: Quiz\n---\n",
            "quizzes/old-quiz/questions/q1.md": "---\ntitle: Q1\n---\nQ1 body",
        })
        _make_manifest(repo, {
            "quizzes/old-quiz/old-quiz.md": {
                "canvas_id": 300,
                "canvas_type": "quiz",
                "last_synced": "2025-01-01T00:00:00+00:00",
            },
        })

        run_mv(repo / "quizzes/old-quiz", repo / "quizzes/new-quiz")

        assert (repo / "quizzes/new-quiz/new-quiz.md").exists()
        assert not (repo / "quizzes/new-quiz/old-quiz.md").exists()
        assert (repo / "quizzes/new-quiz/questions/q1.md").exists()

        with (repo / ".canvas-manifest.toml").open("rb") as f:
            manifest = tomllib.load(f)
        assert "quizzes/new-quiz/new-quiz.md" in manifest

    def test_updates_canvas_item_ids_in_module_manifest(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/old.md": "# Old",
        })
        _make_manifest(repo, {
            "pages/old.md": {
                "canvas_id": 100,
                "canvas_type": "page",
                "last_synced": "2025-01-01T00:00:00+00:00",
            },
            "modules/week1.md": {
                "canvas_id": 999,
                "canvas_type": "module",
                "last_synced": "2025-01-01T00:00:00+00:00",
                "canvas_item_ids": {
                    "pages/old.md": 50001,
                    "assignments/hw.md": 50002,
                },
            },
        })

        run_mv(repo / "pages/old.md", repo / "pages/new.md")

        with (repo / ".canvas-manifest.toml").open("rb") as f:
            manifest = tomllib.load(f)
        item_ids = manifest["modules/week1.md"]["canvas_item_ids"]
        assert "pages/new.md" in item_ids
        assert "pages/old.md" not in item_ids
        assert item_ids["pages/new.md"] == 50001

    def test_rejects_cross_type(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/a.md": "# A"})
        (repo / "assignments").mkdir()
        with pytest.raises(ValueError, match="Cannot move across content types"):
            run_mv(repo / "pages/a.md", repo / "assignments/a.md")

    def test_snippet_ref_updated(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "snippets/inline/OLD.md": "https://example.com",
            "pages/page.md": "Go to [Modules]($../snippets/inline/OLD.md$/modules)\n",
        })

        run_mv(
            repo / "snippets/inline/OLD.md",
            repo / "snippets/inline/NEW.md",
        )

        content = (repo / "pages/page.md").read_text()
        assert "$../snippets/inline/NEW.md$" in content
        assert "$../snippets/inline/OLD.md$" not in content

    def test_directory_rename_only_leaf(self, tmp_path: Path) -> None:
        """Only the leaf directory is renamed — parent must already exist."""
        repo = _make_repo(tmp_path, {
            "assets/Lecture-Related/Unit-01/file.png": "img",
            "pages/p.md": "![](../assets/Lecture-Related/Unit-01/file.png)",
        })

        run_mv(
            repo / "assets/Lecture-Related/Unit-01",
            repo / "assets/Lecture-Related/unit-01",
        )

        assert (repo / "assets/Lecture-Related/unit-01/file.png").exists()
        content = (repo / "pages/p.md").read_text()
        assert "../assets/Lecture-Related/unit-01/file.png" in content

    def test_rejects_multi_component_rename(self, tmp_path: Path) -> None:
        """Cannot rename multiple path components at once (dest parent must exist)."""
        repo = _make_repo(tmp_path, {
            "assets/Lecture-Related/Unit-01/file.png": "img",
        })

        with pytest.raises(ValueError, match="Destination parent directory does not exist"):
            run_mv(
                repo / "assets/Lecture-Related/Unit-01",
                repo / "assets/lecture-related/unit-01",
            )

    def test_url_encoded_spaces_matched(self, tmp_path: Path) -> None:
        """Links with %20-encoded spaces match files with actual spaces."""
        repo = _make_repo(tmp_path, {
            "assets/My Image.png": "img-data",
            "pages/page.md": "![photo](../assets/My%20Image.png)",
        })
        _make_manifest(repo, {
            "assets/My Image.png": {
                "canvas_id": 500,
                "canvas_type": "file",
                "last_synced": "2025-01-01T00:00:00+00:00",
            },
        })

        run_mv(repo / "assets/My Image.png", repo / "assets/my-image.png")

        content = (repo / "pages/page.md").read_text()
        assert "../assets/my-image.png" in content
        assert "My%20Image" not in content

        with (repo / ".canvas-manifest.toml").open("rb") as f:
            manifest = tomllib.load(f)
        assert "assets/my-image.png" in manifest
        assert "assets/My Image.png" not in manifest

    def test_moving_dashboard_image_updates_course_settings(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "assets/course_settings/IT-CS_115_dashboard_logo.png": "img-data",
        })
        settings_path = repo / "course_settings" / "course_settings.toml"
        with settings_path.open("wb") as f:
            tomli_w.dump(
                {"dashboard_image": "assets/course_settings/IT-CS_115_dashboard_logo.png"}, f
            )

        run_mv(
            repo / "assets/course_settings/IT-CS_115_dashboard_logo.png",
            repo / "assets/course_settings/it-cs_115_dashboard_logo.png",
        )

        with settings_path.open("rb") as f:
            settings = tomllib.load(f)
        assert settings["dashboard_image"] == "assets/course_settings/it-cs_115_dashboard_logo.png"

    def test_moving_front_page_updates_course_settings(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "pages/Old Home.md": "# Home",
        })
        settings_path = repo / "course_settings" / "course_settings.toml"
        with settings_path.open("wb") as f:
            tomli_w.dump({"title": "Test", "front_page": "pages/Old Home.md"}, f)

        run_mv(repo / "pages/Old Home.md", repo / "pages/old-home.md")

        with settings_path.open("rb") as f:
            settings = tomllib.load(f)
        assert settings["front_page"] == "pages/old-home.md"
        assert settings["title"] == "Test"

    def test_moving_asset_updates_syllabus_html_link(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "assets/syllabus/file.docx": "content",
            "course_settings/syllabus.md": '<a href="../assets/syllabus/file.docx">Download</a>',
            "assets/docs/.gitkeep": "",
        })

        run_mv(repo / "assets/syllabus/file.docx", repo / "assets/docs/file.docx")

        syllabus_content = (repo / "course_settings/syllabus.md").read_text()
        assert "../assets/docs/file.docx" in syllabus_content
        assert "../assets/syllabus/file.docx" not in syllabus_content


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)


class TestRunMvInGitRepo:
    """git mv refuses to touch untracked content, so run_mv must fall back
    to a plain filesystem move instead of failing when files aren't yet
    added to the repo (or aren't yet in the manifest)."""

    def test_moves_untracked_file_in_git_repo(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/old-page.md": "# Old Page"})
        _git_init(repo)

        run_mv(repo / "pages/old-page.md", repo / "pages/new-page.md")

        assert not (repo / "pages/old-page.md").exists()
        assert (repo / "pages/new-page.md").exists()

    def test_moves_untracked_directory_in_git_repo(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"assets/Unit 03/worksheet.docx": "data"})
        _git_init(repo)

        run_mv(repo / "assets/Unit 03", repo / "assets/unit-03")

        assert not (repo / "assets/Unit 03").exists()
        assert (repo / "assets/unit-03/worksheet.docx").exists()

    def test_moves_tracked_file_via_git_mv(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {"pages/old-page.md": "# Old Page"})
        _git_init(repo)
        subprocess.run(["git", "add", "pages/old-page.md"], cwd=str(repo), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)

        run_mv(repo / "pages/old-page.md", repo / "pages/new-page.md")

        assert not (repo / "pages/old-page.md").exists()
        assert (repo / "pages/new-page.md").exists()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True, check=True
        ).stdout
        assert "R  pages/old-page.md -> pages/new-page.md" in status
