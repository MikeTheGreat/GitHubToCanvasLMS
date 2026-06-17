"""Find orphaned (unreferenced) resources in a Canvas course."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

import click


@dataclass(frozen=True)
class ResourceKey:
    canvas_type: str
    identifier: str | int


@dataclass
class ResourceInfo:
    key: ResourceKey
    title: str
    html_url: str
    published: bool


_CANVAS_REF_RE = re.compile(
    r"(?:https?://[^/]+)?/courses/\d+/"
    r"(?:"
    r'pages/([^"\'?#\s]+)'
    r"|assignments/(\d+)"
    r"|discussion_topics/(\d+)"
    r"|quizzes/(\d+)"
    r"|files/(\d+)"
    r"|modules/(\d+)"
    r")"
)

_MODULE_ITEM_TYPE_MAP = {
    "Page": "page",
    "Assignment": "assignment",
    "Discussion": "discussion",
    "Quiz": "quiz",
    "File": "file",
}

_TYPE_LABELS = {
    "page": "Pages",
    "assignment": "Assignments",
    "discussion": "Discussions",
    "quiz": "Quizzes",
}


def extract_canvas_refs(html: str) -> set[ResourceKey]:
    refs: set[ResourceKey] = set()
    if not html:
        return refs
    for m in _CANVAS_REF_RE.finditer(html):
        if m.group(1):
            refs.add(ResourceKey("page", unquote(m.group(1))))
        elif m.group(2):
            refs.add(ResourceKey("assignment", int(m.group(2))))
        elif m.group(3):
            refs.add(ResourceKey("discussion", int(m.group(3))))
        elif m.group(4):
            refs.add(ResourceKey("quiz", int(m.group(4))))
        elif m.group(5):
            refs.add(ResourceKey("file", int(m.group(5))))
        elif m.group(6):
            refs.add(ResourceKey("module", int(m.group(6))))
    return refs


def _scan_html(html: str, referenced: set[ResourceKey]) -> None:
    referenced.update(extract_canvas_refs(html))


def find_orphans(course) -> list[ResourceInfo]:
    all_resources: dict[ResourceKey, ResourceInfo] = {}
    referenced: set[ResourceKey] = set()

    click.echo("Fetching pages...")
    for page in course.get_pages(include=["body"]):
        key = ResourceKey("page", page.url)
        all_resources[key] = ResourceInfo(
            key=key,
            title=page.title,
            html_url=getattr(page, "html_url", ""),
            published=getattr(page, "published", False),
        )
        _scan_html(getattr(page, "body", "") or "", referenced)

    click.echo("Fetching assignments...")
    for a in course.get_assignments():
        key = ResourceKey("assignment", a.id)
        all_resources[key] = ResourceInfo(
            key=key,
            title=a.name,
            html_url=getattr(a, "html_url", ""),
            published=getattr(a, "published", False),
        )
        _scan_html(getattr(a, "description", "") or "", referenced)

    click.echo("Fetching discussions...")
    for d in course.get_discussion_topics():
        key = ResourceKey("discussion", d.id)
        all_resources[key] = ResourceInfo(
            key=key,
            title=d.title,
            html_url=getattr(d, "html_url", ""),
            published=getattr(d, "published", False),
        )
        _scan_html(getattr(d, "message", "") or "", referenced)

    click.echo("Fetching quizzes...")
    quizzes = []
    for q in course.get_quizzes():
        key = ResourceKey("quiz", q.id)
        all_resources[key] = ResourceInfo(
            key=key,
            title=q.title,
            html_url=getattr(q, "html_url", ""),
            published=getattr(q, "published", False),
        )
        _scan_html(getattr(q, "description", "") or "", referenced)
        quizzes.append(q)

    click.echo("Scanning quiz questions for links...")
    for q in quizzes:
        try:
            for question in q.get_questions():
                _scan_html(getattr(question, "question_text", "") or "", referenced)
                for answer in getattr(question, "answers", []) or []:
                    _scan_html(answer.get("html", "") or "", referenced)
                    _scan_html(answer.get("text", "") or "", referenced)
        except Exception:
            pass

    click.echo("Fetching modules and module items...")
    for module in course.get_modules():
        for item in module.get_module_items():
            item_type = getattr(item, "type", "")
            canvas_type = _MODULE_ITEM_TYPE_MAP.get(item_type)
            if canvas_type == "page":
                page_url = getattr(item, "page_url", None)
                if page_url:
                    referenced.add(ResourceKey("page", page_url))
            elif canvas_type:
                content_id = getattr(item, "content_id", None)
                if content_id:
                    referenced.add(ResourceKey(canvas_type, content_id))

    click.echo("Checking front page...")
    try:
        front_page = course.show_front_page()
        if front_page:
            referenced.add(ResourceKey("page", front_page.url))
    except Exception:
        pass

    click.echo("Checking syllabus...")
    try:
        response = course._requester.request(
            "GET",
            f"courses/{course.id}",
            _kwargs=[("include[]", "syllabus_body")],
        )
        syllabus_body = response.json().get("syllabus_body", "") or ""
        _scan_html(syllabus_body, referenced)
    except Exception:
        pass

    orphans = [info for key, info in all_resources.items() if key not in referenced]
    orphans.sort(key=lambda r: (r.key.canvas_type, r.title.lower()))

    return orphans


def print_report(orphans: list[ResourceInfo], base_url: str) -> None:
    if not orphans:
        click.secho("No orphaned resources found.", fg="green")
        return

    click.echo()
    click.secho(f"Found {len(orphans)} orphaned resource(s):", fg="yellow")
    click.echo()

    current_type = None
    for r in orphans:
        if r.key.canvas_type != current_type:
            current_type = r.key.canvas_type
            label = _TYPE_LABELS.get(current_type, current_type.title() + "s")
            click.secho(f"  {label}:", bold=True)

        status = "published" if r.published else "unpublished"
        url = r.html_url
        if url and not url.startswith("http"):
            url = base_url + url
        click.echo(f"    - {r.title}  ({status})")
        if url:
            click.echo(f"      {url}")
