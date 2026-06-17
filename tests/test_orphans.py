"""Unit tests for orphan detection logic."""
from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from github_to_canvas.orphans import ResourceKey, extract_canvas_refs, find_orphans


# ---------------------------------------------------------------------------
# extract_canvas_refs
# ---------------------------------------------------------------------------


class TestExtractCanvasRefs:
    def test_page_link(self):
        html = '<a href="/courses/123/pages/my-page">link</a>'
        assert extract_canvas_refs(html) == {ResourceKey("page", "my-page")}

    def test_assignment_link(self):
        html = '<a href="/courses/1/assignments/456">hw</a>'
        assert extract_canvas_refs(html) == {ResourceKey("assignment", 456)}

    def test_discussion_link(self):
        html = '<a href="/courses/1/discussion_topics/789">disc</a>'
        assert extract_canvas_refs(html) == {ResourceKey("discussion", 789)}

    def test_quiz_link(self):
        html = '<a href="/courses/1/quizzes/101">quiz</a>'
        assert extract_canvas_refs(html) == {ResourceKey("quiz", 101)}

    def test_file_link(self):
        html = '<img src="/courses/1/files/55/preview">'
        assert extract_canvas_refs(html) == {ResourceKey("file", 55)}

    def test_module_link(self):
        html = '<a href="/courses/1/modules/22">mod</a>'
        assert extract_canvas_refs(html) == {ResourceKey("module", 22)}

    def test_absolute_url(self):
        html = '<a href="https://school.instructure.com/courses/1/pages/foo">x</a>'
        assert extract_canvas_refs(html) == {ResourceKey("page", "foo")}

    def test_url_encoded_slug(self):
        html = '<a href="/courses/1/pages/caf%C3%A9-menu">x</a>'
        assert extract_canvas_refs(html) == {ResourceKey("page", "café-menu")}

    def test_multiple_refs(self):
        html = (
            '<a href="/courses/1/pages/a">x</a>'
            '<a href="/courses/1/assignments/2">y</a>'
            '<a href="/courses/1/quizzes/3">z</a>'
        )
        refs = extract_canvas_refs(html)
        assert refs == {
            ResourceKey("page", "a"),
            ResourceKey("assignment", 2),
            ResourceKey("quiz", 3),
        }

    def test_empty_html(self):
        assert extract_canvas_refs("") == set()
        assert extract_canvas_refs(None) == set()

    def test_external_links_ignored(self):
        html = '<a href="https://example.com/pages/foo">ext</a>'
        assert extract_canvas_refs(html) == set()

    def test_anchor_only_ignored(self):
        html = '<a href="#section">anchor</a>'
        assert extract_canvas_refs(html) == set()

    def test_query_string_stripped(self):
        html = '<a href="/courses/1/pages/slug?foo=bar">x</a>'
        assert extract_canvas_refs(html) == {ResourceKey("page", "slug")}


# ---------------------------------------------------------------------------
# find_orphans — integration with mocked canvasapi course
# ---------------------------------------------------------------------------


def _mock_page(url, title, body="", published=True, html_url=""):
    p = MagicMock()
    p.url = url
    p.title = title
    p.body = body
    p.published = published
    p.html_url = html_url or f"/courses/1/pages/{url}"
    return p


def _mock_assignment(aid, name, description="", published=True):
    a = MagicMock()
    a.id = aid
    a.name = name
    a.description = description
    a.published = published
    a.html_url = f"/courses/1/assignments/{aid}"
    return a


def _mock_quiz(qid, title, description="", published=True):
    q = MagicMock()
    q.id = qid
    q.title = title
    q.description = description
    q.published = published
    q.html_url = f"/courses/1/quizzes/{qid}"
    q.get_questions.return_value = []
    return q


def _mock_discussion(did, title, message="", published=True):
    d = MagicMock()
    d.id = did
    d.title = title
    d.message = message
    d.published = published
    d.html_url = f"/courses/1/discussion_topics/{did}"
    return d


def _mock_module(mid, name, items=None):
    m = MagicMock()
    m.id = mid
    m.name = name
    m.get_module_items.return_value = items or []
    return m


def _mock_module_item(item_type, page_url=None, content_id=None):
    mi = MagicMock()
    mi.type = item_type
    mi.page_url = page_url
    mi.content_id = content_id
    return mi


def _base_course(pages=None, assignments=None, discussions=None, quizzes=None,
                 modules=None, front_page=None, syllabus_body=""):
    course = MagicMock()
    course.id = 1
    course.get_pages.return_value = pages or []
    course.get_assignments.return_value = assignments or []
    course.get_discussion_topics.return_value = discussions or []
    course.get_quizzes.return_value = quizzes or []
    course.get_modules.return_value = modules or []

    if front_page:
        course.show_front_page.return_value = front_page
    else:
        course.show_front_page.side_effect = Exception("no front page")

    resp = MagicMock()
    resp.json.return_value = {"syllabus_body": syllabus_body}
    course._requester.request.return_value = resp

    return course


