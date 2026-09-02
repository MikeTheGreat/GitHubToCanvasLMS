"""In-memory stand-in for canvas_api, used by `update --check-all`.

DryRunCanvas implements the same public functions (names, signatures, return
shapes) as canvas_api that the run_sync pipeline calls, so the whole pipeline
runs unchanged against it — every local validation fires, nothing touches the
network. It is lightly stateful (fake-id counter, assignment groups, rubrics)
so that name resolution behaves exactly like a real first sync to an empty
course: settings sync "creates" the groups/rubrics, later phases resolve
content references against them.

Keep this in sync with canvas_api: when the sync pipeline starts calling a new
canvas_api function, add its dry-run twin here (test_check_all's full-fixture
run catches a missing one as an AttributeError).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .canvas_api import _CANVAS_ITEM_TYPE, GradingStandardSync


def _fake_html_url(canvas_type: str, canvas_id: int) -> str:
    return f"(check-all: {canvas_type} not uploaded, fake id {canvas_id})"


class _AnyModuleName(dict):
    """Name index in which every name resolves to exactly one fake module id.

    module_order.toml may list modules that exist only on Canvas; the simulated
    course is empty, so looking them up for real would report every one of them
    as missing even though the actual course has them.
    """

    def __init__(self, new_id) -> None:
        super().__init__()
        self._new_id = new_id

    def get(self, name, default=None):  # noqa: D102 - dict.get override
        if name not in self:
            self[name] = [self._new_id()]
        return self[name]


def _page_slug(title: str) -> str:
    """Approximate Canvas's title→URL slugification (lowercase, dashes)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "page"


class DryRunCourse:
    """Placeholder for the canvasapi Course object during --check-all.

    Only the Course methods sync.py invokes directly are implemented; all
    other Canvas traffic goes through DryRunCanvas.
    """

    name = "(check-all: not contacting Canvas)"
    id = 0

    def get_quiz(self, quiz_id: int):
        # Consumed only by DryRunCanvas.sync_quiz_questions /
        # finalize_quiz_publish_state, which ignore it.
        return SimpleNamespace(id=quiz_id, published=False)


