"""Unit tests for quiz.py — quiz and question file parsing."""
from pathlib import Path


from markdown_to_canvas.quiz import parse_question_file, parse_quiz_file

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


def test_parse_quiz_file_skips_question_lookalikes_in_code_blocks(tmp_path):
    """A numbered .md link inside a regular fenced code block is literal
    example text in the description, not a question."""
    quiz_md = tmp_path / "quiz.md"
    quiz_md.write_text(
        "---\ntitle: Q\n---\n\n"
        "How to write a question list:\n\n"
        "```markdown\n"
        "1. [Example](questions/example.md)\n"
        "```\n\n"
        "1. [Question](questions/question.md)\n"
    )
    _, desc_html, q_paths = parse_quiz_file(quiz_md)
    assert [p.name for p in q_paths] == ["question.md"]
    assert "example.md" in desc_html  # stays in the description as code


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


# ---------------------------------------------------------------------------
# parse_question_file — multiple_response_question
# ---------------------------------------------------------------------------

def test_parse_multiple_response_type(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Select all primes\n"
        "question_type: multiple_response_question\n"
        "points_possible: 2\n"
        "correct: [1, 3]\n"
        "---\n\n"
        "Select all prime numbers.\n\n"
        "## Answers\n\n"
        "1. 2\n"
        "2. 4\n"
        "3. 7\n"
        "4. 9\n"
    )
    q = parse_question_file(md)
    assert q["question_type"] == "multiple_response_question"


def test_parse_multiple_response_correct_answers_have_weight_100(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Select primes\n"
        "question_type: multiple_response_question\n"
        "points_possible: 2\n"
        "correct: [1, 3]\n"
        "---\n\n"
        "Pick all primes.\n\n"
        "## Answers\n\n"
        "1. 2\n"
        "2. 4\n"
        "3. 7\n"
        "4. 9\n"
    )
    q = parse_question_file(md)
    assert len(q["answers"]) == 4
    assert q["answers"][0]["weight"] == 100   # index 1 correct
    assert q["answers"][1]["weight"] == 0
    assert q["answers"][2]["weight"] == 100   # index 3 correct
    assert q["answers"][3]["weight"] == 0


# ---------------------------------------------------------------------------
# parse_question_file — fill_in_blank_question
# ---------------------------------------------------------------------------

def test_parse_fill_in_blank_maps_to_short_answer(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Speed of light\n"
        "question_type: fill_in_blank_question\n"
        "points_possible: 1\n"
        "answers: [300000, '3 x 10^5']\n"
        "---\n\n"
        "The speed of light is approximately _____ km/s.\n"
    )
    q = parse_question_file(md)
    assert q["question_type"] == "short_answer_question"


def test_parse_fill_in_blank_answers_all_weight_100(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Speed\n"
        "question_type: fill_in_blank_question\n"
        "points_possible: 1\n"
        "answers: [photosynthesis, Photosynthesis]\n"
        "---\n\n"
        "Plants make food by ________.\n"
    )
    q = parse_question_file(md)
    assert all(a["weight"] == 100 for a in q["answers"])
    assert {a["text"] for a in q["answers"]} == {"photosynthesis", "Photosynthesis"}


# ---------------------------------------------------------------------------
# parse_question_file — pattern_match_question
# ---------------------------------------------------------------------------

def test_parse_pattern_match_maps_to_short_answer(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Name a language\n"
        "question_type: pattern_match_question\n"
        "points_possible: 1\n"
        "answers: [python, r language]\n"
        "match_type: substring\n"
        "---\n\n"
        "Name a data science programming language.\n"
    )
    q = parse_question_file(md)
    assert q["question_type"] == "short_answer_question"
    assert q["answers"][0]["text"] == "python"
    assert q["answers"][0]["weight"] == 100


# ---------------------------------------------------------------------------
# parse_question_file — feedback sections
# ---------------------------------------------------------------------------

