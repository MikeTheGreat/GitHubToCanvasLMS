"""Parse quiz and question Markdown files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .convert import markdown_to_html


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


def parse_quiz_file(quiz_md: Path) -> tuple[dict[str, Any], str, list[Path]]:
    """Parse a quiz-level .md file.

    Returns (frontmatter, description_html, question_paths_in_order).
    question_paths_in_order is a list of absolute Paths to question files,
    in the order they appear in the numbered list.
    """
    text = quiz_md.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)

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


def parse_question_file(q_path: Path) -> dict[str, Any]:
    """Parse a quiz question .md file.

    Returns a dict with: title, question_type, points_possible, question_text (HTML),
    answers (list of {text, weight} for MCQ/T-F; empty for essay).
    rel_path is NOT set here — callers add it.
    """
    text = q_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)

    question_type = frontmatter.get("question_type", "essay_question")
    points = frontmatter.get("points_possible", 0)
    title = frontmatter.get("title", q_path.stem)
    correct = frontmatter.get("correct")

    if question_type == "true_false_question":
        question_text_html = markdown_to_html(body.strip()) if body.strip() else ""
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
        }

    if question_type == "multiple_choice_question":
        m = _ANSWERS_HEADING_RE.search(body)
        if m:
            desc_part = body[: m.start()].strip()
            answers_part = body[m.end():].strip()
        else:
            desc_part = body.strip()
            answers_part = ""

        question_text_html = markdown_to_html(desc_part) if desc_part else ""

        answer_texts: list[str] = []
        for line in answers_part.splitlines():
            am = _ANSWER_ITEM_RE.match(line)
            if am:
                answer_texts.append(am.group(1).strip())

        answers = [
            {"text": text, "weight": 100 if (i + 1) == correct else 0}
            for i, text in enumerate(answer_texts)
        ]
        return {
            "title": title,
            "question_type": question_type,
            "points_possible": points,
            "question_text": question_text_html,
            "answers": answers,
        }

    # essay_question or any other type — no structured answers
    question_text_html = markdown_to_html(body.strip()) if body.strip() else ""
    return {
        "title": title,
        "question_type": question_type,
        "points_possible": points,
        "question_text": question_text_html,
        "answers": [],
    }
