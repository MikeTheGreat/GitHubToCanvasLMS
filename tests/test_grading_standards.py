"""Grading-standard value scaling on upload.

Canvas returns grading-scheme values as fractions (0..1) but the create endpoint
expects the lower bound on a 0..100 scale. course_settings.toml stores fractions
(that is what ``import`` writes), so sync has to scale on the way out.
"""

from types import SimpleNamespace

from markdown_to_canvas.canvas_api import (
    _COURSE_METADATA_SKIP,
    _grading_scheme_entries,
    _scheme_differences,
    sync_grading_standards,
    update_course_metadata,
)
from markdown_to_canvas.imscc_import import _parse_course_settings_full


def test_fractions_are_scaled_to_percentages():
    data = [["A (4.0)", 0.95], ["C- (1.5)", 0.7], ["F (0.0)", 0.0]]
    assert _grading_scheme_entries(data) == [
        {"name": "A (4.0)", "value": 95.0},
        {"name": "C- (1.5)", "value": 70.0},
        {"name": "F (0.0)", "value": 0.0},
    ]


def test_values_already_on_0_to_100_scale_pass_through():
    data = [["A", 94], ["B", 84], ["F", 0]]
    assert _grading_scheme_entries(data) == [
        {"name": "A", "value": 94},
        {"name": "B", "value": 84},
        {"name": "F", "value": 0},
    ]


def test_top_band_of_exactly_one_is_still_a_fraction():
    data = [["Perfect", 1.0], ["Not", 0.5]]
    assert _grading_scheme_entries(data) == [
        {"name": "Perfect", "value": 100.0},
        {"name": "Not", "value": 50.0},
    ]


def test_rows_that_are_not_name_value_pairs_are_dropped():
    data = [["A", 0.9], ["oops"], ["B", 0.8, "extra"]]
    assert _grading_scheme_entries(data) == [{"name": "A", "value": 90.0}]


def test_empty_data_yields_no_entries():
    assert _grading_scheme_entries([]) == []


# ---------------------------------------------------------------------------
# Scheme verification against a live standard
#
# A title match is not enough to reuse a standard: an account-level "ELP Grade
# Scale" that grades differently from the one in course_settings.toml would
# silently change every student's letter grade. _scheme_differences is what
# stops that, so it has to catch every way the two can disagree.
# ---------------------------------------------------------------------------

def _canvas_gs(rows, **extra):
    """A Canvas grading-standard payload; Canvas reports values as fractions."""
    return {
        "id": 569017,
        "title": "Scheme",
        "context_type": "Account",
        "context_id": 102875,
        "grading_scheme": [
            {"name": n, "value": v, "calculated_value": v * 100} for n, v in rows
        ],
        **extra,
    }


def test_identical_scheme_has_no_differences():
    std = {"title": "Scheme", "data": [["A", 0.95], ["B", 0.85], ["F", 0.0]]}
    gs = _canvas_gs([("A", 0.95), ("B", 0.85), ("F", 0.0)])
    assert _scheme_differences(std, gs) == []


def test_toml_written_on_0_to_100_scale_still_matches_canvas_fractions():
    # A hand-edited file may use percentages; that is a notation difference,
    # not a grading difference, and must not trip the error.
    std = {"title": "Scheme", "data": [["A", 95], ["B", 85], ["F", 0]]}
    gs = _canvas_gs([("A", 0.95), ("B", 0.85), ("F", 0.0)])
    assert _scheme_differences(std, gs) == []


def test_changed_cutoff_is_reported_with_both_values():
    std = {"title": "Scheme", "data": [["A", 0.93], ["F", 0.0]]}
    gs = _canvas_gs([("A", 0.95), ("F", 0.0)])
    diffs = _scheme_differences(std, gs)
    assert len(diffs) == 1
    assert "93%" in diffs[0] and "95%" in diffs[0]


def test_renamed_band_is_reported():
    std = {"title": "Scheme", "data": [["A (4.0)", 0.95]]}
    gs = _canvas_gs([("A", 0.95)])
    assert any("name" in d for d in _scheme_differences(std, gs))


