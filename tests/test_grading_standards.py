"""Grading-standard value scaling on upload.

Canvas returns grading-scheme values as fractions (0..1) but the create endpoint
expects the lower bound on a 0..100 scale. course_settings.toml stores fractions
(that is what ``import`` writes), so sync has to scale on the way out.
"""

from markdown_to_canvas.canvas_api import _grading_scheme_entries


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