def test_parse_question_feedback_correct_incorrect(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: A question\n"
        "question_type: multiple_choice_question\n"
        "points_possible: 1\n"
        "correct: 2\n"
        "---\n\n"
        "What is the answer?\n\n"
        "## Answers\n\n"
        "1. Wrong\n"
        "2. Right\n\n"
        "## Feedback\n\n"
        "### Correct\n"
        "Well done!\n\n"
        "### Incorrect\n"
        "Try again.\n"
    )
    q = parse_question_file(md)
    assert q["correct_comments"] == "Well done!"
    assert q["incorrect_comments"] == "Try again."
    assert "neutral_comments" not in q


def test_parse_question_feedback_general(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: A question\n"
        "question_type: essay_question\n"
        "points_possible: 5\n"
        "---\n\n"
        "Explain something.\n\n"
        "## Feedback\n\n"
        "### General\n"
        "General feedback here.\n"
    )
    q = parse_question_file(md)
    assert q["neutral_comments"] == "General feedback here."


def test_parse_question_no_feedback_no_comment_keys(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: A question\n"
        "question_type: essay_question\n"
        "points_possible: 5\n"
        "---\n\n"
        "Explain something.\n"
    )
    q = parse_question_file(md)
    assert "neutral_comments" not in q
    assert "correct_comments" not in q
    assert "incorrect_comments" not in q


# ---------------------------------------------------------------------------
# parse_question_file — essay sample solution → neutral_comments
# ---------------------------------------------------------------------------

def test_parse_essay_sample_solution_becomes_neutral_comments(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Explain gravity\n"
        "question_type: essay_question\n"
        "points_possible: 5\n"
        "---\n\n"
        "Explain the concept of gravity.\n\n"
        "## Sample Solution\n\n"
        "Gravity is a force that attracts two masses toward each other.\n"
    )
    q = parse_question_file(md)
    assert "Gravity is a force" in q["neutral_comments"]


def test_parse_essay_sample_solution_does_not_bleed_into_question_text(tmp_path):
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Explain gravity\n"
        "question_type: essay_question\n"
        "points_possible: 5\n"
        "---\n\n"
        "Explain the concept of gravity.\n\n"
        "## Sample Solution\n\n"
        "Gravity is a force that attracts two masses toward each other.\n"
    )
    q = parse_question_file(md)
    assert "Gravity is a force" not in q["question_text"]


# ---------------------------------------------------------------------------
# Snippet expansion in quiz and question files
# ---------------------------------------------------------------------------

def test_parse_quiz_file_expands_inline_snippets(tmp_path):
    """Inline snippets in quiz description are expanded when snippets_dir is provided."""
    snippets_inline = tmp_path / "snippets" / "inline"
    snippets_inline.mkdir(parents=True)
    (snippets_inline / "CANVAS_COURSE_REFERENCE.md").write_text(
        "https://school.instructure.com/courses/999\n"
    )
    quiz_dir = tmp_path / "quizzes" / "q1"
    (quiz_dir / "questions").mkdir(parents=True)
    (quiz_dir / "questions" / "q.md").write_text(
        "---\ntitle: Q\nquestion_type: essay_question\npoints_possible: 1\n---\n\nExplain.\n"
    )
    quiz_md = quiz_dir / "q1.md"
    quiz_md.write_text(
        "---\ntitle: Quiz\n---\n\n"
        "See your [Grades]($../../snippets/inline/CANVAS_COURSE_REFERENCE.md$/grades) in Canvas.\n\n"
        "1. [Q](questions/q.md)\n"
    )
    _, desc_html, _ = parse_quiz_file(quiz_md, tmp_path / "snippets")
    assert "https://school.instructure.com/courses/999/grades" in desc_html