def test_extra_row_in_toml_is_reported():
    std = {"title": "Scheme", "data": [["A", 0.95], ["B", 0.85]]}
    gs = _canvas_gs([("A", 0.95)])
    diffs = _scheme_differences(std, gs)
    assert any("row count" in d for d in diffs)
    assert any("in .toml but not on Canvas" in d for d in diffs)


def test_extra_row_on_canvas_is_reported():
    std = {"title": "Scheme", "data": [["A", 0.95]]}
    gs = _canvas_gs([("A", 0.95), ("B", 0.85)])
    diffs = _scheme_differences(std, gs)
    assert any("on Canvas but not in .toml" in d for d in diffs)


def test_points_based_mismatch_is_reported():
    std = {"title": "Scheme", "data": [["A", 0.95]], "points_based": True}
    gs = _canvas_gs([("A", 0.95)], points_based=False)
    assert any("points_based" in d for d in _scheme_differences(std, gs))


def test_unset_optional_fields_are_not_differences():
    std = {"title": "Scheme", "data": [["A", 0.95]]}
    gs = _canvas_gs([("A", 0.95)], points_based=True, scaling_factor=1.0)
    assert _scheme_differences(std, gs) == []


def test_float_rounding_does_not_register_as_a_difference():
    std = {"title": "Scheme", "data": [["A", 0.1 + 0.2]]}
    gs = _canvas_gs([("A", 0.3)])
    assert _scheme_differences(std, gs) == []


# ---------------------------------------------------------------------------
# sync_grading_standards: reuse, notice, and the refusal to change on mismatch
# ---------------------------------------------------------------------------

class _FakeRequester:
    def __init__(self, routes):
        self.routes = routes

    def request(self, method, path, **kwargs):
        if path not in self.routes:
            raise RuntimeError(f"404 {path}")
        return SimpleNamespace(json=lambda p=path: self.routes[p])


class _FakeCourse:
    def __init__(self, routes, account_id=102875):
        self.id = 1
        self.account_id = account_id
        self._requester = _FakeRequester(routes)
        self.created = []

    def add_grading_standards(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=999)


ELP = [["A", 0.95], ["F", 0.0]]


def _routes(course_owned=(), account_owned=(), parent_id=None):
    return {
        "courses/1/grading_standards": list(course_owned),
        "accounts/102875/grading_standards": list(account_owned),
        "accounts/102875": {"parent_account_id": parent_id},
    }


def test_account_level_standard_is_reused_not_cloned(capsys):
    """The bug this whole change exists for: Canvas's course-context endpoint
    hides account-level standards, so the tool used to clone one into every
    course instead of pointing at the institution's own scheme."""
    gs = _canvas_gs([("A", 0.95), ("F", 0.0)], id=569017, title="ELP Grade Scale")
    course = _FakeCourse(_routes(account_owned=[gs]))
    result = sync_grading_standards(course, [{"title": "ELP Grade Scale", "data": ELP}])
    assert result.standard_id == 569017
    assert result.mismatches == []
    assert course.created == []  # nothing cloned
    assert "account-level standard 569017" in capsys.readouterr().out


def test_no_match_anywhere_creates_the_standard():
    course = _FakeCourse(_routes())
    result = sync_grading_standards(course, [{"title": "Novel", "data": ELP}])
    assert result.standard_id == 999
    assert len(course.created) == 1
    # Fractions are scaled on the way out; see the top of this file.
    assert course.created[0]["grading_scheme_entry"][0]["value"] == 95.0


def test_course_owned_standard_wins_over_same_titled_account_one():
    course_gs = _canvas_gs([("A", 0.95), ("F", 0.0)], id=111, context_type="Course")
    acct_gs = _canvas_gs([("A", 0.95), ("F", 0.0)], id=222)
    course = _FakeCourse(_routes(course_owned=[course_gs], account_owned=[acct_gs]))
    result = sync_grading_standards(course, [{"title": "Scheme", "data": ELP}])
    assert result.standard_id == 111


