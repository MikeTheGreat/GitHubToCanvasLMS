"""Canvas upload logic via the canvasapi library."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from canvasapi import Canvas

from .config import Config


def get_course(config: Config):
    canvas = Canvas(config.base_url, config.api_token)
    return canvas.get_course(config.course_id)


def upload_asset(course, local_path: Path, assets_root: Path) -> dict[str, Any]:
    """Upload a file to Canvas Files. Returns manifest entry."""
    rel = local_path.relative_to(assets_root)
    parent = rel.parent
    canvas_folder = "course files" if parent == Path(".") else f"course files/{parent}"
    _success, response = course.upload(str(local_path), parent_folder_path=canvas_folder)
    return {
        "canvas_type": "file",
        "canvas_id": response["id"],
        "canvas_url": response["url"],
    }


def create_or_update_page(
    course, canvas_url: str | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    """Create or update a Canvas Page. `canvas_url` is the page URL slug."""
    if canvas_url is not None:
        page = course.get_page(canvas_url)
        page = page.edit(wiki_page={"title": title, "body": body, **kwargs})
    else:
        page = course.create_page(wiki_page={"title": title, "body": body, **kwargs})
    return {"canvas_type": "page", "canvas_id": page.page_id, "canvas_url": page.url}


def create_or_update_assignment(
    course, canvas_id: int | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    if canvas_id is not None:
        assignment = course.get_assignment(canvas_id)
        assignment = assignment.edit(assignment={"name": title, "description": body, **kwargs})
    else:
        assignment = course.create_assignment(
            assignment={"name": title, "description": body, **kwargs}
        )
    return {"canvas_type": "assignment", "canvas_id": assignment.id}


def create_or_update_discussion(
    course, canvas_id: int | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    if canvas_id is not None:
        topic = course.get_discussion_topic(canvas_id)
        topic = topic.update(title=title, message=body, **kwargs)
    else:
        topic = course.create_discussion_topic(title=title, message=body, **kwargs)
    return {"canvas_type": "discussion", "canvas_id": topic.id}


def create_stub(course, canvas_type: str, title: str) -> dict[str, Any]:
    """Create a minimal unpublished Canvas item. Returns manifest entry."""
    if canvas_type == "page":
        page = course.create_page(wiki_page={"title": title, "body": "", "published": False})
        return {"canvas_type": "page", "canvas_id": page.page_id, "canvas_url": page.url}
    if canvas_type == "assignment":
        assignment = course.create_assignment(
            assignment={"name": title, "description": "", "published": False}
        )
        return {"canvas_type": "assignment", "canvas_id": assignment.id}
    if canvas_type == "discussion":
        topic = course.create_discussion_topic(title=title, message="", published=False)
        return {"canvas_type": "discussion", "canvas_id": topic.id}
    raise ValueError(f"Cannot create stub for canvas_type: {canvas_type!r}")


def create_or_update_module(course, canvas_id: int | None, title: str, **kwargs):
    """Return the canvasapi Module object (created or updated)."""
    if canvas_id is not None:
        module = course.get_module(canvas_id)
        return module.edit(module={"name": title, **kwargs})
    return course.create_module(module={"name": title, **kwargs})


_CANVAS_ITEM_TYPE = {
    "page": "Page",
    "assignment": "Assignment",
    "discussion": "Discussion",
}


def clear_module_items(module) -> None:
    for item in module.get_module_items():
        item.delete()


def add_module_item(module, item: dict[str, Any], manifest: dict) -> int:
    """Add one item to a Canvas module. Returns the Canvas module item ID."""
    if item["type"] == "SubHeader":
        mi = module.create_module_item(
            module_item={"type": "SubHeader", "title": item["title"]}
        )
        return mi.id

    local_path = item["local_path"]
    entry = manifest[local_path]
    canvas_type = _CANVAS_ITEM_TYPE[entry["canvas_type"]]
    mi = module.create_module_item(
        module_item={
            "type": canvas_type,
            "content_id": entry["canvas_id"],
            "title": item["title"],
        }
    )
    return mi.id
