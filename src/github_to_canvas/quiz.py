"""Parse quiz and question Markdown files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .convert import markdown_to_html, preprocess_snippets


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}, text
    return yaml.safe_load(text[4:end]) or {}, text[end + 5:]


_QUIZ_LINK_RE = re.compile(r"^\s*\d+\.\s+\[([^\]]+)\]\(([^)]+\.md)\)")
_ANSWERS_HEADING_RE = re.compile(r"^##\s+Answers\s*$", re.MULTILINE)
_ANSWER_ITEM_RE = re.compile(r"^\s*\d+\.\s+(.+)")
_SECTION_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_SUBSECTION_HEADING_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)


def parse_quiz_file(
    quiz_md: Path, snippets_dir: Path | None = None
) -> tuple[dict[str, Any], str, list[Path]]:
    """Parse a quiz-level .md file.

    Returns (frontmatter, description_html, question_paths_in_order).
    question_paths_in_order is a list of absolute Paths to question files,
    in the order they appear in the numbered list.
    """
    text = quiz_md.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)

    if snippets_dir is not None:
        body = preprocess_snippets(body, quiz_md, snippets_dir)

    question_files: list[Path] = []
    description_lines: list[str] = []

    for line in body.splitlines():
        m = _QUIZ_LINK_RE.match(line)
        if m:
            href = m.group(2)
            q_path = (quiz_md.parent / href).resolve()
            question_files.append(q_path)
        else:
            description_lines.append(line)

    description_md = "\n".join(description_lines).strip()
    desc_html = markdown_to_html(description_md) if description_md else ""

    return frontmatter, desc_html, question_files


def _split_sections(body: str) -> dict[str, str]:
    """Split body into named sections keyed by ## heading text (lowercased).

    The implicit first section (before any ## heading) is stored as "".
    """
    sections: dict[str, str] = {}
    parts = _SECTION_HEADING_RE.split(body)
    # parts[0] = text before first ##, then alternating heading/content
    sections[""] = parts[0]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip().lower()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        sections[heading] = content
    return sections


def _parse_feedback_section(feedback_body: str) -> dict[str, str]:
    """Parse ### General/Correct/Incorrect subsections from a ## Feedback section."""
    result: dict[str, str] = {}
    parts = _SUBSECTION_HEADING_RE.split(feedback_body)
    # parts[0] is text before first ###
    for i in range(1, len(parts), 2):
        subheading = parts[i].strip().lower()
        content = (parts[i + 1] if i + 1 < len(parts) else "").strip()
        if subheading == "general" and content:
            result["neutral_comments"] = content
        elif subheading == "correct" and content:
            result["correct_comments"] = content
        elif subheading == "incorrect" and content:
            result["incorrect_comments"] = content
    return result


def _parse_answers_section(answers_text: str, correct, question_type: str) -> list[dict[str, Any]]:
    """Parse numbered answer list into answer dicts for MCQ or multiple_response."""
    answer_texts: list[str] = []
    for line in answers_text.splitlines():
        am = _ANSWER_ITEM_RE.match(line)
        if am:
            answer_texts.append(am.group(1).strip())

    if question_type == "multiple_response_question":
        correct_set = set(correct) if isinstance(correct, list) else set()
        return [
            {"text": text, "weight": 100 if (i + 1) in correct_set else 0}
            for i, text in enumerate(answer_texts)
        ]
    # multiple_choice_question
    return [
        {"text": text, "weight": 100 if (i + 1) == correct else 0}
        for i, text in enumerate(answer_texts)
    ]


def parse_question_file(
    q_path: Path, snippets_dir: Path | None = None
) -> dict[str, Any]:
    """Parse a quiz question .md file.

    Returns a dict with: title, question_type, points_possible, question_text (HTML),
    answers (list of {text, weight} for MCQ/T-F/multiple_response; empty for essay),
    and optionally neutral_comments, correct_comments, incorrect_comments.
    rel_path is NOT set here — callers add it.
    """
    text = q_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)

    if snippets_dir is not None:
        body = preprocess_snippets(body, q_path, snippets_dir)

    question_type = frontmatter.get("question_type", "essay_question")
    points = frontmatter.get("points_possible", 0)
    title = frontmatter.get("title", q_path.stem)
    correct = frontmatter.get("correct")

    sections = _split_sections(body)
    feedback_section = sections.get("feedback", "")
    feedback = _parse_feedback_section(feedback_section) if feedback_section.strip() else {}

    # Question text: prefer ## Question section, fall back to implicit first section
    desc = sections.get("question", sections.get("", "")).strip()
    question_text_html = markdown_to_html(desc) if desc else ""

    if question_type == "true_false_question":
        answers = [
            {"text": "True", "weight": 100 if correct is True else 0},
            {"text": "False", "weight": 100 if correct is False else 0},
        ]
        return {
            "title": title,
            "question_type": question_type,
            "points_possible": points,
            "question_text": question_text_html,
            "answers": answers,
            **feedback,
        }

    if question_type in ("multiple_choice_question", "multiple_response_question"):
        answers_text = sections.get("answers", "")
        answers = _parse_answers_section(answers_text, correct, question_type)
        return {
            "title": title,
            "question_type": question_type,
            "points_possible": points,
            "question_text": question_text_html,
            "answers": answers,
            **feedback,
        }

    if question_type == "fill_in_blank_question":
        accepted = frontmatter.get("answers", [])
        answers = [{"text": a, "weight": 100} for a in accepted]
        return {
            "title": title,
            "question_type": "short_answer_question",
            "points_possible": points,
            "question_text": question_text_html,
            "answers": answers,
            **feedback,
        }

    if question_type == "pattern_match_question":
        patterns = frontmatter.get("answers", [])
        answers = [{"text": patterns[0], "weight": 100}] if patterns else []
        return {
            "title": title,
            "question_type": "short_answer_question",
            "points_possible": points,
            "question_text": question_text_html,
            "answers": answers,
            **feedback,
        }

    # essay_question or any other type — no structured answers
    # §11: Sample Solution → neutral_comments
    sample_solution = sections.get("sample solution", "").strip()
    if sample_solution and "neutral_comments" not in feedback:
        feedback["neutral_comments"] = sample_solution

    return {
        "title": title,
        "question_type": question_type,
        "points_possible": points,
        "question_text": question_text_html,
        "answers": [],
        **feedback,
    }