class TestFindOrphans:
    def test_empty_course(self):
        course = _base_course()
        assert find_orphans(course) == []

    def test_all_pages_in_module(self):
        """Pages referenced by a module should not be orphaned."""
        pages = [_mock_page("p1", "Page 1"), _mock_page("p2", "Page 2")]
        items = [
            _mock_module_item("Page", page_url="p1"),
            _mock_module_item("Page", page_url="p2"),
        ]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(pages=pages, modules=modules)
        assert find_orphans(course) == []

    def test_orphaned_page(self):
        """A page not in any module and not linked from anywhere is orphaned."""
        pages = [
            _mock_page("used", "Used Page"),
            _mock_page("orphan", "Orphan Page"),
        ]
        items = [_mock_module_item("Page", page_url="used")]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(pages=pages, modules=modules)
        orphans = find_orphans(course)
        assert len(orphans) == 1
        assert orphans[0].title == "Orphan Page"

    def test_front_page_not_orphaned(self):
        """The course front page should never be reported as orphaned."""
        fp = _mock_page("home", "Home Page")
        pages = [fp, _mock_page("other", "Other")]
        items = [_mock_module_item("Page", page_url="other")]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(pages=pages, modules=modules, front_page=fp)
        orphans = find_orphans(course)
        assert all(o.title != "Home Page" for o in orphans)

    def test_html_link_counts_as_reference(self):
        """A page linked from another page's body is not orphaned."""
        pages = [
            _mock_page("linker", "Linker", body='<a href="/courses/1/pages/target">go</a>'),
            _mock_page("target", "Target"),
        ]
        items = [_mock_module_item("Page", page_url="linker")]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(pages=pages, modules=modules)
        orphans = find_orphans(course)
        assert all(o.title != "Target" for o in orphans)

    def test_assignment_in_module_not_orphaned(self):
        assignments = [_mock_assignment(10, "HW1")]
        items = [_mock_module_item("Assignment", content_id=10)]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(assignments=assignments, modules=modules)
        assert find_orphans(course) == []

    def test_orphaned_assignment(self):
        assignments = [_mock_assignment(10, "HW1"), _mock_assignment(20, "HW2")]
        items = [_mock_module_item("Assignment", content_id=10)]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(assignments=assignments, modules=modules)
        orphans = find_orphans(course)
        assert len(orphans) == 1
        assert orphans[0].title == "HW2"

    def test_quiz_linked_from_page(self):
        pages = [_mock_page("p", "P", body='<a href="/courses/1/quizzes/5">quiz</a>')]
        quizzes = [_mock_quiz(5, "Quiz")]
        items = [_mock_module_item("Page", page_url="p")]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(pages=pages, quizzes=quizzes, modules=modules)
        assert find_orphans(course) == []

    def test_syllabus_link_counts_as_reference(self):
        pages = [_mock_page("syllabus-target", "Syllabus Target")]
        course = _base_course(
            pages=pages,
            syllabus_body='<a href="/courses/1/pages/syllabus-target">go</a>',
        )
        assert find_orphans(course) == []

    def test_sorted_by_type_then_title(self):
        pages = [_mock_page("z", "Zebra"), _mock_page("a", "Apple")]
        assignments = [_mock_assignment(1, "Beta")]
        course = _base_course(pages=pages, assignments=assignments)
        orphans = find_orphans(course)
        types_and_titles = [(o.key.canvas_type, o.title) for o in orphans]
        assert types_and_titles == [
            ("assignment", "Beta"),
            ("page", "Apple"),
            ("page", "Zebra"),
        ]

    def test_discussion_in_module(self):
        discussions = [_mock_discussion(7, "Forum")]
        items = [_mock_module_item("Discussion", content_id=7)]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(discussions=discussions, modules=modules)
        assert find_orphans(course) == []

    def test_quiz_question_link_counts(self):
        """Links inside quiz question text should count as references."""
        question = MagicMock()
        question.question_text = '<a href="/courses/1/pages/ref-page">see</a>'
        question.answers = []
        q = _mock_quiz(1, "Q")
        q.get_questions.return_value = [question]
        pages = [_mock_page("ref-page", "Ref Page")]
        items = [_mock_module_item("Quiz", content_id=1)]
        modules = [_mock_module(1, "Mod", items)]
        course = _base_course(pages=pages, quizzes=[q], modules=modules)
        assert find_orphans(course) == []