class DryRunCanvas:
    def __init__(self) -> None:
        self._next_id = 100000
        self._assignment_groups: dict[str, int] = {}
        self._rubrics: dict[str, int] = {}

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    # -- reads ---------------------------------------------------------------

    def get_canvas_updated_at(self, course, canvas_type, identifier):
        return None  # local always wins; moot anyway with the empty manifest

    def get_assignment_group_ids(self, course) -> dict[str, int]:
        return dict(self._assignment_groups)

    def get_rubric_ids(self, course) -> dict[str, int]:
        return dict(self._rubrics)

    # -- course settings -----------------------------------------------------

    def sync_grading_standards(self, course, standards):
        # No live course to compare schemes against, so never a mismatch here.
        return GradingStandardSync(self._new_id() if standards else None, [])

    def update_course_metadata(self, course, settings, grading_standard_id=None) -> None:
        pass

    def upload_course_image(self, course, local_path: Path) -> int:
        return self._new_id()

    def sync_assignment_groups(self, course, groups) -> list[str]:
        for group in groups:
            name = group.get("title", "")
            self._assignment_groups.setdefault(name, self._new_id())
        # No drop rules to defer: apply_assignment_group_rules is a no-op here.
        return []

    def apply_assignment_group_rules(self, course, groups, names=None) -> None:
        pass

    def update_late_policy(self, course, late_policy) -> None:
        pass

    def update_post_policy(self, course, post_manually) -> None:
        pass

    def sync_tab_configuration(self, course, tab_config) -> None:
        # Tab ids/labels can only be validated against a live course.
        pass

    def sync_rubrics(
        self, course, rubrics
    ) -> tuple[dict[str, int], list[str], list[str], list[tuple[str, str]]]:
        created: list[str] = []
        for r in rubrics:
            title = r.get("title", "")
            if title not in self._rubrics:
                self._rubrics[title] = self._new_id()
                created.append(title)
        return dict(self._rubrics), created, [], []

    def associate_rubric_with_assignment(
        self, course, rubric_id, assignment_id, use_for_grading=True
    ) -> None:
        pass

    def remove_rubric_from_assignment(self, course, assignment_id, rubric_id) -> bool:
        return False

    def update_syllabus_body(self, course, html: str) -> None:
        pass

    # -- content -------------------------------------------------------------

    def upload_asset(self, course, local_path: Path, assets_root: Path) -> dict[str, Any]:
        canvas_id = self._new_id()
        return {
            "canvas_type": "file",
            "canvas_id": canvas_id,
            "canvas_url": f"/files/{canvas_id}/download",
        }

    def create_or_update_page(
        self, course, canvas_url, title, body, **kwargs
    ) -> dict[str, Any]:
        canvas_id = self._new_id()
        return {
            "canvas_type": "page",
            "canvas_id": canvas_id,
            "canvas_url": canvas_url or _page_slug(title),
            "html_url": _fake_html_url("page", canvas_id),
        }

    def create_or_update_assignment(
        self, course, canvas_id, title, body, **kwargs
    ) -> dict[str, Any]:
        new_id = canvas_id or self._new_id()
        return {
            "canvas_type": "assignment",
            "canvas_id": new_id,
            "html_url": _fake_html_url("assignment", new_id),
            "rubric_settings": None,
        }

    def create_or_update_discussion(
        self, course, canvas_id, title, body, **kwargs
    ) -> dict[str, Any]:
        new_id = canvas_id or self._new_id()
        return {
            "canvas_type": "discussion",
            "canvas_id": new_id,
            "html_url": _fake_html_url("discussion", new_id),
        }

    def create_or_update_announcement(
        self, course, canvas_id, title, body, **kwargs
    ) -> dict[str, Any]:
        new_id = canvas_id or self._new_id()
        return {
            "canvas_type": "announcement",
            "canvas_id": new_id,
            "html_url": _fake_html_url("announcement", new_id),
        }

    def create_stub(self, course, canvas_type: str, title: str) -> dict[str, Any]:
        canvas_id = self._new_id()
        if canvas_type == "page":
            return {
                "canvas_type": "page",
                "canvas_id": canvas_id,
                "canvas_url": _page_slug(title),
            }
        if canvas_type in ("assignment", "discussion", "announcement"):
            return {"canvas_type": canvas_type, "canvas_id": canvas_id}
        # Same behavior as the real create_stub, so check-all reports exactly
        # what a real fresh sync would die on.
        raise ValueError(f"Cannot create stub for canvas_type: {canvas_type!r}")

    def set_front_page(self, course, page_url: str) -> None:
        pass

    def update_dates(self, course, canvas_type, canvas_id, date_fields) -> dict[str, Any]:
        return {"html_url": _fake_html_url(canvas_type, canvas_id)}

    # -- quizzes / question banks ---------------------------------------------

    def create_or_update_quiz(
        self, course, canvas_id, title, description, **kwargs
    ) -> dict[str, Any]:
        new_id = canvas_id or self._new_id()
        return {
            "canvas_type": "quiz",
            "canvas_id": new_id,
            "html_url": _fake_html_url("quiz", new_id),
        }

    def sync_quiz_questions(self, course, quiz, questions) -> dict[str, int]:
        return {q["rel_path"]: self._new_id() for q in questions}

    def finalize_quiz_publish_state(self, quiz, published: bool) -> bool:
        return False  # never "already published" on an empty course

    def sync_question_bank(self, course, bank_title, questions) -> int:
        return self._new_id()

    # -- modules ---------------------------------------------------------------

    def create_or_update_module(self, course, canvas_id, title, **kwargs):
        return SimpleNamespace(id=canvas_id or self._new_id())

    def get_module_ids_by_name(self, course) -> dict[str, list[int]]:
        """Every name resolves, so check-all does not report a Canvas-only
        module listed in module_order.toml as missing — the simulated course is
        empty, but the real one is where those modules actually live."""
        return _AnyModuleName(self._new_id)

    def reposition_module(self, course, canvas_id: int, position: int) -> None:
        pass

    def clear_module_items(self, module) -> None:
        pass

    def add_module_item(
        self, module, item: dict[str, Any], manifest: dict
    ) -> tuple[int | None, str | None]:
        """Mirror the real add_module_item's *local* validation (manifest
        lookup, supported-type check, warnings) without the API calls."""
        if item["type"] in ("SubHeader", "ExternalUrl"):
            return self._new_id(), None
        local_path = item["local_path"]
        if local_path not in manifest:
            print(f"  WARNING: module item not in manifest (skipping): {local_path}")
            return None, None
        entry = manifest[local_path]
        if _CANVAS_ITEM_TYPE.get(entry.get("canvas_type", "")) is None:
            print(f"  WARNING: unsupported canvas_type for module item (skipping): {local_path}")
            return None, None
        return self._new_id(), None