def test_parse_question_file_expands_inline_snippets(tmp_path):
    """Inline snippets in question text are expanded when snippets_dir is provided."""
    snippets_inline = tmp_path / "snippets" / "inline"
    snippets_inline.mkdir(parents=True)
    (snippets_inline / "CANVAS_COURSE_REFERENCE.md").write_text(
        "https://school.instructure.com/courses/999\n"
    )
    q_dir = tmp_path / "quizzes" / "q1" / "questions"
    q_dir.mkdir(parents=True)
    q_md = q_dir / "q.md"
    q_md.write_text(
        "---\ntitle: Q\nquestion_type: essay_question\npoints_possible: 1\n---\n\n"
        "Review your [Grades]($../../../snippets/inline/CANVAS_COURSE_REFERENCE.md$/grades) and answer.\n"
    )
    q = parse_question_file(q_md, tmp_path / "snippets")
    assert "https://school.instructure.com/courses/999/grades" in q["question_text"]


def test_parse_quiz_file_merges_frontmatter_snippet(tmp_path):
    """PASTE_SNIPPET_INTO_FRONTMATTER merges shared defaults into quiz frontmatter."""
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    (snippets_dir / "quiz-defaults.md").write_text("time_limit: 30\nallowed_attempts: 1\n")
    quiz_dir = tmp_path / "quizzes" / "q1"
    quiz_dir.mkdir(parents=True)
    quiz_md = quiz_dir / "q1.md"
    quiz_md.write_text(
        "---\ntitle: Quiz\n---\n"
        "[PASTE_SNIPPET_INTO_FRONTMATTER](../../snippets/quiz-defaults.md)\n\n"
        "Description text.\n"
    )
    fm, desc_html, _ = parse_quiz_file(quiz_md, snippets_dir)
    assert fm == {"title": "Quiz", "time_limit": 30, "allowed_attempts": 1}
    assert "PASTE_SNIPPET_INTO_FRONTMATTER" not in desc_html
    assert "Description text." in desc_html


def test_parse_question_file_merges_frontmatter_snippet(tmp_path):
    """PASTE_SNIPPET_INTO_FRONTMATTER merges shared defaults into question frontmatter."""
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    (snippets_dir / "question-defaults.md").write_text(
        "question_type: essay_question\npoints_possible: 2\n"
    )
    q_dir = tmp_path / "quizzes" / "q1" / "questions"
    q_dir.mkdir(parents=True)
    q_md = q_dir / "q.md"
    q_md.write_text(
        "---\ntitle: Q\n---\n"
        "[PASTE_SNIPPET_INTO_FRONTMATTER](../../../snippets/question-defaults.md)\n\n"
        "Explain.\n"
    )
    q = parse_question_file(q_md, snippets_dir)
    assert q["question_type"] == "essay_question"
    assert q["points_possible"] == 2
    assert "Explain." in q["question_text"]


def test_parse_quiz_file_works_without_snippets_dir():
    """parse_quiz_file still works when snippets_dir is not provided (backward compat)."""
    fm, desc_html, q_paths = parse_quiz_file(QUIZ_MD)
    assert fm["title"] == "A Quiz"
    assert len(q_paths) == 2


# ---------------------------------------------------------------------------
# Fenced code blocks in question files
# ---------------------------------------------------------------------------

def test_question_heading_lookalike_in_code_fence_is_not_a_section(tmp_path):
    """A '## Answers'-looking line inside a code fence (e.g. a bash or markdown
    example) must not split the question into sections."""
    md = tmp_path / "q.md"
    md.write_text(
        "---\n"
        "title: Markdown question\n"
        "question_type: multiple_choice_question\n"
        "points_possible: 1\n"
        "correct: 1\n"
        "---\n\n"
        "What does this markdown do?\n\n"
        "```markdown\n"
        "## Answers\n"
        "1. fake answer\n"
        "```\n\n"
        "## Answers\n\n"
        "1. real answer\n"
        "2. other answer\n"
    )
    q = parse_question_file(md)
    assert [a["text"] for a in q["answers"]] == ["real answer", "other answer"]
    # the example block stays in the question text
    assert "fake answer" in q["question_text"]


