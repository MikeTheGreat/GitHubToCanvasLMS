"""Unit tests for quiz.py — quiz and question file parsing."""
from pathlib import Path

import pytest

from github_to_canvas.quiz import parse_question_file, parse_quiz_file

FIXTURES = Path(__file__).parent / "fixtures"
QUIZ_DIR = FIXTURES / "quizzes" / "a-quiz"
QUIZ_MD = QUIZ_DIR / "a-quiz.md"
MCQ_MD = QUIZ_DIR / "questions" / "what-is-2-plus-2.md"
ESSAY_MD = QUIZ_DIR / "questions" / "explain-something.md"


# ---------------------------------------------------------------------------
# parse_quiz_file
# ---------------------------------------------------------------------------

def test_parse_quiz_file_returns_frontmatter():
    fm, _, _ = parse_quiz_file(QUIZ_MD)
    assert fm["title"] == "A Quiz"
    assert fm["quiz_type"] == "assignment"
    assert fm["points_possible"] == 6.0
    assert fm["time_limit"] == 30
    assert fm["published"] is True


def test_parse_quiz_file_question_order():
    _, _, q_paths = parse_quiz_file(QUIZ_MD)
    assert len(q_paths) == 2
    assert q_paths[0].name == "what-is-2-plus-2.md"
    assert q_paths[1].name == "explain-something.md"


def test_parse_quiz_file_description_html():
    _, desc_html, _ = parse_quiz_file(QUIZ_MD)
    assert "carefully" in desc_html


def test_parse_quiz_file_description_excludes_question_links():
    _, desc_html, _ = parse_quiz_file(QUIZ_MD)
    assert "what-is-2-plus-2" not in desc_html
    assert "explain-something" not in desc_html


def test_parse_quiz_file_question_paths_are_absolute():
    _, _, q_paths = parse_quiz_file(QUIZ_MD)
    for p in q_paths:
        assert p.is_absolute()


# ---------------------------------------------------------------------------
# parse_question_file — MCQ
# ---------------------------------------------------------------------------

def test_parse_mcq_type():
    q = parse_question_file(MCQ_MD)
    assert q["question_type"] == "multiple_choice_question"


def test_parse_mcq_title():
    q = parse_question_file(MCQ_MD)
    assert q["title"] == "What is 2+2?"


def test_parse_mcq_points():
    q = parse_question_file(MCQ_MD)
    assert q["points_possible"] == 1


def test_parse_mcq_answers_count():
    q = parse_question_file(MCQ_MD)
    assert len(q["answers"]) == 3


def test_parse_mcq_correct_answer_has_weight_100():
    q = parse_question_file(MCQ_MD)
    # correct: 2 means 0-indexed index 1 (answer "4")
    assert q["answers"][1]["text"] == "4"
    assert q["answers"][1]["weight"] == 100


def test_parse_mcq_wrong_answers_have_weight_0():
    q = parse_question_file(MCQ_MD)
    assert q["answers"][0]["weight"] == 0
    assert q["answers"][2]["weight"] == 0


def test_parse_mcq_question_text_contains_prompt():
    q = parse_question_file(MCQ_MD)
    assert "2" in q["question_text"]


# ---------------------------------------------------------------------------
# parse_question_file — essay
# ---------------------------------------------------------------------------

def test_parse_essay_type():
    q = parse_question_file(ESSAY_MD)
    assert q["question_type"] == "essay_question"


def test_parse_essay_title():
    q = parse_question_file(ESSAY_MD)
    assert q["title"] == "Explain something"


def test_parse_essay_points():
    q = parse_question_file(ESSAY_MD)
    assert q["points_possible"] == 5


def test_parse_essay_answers_empty():
    q = parse_question_file(ESSAY_MD)
    assert q["answers"] == []


def test_parse_essay_has_question_text():
    q = parse_question_file(ESSAY_MD)
    assert q["question_text"]


# ---------------------------------------------------------------------------
# parse_question_file — true/false (written inline)
# ---------------------------------------------------------------------------

def test_parse_true_false(tmp_path):
    tf_md = tmp_path / "tf.md"
    tf_md.write_text(
        "---\n"
        "title: Sky is blue\n"
        "question_type: true_false_question\n"
        "points_possible: 1\n"
        "correct: true\n"
        "---\n\n"
        "The sky appears blue during the day.\n"
    )
    q = parse_question_file(tf_md)
    assert q["question_type"] == "true_false_question"
    assert len(q["answers"]) == 2
    true_ans = next(a for a in q["answers"] if a["text"] == "True")
    false_ans = next(a for a in q["answers"] if a["text"] == "False")
    assert true_ans["weight"] == 100
    assert false_ans["weight"] == 0


def test_parse_true_false_correct_false(tmp_path):
    tf_md = tmp_path / "tf.md"
    tf_md.write_text(
        "---\n"
        "title: Sky is green\n"
        "question_type: true_false_question\n"
        "points_possible: 1\n"
        "correct: false\n"
        "---\n\n"
        "The sky appears green during the day.\n"
    )
    q = parse_question_file(tf_md)
    false_ans = next(a for a in q["answers"] if a["text"] == "False")
    true_ans = next(a for a in q["answers"] if a["text"] == "True")
    assert false_ans["weight"] == 100
    assert true_ans["weight"] == 0