def test_mismatch_reports_and_leaves_the_course_standard_alone():
    gs = _canvas_gs([("A", 0.90), ("F", 0.0)])  # Canvas grades A at 90, .toml at 95
    course = _FakeCourse(_routes(account_owned=[gs]))
    result = sync_grading_standards(course, [{"title": "Scheme", "data": ELP}])
    assert result.standard_id is None  # course is NOT repointed
    assert course.created == []  # nor is a conflicting clone made
    assert len(result.mismatches) == 1
    assert "NOT changed" in result.mismatches[0]


def test_one_mismatch_suppresses_an_otherwise_good_standard():
    """A mismatch anywhere means no grading-standard change at all: swapping the
    course onto a *different* standard than the disputed one would itself be a
    silent grade change."""
    good = _canvas_gs([("A", 0.95), ("F", 0.0)], id=111, title="Good")
    bad = _canvas_gs([("A", 0.10), ("F", 0.0)], id=222, title="Bad")
    course = _FakeCourse(_routes(account_owned=[good, bad]))
    result = sync_grading_standards(
        course,
        [{"title": "Good", "data": ELP}, {"title": "Bad", "data": ELP}],
    )
    assert result.standard_id is None
    assert len(result.mismatches) == 1


def test_unreadable_account_falls_back_to_creating():
    """A teacher without account-read permission gets a working sync, not a crash."""
    course = _FakeCourse({"courses/1/grading_standards": []})
    result = sync_grading_standards(course, [{"title": "Scheme", "data": ELP}])
    assert result.standard_id == 999


def test_no_standards_declared_is_a_no_op():
    course = _FakeCourse(_routes())
    assert sync_grading_standards(course, []) == (None, [])


def test_parent_accounts_are_searched():
    gs = _canvas_gs([("A", 0.95), ("F", 0.0)], id=777, context_id=5)
    course = _FakeCourse(_routes(parent_id=5))
    course._requester.routes["accounts/5/grading_standards"] = [gs]
    course._requester.routes["accounts/5"] = {"parent_account_id": None}
    result = sync_grading_standards(course, [{"title": "Scheme", "data": ELP}])
    assert result.standard_id == 777


def test_account_cycle_does_not_hang():
    course = _FakeCourse(_routes(parent_id=102875))
    assert sync_grading_standards(course, [{"title": "S", "data": ELP}]).standard_id == 999


# ---------------------------------------------------------------------------
# Import drops the source course's grading_standard_id
# ---------------------------------------------------------------------------

def test_import_drops_grading_standard_id(tmp_path):
    """It is the source course's id, meaningless in the target course, and it
    would override the title-based resolution on a metadata-only sync."""
    xml = tmp_path / "course_settings.xml"
    xml.write_text(
        '<?xml version="1.0"?>\n'
        "<course>\n"
        "  <title>Some Course</title>\n"
        "  <grading_standard_enabled>true</grading_standard_enabled>\n"
        "  <grading_standard_id>569017</grading_standard_id>\n"
        "</course>\n"
    )
    parsed = _parse_course_settings_full(xml)
    assert parsed["title"] == "Some Course"
    assert parsed["grading_standard_enabled"] is True
    assert "grading_standard_id" not in parsed


def test_grading_standard_id_is_not_uploaded_from_an_existing_repo():
    """Repos imported before the drop still carry the key; it must be ignored
    rather than sent, or the course's standard flip-flops between runs."""
    assert "grading_standard_id" in _COURSE_METADATA_SKIP
    sent = {}
    course = SimpleNamespace(update=lambda course: sent.update(course))
    update_course_metadata(course, {"title": "X", "grading_standard_id": 569017})
    assert "grading_standard_id" not in sent


def test_resolved_grading_standard_id_is_still_uploaded():
    sent = {}
    course = SimpleNamespace(update=lambda course: sent.update(course))
    update_course_metadata(
        course, {"title": "X", "grading_standard_id": 569017}, grading_standard_id=1234
    )
    assert sent["grading_standard_id"] == 1234
