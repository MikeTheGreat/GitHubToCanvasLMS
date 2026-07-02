"""Unit tests: imsmanifest.xml parsing → temp manifest."""
from __future__ import annotations

from pathlib import Path


from github_to_canvas.imscc_import import parse_imsmanifest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "imscc"


# ---------------------------------------------------------------------------
# Classification by resource type
# ---------------------------------------------------------------------------


def test_page_resource_classified_as_page() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    entry = manifest["g_page_1"]
    assert entry.category == "page"


def test_page_local_path_uses_stem() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_page_1"].local_path == "pages/my-page.md"


def test_page_title_extracted_from_html() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_page_1"].title == "My Page"


def test_assignment_resource_classified_as_assignment() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_assignment_1"].category == "assignment"


def test_assignment_local_path_uses_html_stem() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_assignment_1"].local_path == "assignments/my-assignment.md"


def test_assignment_title_from_settings_xml() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_assignment_1"].title == "My Test Assignment"


def test_discussion_resource_classified_as_discussion() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_discussion_1"].category == "discussion"


def test_discussion_local_path_slugified_from_title() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_discussion_1"].local_path == "discussions/week-01-forum.md"


def test_discussion_title_from_topic_meta() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_discussion_1"].title == "Week 01 Forum"


def test_discussion_meta_path_stored_in_metadata() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_discussion_1"].metadata["meta_path"] == "g_discussion_1_meta.xml"


def test_discussion_meta_not_a_top_level_entry() -> None:
    """topicMeta is a dependency — it should not appear as its own top-level entry."""
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert "g_discussion_1_meta" not in manifest


def test_asset_resource_classified_as_asset() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_asset_1"].category == "asset"


def test_asset_local_path_preserves_subdir() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_asset_1"].local_path == "assets/images/logo.png"


def test_quiz_resource_classified_as_quiz() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_quiz_1"].category == "quiz"


def test_external_url_resource_classified() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_exturl_1"].category == "external_url"


def test_external_url_url_stored_in_metadata() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_exturl_1"].metadata["url"] == "https://example.com/resource"


def test_syllabus_resource_classified_as_syllabus() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_course_1_syllabus"].category == "syllabus"


def test_syllabus_local_path() -> None:
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_course_1_syllabus"].local_path == "course_settings/syllabus.md"


# ---------------------------------------------------------------------------
# Slugification
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    from github_to_canvas.imscc_import import _slugify
    assert _slugify("Week 06 Forum") == "week-06-forum"


def test_slugify_special_chars() -> None:
    from github_to_canvas.imscc_import import _slugify
    assert _slugify("Assignment 1: Foo Bar!") == "assignment-1-foo-bar"


def test_slugify_multiple_spaces() -> None:
    from github_to_canvas.imscc_import import _slugify
    assert _slugify("Hello   World") == "hello-world"


def test_slugify_ampersand_stripped() -> None:
    from github_to_canvas.imscc_import import _slugify
    result = _slugify("Week 1 & 2")
    assert "&" not in result
    assert "week" in result
