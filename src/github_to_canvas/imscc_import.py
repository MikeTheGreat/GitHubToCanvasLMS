"""Import a Canvas course from a local .imscc file into a Markdown repo."""
from __future__ import annotations

import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pypandoc


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TempEntry:
    """One item from the IMSCC, classified and mapped to its output path."""
    imscc_id: str
    category: str          # page | assignment | discussion | asset | quiz | external_url | lti | course_settings | syllabus
    imscc_path: str        # path within the imscc directory
    local_path: str        # output path relative to output_dir
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Group 0: Input normalization
# ---------------------------------------------------------------------------

def open_imscc(path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    """Return (imscc_dir, tmp) where imscc_dir is a usable directory.

    If path is a zip / .imscc file, extract it into a temp dir and return
    (temp_dir_path, tmp_object).  Caller must keep tmp alive and call
    tmp.cleanup() when done.  If path is already a directory, returns
    (path, None).
    """
    if path.is_dir():
        return path, None
    if zipfile.is_zipfile(path):
        tmp = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp.name)
        return Path(tmp.name), tmp
    raise ValueError(f"Not a directory or zip file: {path}")


# ---------------------------------------------------------------------------
# Group 1: Temp manifest — parse imsmanifest.xml
# ---------------------------------------------------------------------------

# XML namespaces used in imsmanifest.xml
_NS_IMS = "http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1"


def _slugify(text: str) -> str:
    """Convert a title to a filename-safe slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def _title_from_html_file(html_path: Path) -> str:
    """Extract <title> text from an HTML file, falling back to the stem."""
    try:
        content = html_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<title[^>]*>([^<]+)</title>", content, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    return html_path.stem


def _title_from_xml_element(xml_path: Path, tag: str) -> str:
    """Return the text of the first <tag> element in an XML file."""
    try:
        tree = ET.parse(xml_path)
        # try with and without namespace
        el = tree.find(f".//{tag}")
        if el is None:
            for ns in _iter_namespaces(tree.getroot()):
                el = tree.find(f".//{{{ns}}}{tag}")
                if el is not None:
                    break
        if el is not None and el.text:
            return el.text.strip()
    except (OSError, ET.ParseError):
        pass
    return xml_path.stem


def _iter_namespaces(element: ET.Element) -> list[str]:
    """Return all namespace URIs found in the element tree."""
    namespaces: set[str] = set()
    for el in element.iter():
        if el.tag.startswith("{"):
            ns = el.tag.split("}")[0][1:]
            namespaces.add(ns)
    return list(namespaces)


def _strip_ns(tag: str) -> str:
    """Remove {namespace} prefix from an XML tag."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def parse_imsmanifest(imscc_dir: Path) -> dict[str, TempEntry]:
    """Parse imsmanifest.xml and return a temp manifest dict.

    Keys are IMSCC resource identifiers.  Only primary content items are
    included (dependencies like topicMeta are excluded from the top-level
    dict but their paths are stored in the metadata of their parent).
    """
    manifest_path = imscc_dir / "imsmanifest.xml"
    tree = ET.parse(manifest_path)
    root = tree.getroot()

    # Find all identifiers that appear only as <dependency> targets — these
    # are secondary resources (e.g. topicMeta) and should not be top-level.
    dependency_ids: set[str] = set()
    for dep in root.iter():
        if _strip_ns(dep.tag) == "dependency":
            ref = dep.get("identifierref")
            if ref:
                dependency_ids.add(ref)

    # Build a quick lookup: identifier → resource element
    resource_map: dict[str, ET.Element] = {}
    for el in root.iter():
        if _strip_ns(el.tag) == "resource":
            rid = el.get("identifier", "")
            if rid:
                resource_map[rid] = el

    result: dict[str, TempEntry] = {}

    for identifier, res in resource_map.items():
        res_type = res.get("type", "")
        href = res.get("href", "")
        intended_use = res.get("intendeduse", "")

        # --- Syllabus (special case of associatedcontent) ---
        if intended_use == "syllabus":
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="syllabus",
                imscc_path=href,
                local_path="course_settings/syllabus.md",
                title="Syllabus",
            )
            continue

        # --- Course settings root resource (no html href) ---
        if res_type.startswith("associatedcontent/") and not href.endswith(".html") and not href.endswith(".xml"):
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="course_settings",
                imscc_path=href,
                local_path="course_settings/course_settings.md",
                title="Course Settings",
            )
            continue

        # --- Pages (webcontent + wiki_content/) ---
        if res_type == "webcontent" and href.startswith("wiki_content/"):
            html_path = imscc_dir / href
            title = _title_from_html_file(html_path)
            stem = Path(href).stem
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="page",
                imscc_path=href,
                local_path=f"pages/{stem}.md",
                title=title,
            )
            continue

        # --- Assets (webcontent + web_resources/) ---
        if res_type == "webcontent" and href.startswith("web_resources/"):
            rel = href[len("web_resources/"):]
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="asset",
                imscc_path=href,
                local_path=f"assets/{rel}",
                title=Path(href).name,
            )
            continue

        # --- Discussions (imsdt_xmlv1p1) ---
        if res_type == "imsdt_xmlv1p1":
            if not href:
                print(f"  WARNING: Discussion resource {identifier!r} has no href — skipping")
                continue

            # find the dependency (topicMeta)
            meta_id = None
            for dep in res:
                if _strip_ns(dep.tag) == "dependency":
                    meta_id = dep.get("identifierref")
                    break
            meta_path = None
            if meta_id and meta_id in resource_map:
                meta_path = resource_map[meta_id].get("href", "")

            # get title from topicMeta if available, else from topic XML
            title = ""
            if meta_path:
                title = _title_from_xml_element(imscc_dir / meta_path, "title")
            if not title:
                title = _title_from_xml_element(imscc_dir / href, "title")
            if not title:
                title = identifier

            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="discussion",
                imscc_path=href,
                local_path=f"discussions/{_slugify(title)}.md",
                title=title,
                metadata={"meta_path": meta_path or ""},
            )
            continue

        # --- Assignments (associatedcontent + href into a gXXX/ dir with HTML) ---
        if res_type.startswith("associatedcontent/") and href.endswith(".html"):
            settings_path = imscc_dir / Path(href).parent / "assignment_settings.xml"
            if settings_path.exists():
                title = _title_from_xml_element(settings_path, "title")
                stem = Path(href).stem
                result[identifier] = TempEntry(
                    imscc_id=identifier,
                    category="assignment",
                    imscc_path=href,
                    local_path=f"assignments/{stem}.md",
                    title=title,
                    metadata={"settings_path": str(settings_path.relative_to(imscc_dir))},
                )
                continue

        # --- Quizzes ---
        if res_type.startswith("imsqti_xmlv1p2/"):
            title = _title_from_xml_element(imscc_dir / href, "title") if href else identifier
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="quiz",
                imscc_path=href,
                local_path="",
                title=title,
            )
            continue

        # --- External URLs ---
        if res_type == "imswl_xmlv1p1":
            title = _title_from_xml_element(imscc_dir / href, "title") if href else identifier
            # read the URL from the webLink XML
            url = ""
            try:
                wl_tree = ET.parse(imscc_dir / href)
                for el in wl_tree.getroot().iter():
                    if _strip_ns(el.tag) == "url":
                        url = el.get("href", "")
                        break
            except (OSError, ET.ParseError):
                pass
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="external_url",
                imscc_path=href,
                local_path="",
                title=title,
                metadata={"url": url},
            )
            continue

        # --- LTI tools ---
        if res_type.startswith("imsbasiclti_"):
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="lti",
                imscc_path=href,
                local_path="",
                title=identifier,
            )
            continue

        # --- Unknown / secondary resources ---
        # Skip dependency-only resources silently; warn on others.
        if identifier not in dependency_ids and res_type:
            print(f"  WARNING: Unknown resource type '{res_type}' for {identifier!r} — skipping")

    return result


# ---------------------------------------------------------------------------
# Group 2: Asset copier
# ---------------------------------------------------------------------------

def copy_assets(imscc_dir: Path, output_dir: Path) -> None:
    """Copy web_resources/ → assets/, preserving subdirectory structure."""
    src_root = imscc_dir / "web_resources"
    if not src_root.exists():
        return
    dst_root = output_dir / "assets"
    for src in sorted(src_root.rglob("*")):
        if src.is_file():
            rel = src.relative_to(src_root)
            dst = dst_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"Copying asset: assets/{rel}")


# ---------------------------------------------------------------------------
# Group 3: IMSCC link rewriter
# ---------------------------------------------------------------------------

# Matches $CANVAS_OBJECT_REFERENCE$/type/identifier or $IMS-CC-FILEBASE$/path
_CANVAS_REF_RE = re.compile(
    r'\$CANVAS_OBJECT_REFERENCE\$/([^/]+)/([^"\'?\s]+?)(?:\?[^"\']*)?(?=["\'\s])',
)
_FILEBASE_RE = re.compile(
    r'\$IMS-CC-FILEBASE\$/([^"\'?\s]+?)(?:\?[^"\']*)?(?=["\'\s])',
)

# Map from module_meta content_type path fragment to canonical content dir
_CONTENT_TYPE_TO_DIR: dict[str, str] = {
    "assignments": "assignments",
    "pages": "pages",
    "discussion_topics": "discussions",
    "discussions": "discussions",
    "modules": None,  # can't represent as local file
}


def rewrite_imscc_links(
    html: str,
    temp_manifest: dict[str, TempEntry],
    output_local_path: str,
) -> str:
    """Rewrite $CANVAS_OBJECT_REFERENCE$ and $IMS-CC-FILEBASE$ tokens.

    output_local_path is the relative output path of the file being converted
    (e.g. 'pages/my-page.md').  Used to compute relative '../' prefix.
    """
    depth = len(Path(output_local_path).parts) - 1
    prefix = "../" * depth  # e.g. '../' for files one dir deep

    def _replace_canvas_ref(m: re.Match) -> str:
        content_type = m.group(1)   # e.g. "assignments", "pages"
        imscc_id = m.group(2)       # e.g. "g_assignment_1"

        if content_type == "modules":
            print(f"  WARNING: Cannot rewrite module link to local path: {m.group(0)!r}")
            return m.group(0)  # leave as-is; will become plain text after Pandoc

        entry = temp_manifest.get(imscc_id)
        if entry is None:
            print(f"  WARNING: Unknown IMSCC id {imscc_id!r} in link — removing href")
            return ""

        return f"{prefix}{entry.local_path}"

    def _replace_filebase(m: re.Match) -> str:
        rel_path = unquote(m.group(1))
        return f"{prefix}assets/{rel_path}"

    html = _CANVAS_REF_RE.sub(_replace_canvas_ref, html)
    html = _FILEBASE_RE.sub(_replace_filebase, html)
    return html


# ---------------------------------------------------------------------------
# Group 4: Page converter
# ---------------------------------------------------------------------------

def _extract_html_body(html: str) -> str:
    """Return the contents of <body>...</body>, or the full string if no body tag."""
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else html.strip()


def _html_to_markdown(html: str) -> str:
    return pypandoc.convert_text(
        html,
        to="markdown",
        format="html",
        extra_args=["--wrap=none"],
    )


def convert_page(
    entry: TempEntry,
    imscc_dir: Path,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
) -> None:
    """Convert a wiki_content page HTML file to pages/{stem}.md."""
    html_path = imscc_dir / entry.imscc_path
    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    body_html = _extract_html_body(raw_html)
    body_html = rewrite_imscc_links(body_html, temp_manifest, entry.local_path)
    markdown = _html_to_markdown(body_html)

    frontmatter = _build_frontmatter({"title": entry.title, "published": True})
    out_path = output_dir / entry.local_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + "\n" + markdown + "\n", encoding="utf-8")
    print(f"Converting page: {entry.local_path}")


# ---------------------------------------------------------------------------
# Group 5: Assignment converter
# ---------------------------------------------------------------------------

def parse_assignment_settings(xml_path: Path) -> dict[str, Any]:
    """Extract frontmatter fields from assignment_settings.xml."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    def _text(tag: str) -> str:
        ns_uri = _ns(root)
        el = root.find(f".//{{{ns_uri}}}{tag}") if ns_uri else None
        if el is None:
            el = root.find(f".//{tag}")
        return (el.text or "").strip() if el is not None else ""

    def _ns(el: ET.Element) -> str:
        if el.tag.startswith("{"):
            return el.tag.split("}")[0][1:]
        return ""

    title = _text("title")
    points_raw = _text("points_possible")
    due_at = _text("due_at") or None
    lock_at = _text("lock_at") or None
    unlock_at = _text("unlock_at") or None
    submission_types_raw = _text("submission_types")
    grading_type = _text("grading_type") or None
    workflow = _text("workflow_state")

    points: float | None = None
    if points_raw:
        try:
            points = float(points_raw)
        except ValueError:
            pass

    submission_types: list[str] = (
        [s.strip() for s in submission_types_raw.split(",") if s.strip()]
        if submission_types_raw
        else []
    )

    return {
        "title": title,
        "published": workflow == "published",
        "points_possible": points,
        "due_at": due_at,
        "lock_at": lock_at,
        "unlock_at": unlock_at,
        "submission_types": submission_types,
        "grading_type": grading_type,
    }


def convert_assignment(
    entry: TempEntry,
    imscc_dir: Path,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
) -> None:
    """Convert an assignment HTML + settings XML to assignments/{stem}.md."""
    settings_path = imscc_dir / entry.metadata["settings_path"]
    fm_fields = parse_assignment_settings(settings_path)

    html_path = imscc_dir / entry.imscc_path
    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    body_html = _extract_html_body(raw_html)
    body_html = rewrite_imscc_links(body_html, temp_manifest, entry.local_path)
    markdown = _html_to_markdown(body_html)

    frontmatter = _build_frontmatter(fm_fields)
    out_path = output_dir / entry.local_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + "\n" + markdown + "\n", encoding="utf-8")
    print(f"Converting assignment: {entry.local_path}")


# ---------------------------------------------------------------------------
# Group 6: Discussion converter
# ---------------------------------------------------------------------------

def parse_topic_meta(xml_path: Path) -> dict[str, Any]:
    """Extract frontmatter fields from a topicMeta XML file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    ns = _xml_ns(root)

    def _text(tag: str) -> str:
        el = root.find(f"{{{ns}}}{tag}") if ns else root.find(tag)
        return (el.text or "").strip() if el is not None else ""

    title = _text("title")
    workflow = _text("workflow_state")
    topic_type = _text("type")
    require_initial = _text("require_initial_post")

    result: dict[str, Any] = {
        "title": title,
        "published": workflow == "active",
        "is_announcement": topic_type == "announcement",
        "require_initial_post": require_initial.lower() == "true" if require_initial else False,
    }

    # graded discussion — pick up points/due_at from embedded <assignment>
    assignment_el = (
        root.find(f"{{{ns}}}assignment") if ns else root.find("assignment")
    )
    if assignment_el is not None:
        def _atext(tag: str) -> str:
            el = assignment_el.find(f"{{{ns}}}{tag}") if ns else assignment_el.find(tag)
            return (el.text or "").strip() if el is not None else ""

        pts_raw = _atext("points_possible")
        result["points_possible"] = float(pts_raw) if pts_raw else None
        result["due_at"] = _atext("due_at") or None
        result["lock_at"] = _atext("lock_at") or None
        result["unlock_at"] = _atext("unlock_at") or None

    return result


def _xml_ns(element: ET.Element) -> str:
    """Return the namespace URI of the root element, or empty string."""
    if element.tag.startswith("{"):
        return element.tag.split("}")[0][1:]
    return ""


def convert_discussion(
    entry: TempEntry,
    imscc_dir: Path,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
) -> None:
    """Convert a discussion topic + topicMeta to discussions/{slug}.md."""
    meta_path_str = entry.metadata.get("meta_path", "")
    if not meta_path_str:
        print(f"  WARNING: No topicMeta found for discussion {entry.imscc_id!r} — skipping")
        return

    fm_fields = parse_topic_meta(imscc_dir / meta_path_str)

    if fm_fields.pop("is_announcement", False):
        print(f"  WARNING: Skipping announcement: {entry.title!r}")
        return

    # read and decode the HTML body from the imsdt XML
    topic_tree = ET.parse(imscc_dir / entry.imscc_path)
    topic_root = topic_tree.getroot()
    ns = _xml_ns(topic_root)
    text_el = (
        topic_root.find(f"{{{ns}}}text") if ns else topic_root.find("text")
    )
    body_html = (text_el.text or "") if text_el is not None else ""

    body_html = rewrite_imscc_links(body_html, temp_manifest, entry.local_path)
    markdown = _html_to_markdown(body_html)

    frontmatter = _build_frontmatter(fm_fields)
    out_path = output_dir / entry.local_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + "\n" + markdown + "\n", encoding="utf-8")
    print(f"Converting discussion: {entry.local_path}")


# ---------------------------------------------------------------------------
# Group 7: Module file generator
# ---------------------------------------------------------------------------

@dataclass
class ModuleItem:
    content_type: str
    title: str
    identifier: str        # module item identifier
    identifierref: str     # content resource identifier
    url: str = ""          # for ExternalUrl items
    position: int = 0


@dataclass
class ModuleData:
    identifier: str
    title: str
    published: bool
    require_sequential_progress: bool
    unlock_at: str | None
    position: int
    items: list[ModuleItem]


def parse_module_meta(imscc_dir: Path) -> list[ModuleData]:
    """Parse course_settings/module_meta.xml into a list of ModuleData."""
    meta_path = imscc_dir / "course_settings" / "module_meta.xml"
    if not meta_path.exists():
        return []

    tree = ET.parse(meta_path)
    root = tree.getroot()
    ns = _xml_ns(root)

    def _child_text(parent: ET.Element, tag: str) -> str:
        el = parent.find(f"{{{ns}}}{tag}") if ns else parent.find(tag)
        return (el.text or "").strip() if el is not None else ""

    modules: list[ModuleData] = []
    mod_tag = f"{{{ns}}}module" if ns else "module"
    item_tag = f"{{{ns}}}item" if ns else "item"

    for mod_el in root.findall(mod_tag):
        identifier = mod_el.get("identifier", "")
        title = _child_text(mod_el, "title")
        workflow = _child_text(mod_el, "workflow_state")
        position_raw = _child_text(mod_el, "position")
        seq_raw = _child_text(mod_el, "require_sequential_progress")
        unlock_at = _child_text(mod_el, "unlock_at") or None

        items_el = mod_el.find(f"{{{ns}}}items") if ns else mod_el.find("items")
        items: list[ModuleItem] = []
        if items_el is not None:
            for item_el in items_el.findall(item_tag):
                i_id = item_el.get("identifier", "")
                i_type = _child_text(item_el, "content_type")
                i_title = _child_text(item_el, "title")
                i_ref = _child_text(item_el, "identifierref")
                i_url = _child_text(item_el, "url")
                i_pos_raw = _child_text(item_el, "position")
                items.append(ModuleItem(
                    content_type=i_type,
                    title=i_title,
                    identifier=i_id,
                    identifierref=i_ref,
                    url=i_url,
                    position=int(i_pos_raw) if i_pos_raw.isdigit() else 0,
                ))

        items.sort(key=lambda i: i.position)
        modules.append(ModuleData(
            identifier=identifier,
            title=title,
            published=workflow == "active",
            require_sequential_progress=seq_raw.lower() == "true",
            unlock_at=unlock_at,
            position=int(position_raw) if position_raw.isdigit() else 0,
            items=items,
        ))

    modules.sort(key=lambda m: m.position)
    return modules


def generate_module_file(
    module: ModuleData,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
) -> None:
    """Write a module .md file with frontmatter and item list."""
    fm: dict[str, Any] = {
        "title": module.title,
        "published": module.published,
        "require_sequential_progress": module.require_sequential_progress,
    }
    if module.unlock_at:
        fm["unlock_at"] = module.unlock_at

    lines: list[str] = []
    for item in module.items:
        ct = item.content_type

        if ct == "ContextModuleSubHeader":
            lines.append(f"## {item.title}")
            lines.append("")
            continue

        if ct == "ExternalUrl":
            url = item.url or "#"
            lines.append(f"- [{item.title}]({url})")
            continue

        if ct in ("Quizzes::Quiz", "Attachment"):
            print(
                f"  WARNING: Skipping {ct} item: {item.title!r} "
                f"({item.identifierref}) in module {module.title!r}"
            )
            lines.append(f"# SKIPPED: {ct} - \"{item.title}\" ({item.identifierref})")
            continue

        if ct in ("WikiPage", "Assignment", "Discussion"):
            entry = temp_manifest.get(item.identifierref)
            if entry is None:
                print(
                    f"  WARNING: Module item {item.title!r} references unknown "
                    f"id {item.identifierref!r} — skipping"
                )
                continue
            lines.append(f"- [{item.title}](../{entry.local_path})")
            continue

        # Unknown content type — warn and skip
        print(f"  WARNING: Unknown module item type {ct!r}: {item.title!r} — skipping")

    slug = _slugify(module.title)
    out_path = output_dir / "modules" / f"{slug}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        _build_frontmatter(fm) + "\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f"Generating module: modules/{slug}.md")


# ---------------------------------------------------------------------------
# Group 8: Course settings folder
# ---------------------------------------------------------------------------

def create_course_settings(
    imscc_dir: Path,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
) -> None:
    """Write course_settings/ folder: syllabus.md, course_settings.md, canvas.toml."""
    cs_dir = output_dir / "course_settings"
    cs_dir.mkdir(parents=True, exist_ok=True)

    # syllabus.html → course_settings/syllabus.md
    syllabus_src = imscc_dir / "course_settings" / "syllabus.html"
    if syllabus_src.exists():
        raw_html = syllabus_src.read_text(encoding="utf-8", errors="replace")
        body_html = _extract_html_body(raw_html)
        body_html = rewrite_imscc_links(body_html, temp_manifest, "course_settings/syllabus.md")
        markdown = _html_to_markdown(body_html)
        fm = _build_frontmatter({"title": "Syllabus", "published": True})
        (cs_dir / "syllabus.md").write_text(fm + "\n" + markdown + "\n", encoding="utf-8")
        print("Converting page: course_settings/syllabus.md")

    # course_settings.xml → course_settings/course_settings.md
    cs_xml = imscc_dir / "course_settings" / "course_settings.xml"
    cs_fm = _parse_course_settings_xml(cs_xml)
    (cs_dir / "course_settings.md").write_text(
        _build_frontmatter(cs_fm) + "\n", encoding="utf-8"
    )
    print("Writing: course_settings/course_settings.md")

    # canvas.toml skeleton in repo root
    toml_path = output_dir / "canvas.toml"
    toml_path.write_text(
        "base_url = \"https://yourschool.instructure.com\"\n"
        "course_id = 0  # TODO: set your course ID\n"
        "\n"
        "[auth]\n"
        "# Prefer env var CANVAS_API_TOKEN; this is a fallback for local use only\n"
        "api_token = \"\"\n",
        encoding="utf-8",
    )
    print("Writing: canvas.toml")


def _parse_course_settings_xml(xml_path: Path) -> dict[str, Any]:
    """Extract key fields from course_settings.xml for course_settings.md frontmatter."""
    if not xml_path.exists():
        return {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = _xml_ns(root)

        def _text(tag: str) -> str:
            el = root.find(f"{{{ns}}}{tag}") if ns else root.find(tag)
            return (el.text or "").strip() if el is not None else ""

        result: dict[str, Any] = {}
        for field in ("title", "course_code", "start_at", "conclude_at", "default_view"):
            val = _text(field)
            if val:
                result[field] = val
        return result
    except (OSError, ET.ParseError):
        return {}


# ---------------------------------------------------------------------------
# Frontmatter helper
# ---------------------------------------------------------------------------

def _build_frontmatter(fields: dict[str, Any]) -> str:
    """Render a YAML frontmatter block, omitting None values."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, list):
            items = ", ".join(str(v) for v in value)
            lines.append(f"{key}: [{items}]")
        else:
            # Quote strings that contain special YAML characters
            s = str(value)
            if any(c in s for c in ':#{}[]|>&*!,?') or s.startswith('"'):
                lines.append(f'{key}: "{s}"')
            else:
                lines.append(f"{key}: {s}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Group 9: Orchestrator
# ---------------------------------------------------------------------------

def run_import(imscc_path: Path, output_dir: Path) -> None:
    """Run the full import pipeline from an IMSCC file or directory."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    imscc_dir, tmp = open_imscc(imscc_path)
    try:
        if tmp is not None:
            print(f"Extracting: {imscc_path.name} → {imscc_dir}")

        print("Parsing IMSCC manifest...")
        temp_manifest = parse_imsmanifest(imscc_dir)

        # Phase 2: assets
        copy_assets(imscc_dir, output_dir)

        # Phase 3: pages
        for entry in temp_manifest.values():
            if entry.category == "page":
                convert_page(entry, imscc_dir, temp_manifest, output_dir)

        # Phase 4: assignments
        for entry in temp_manifest.values():
            if entry.category == "assignment":
                convert_assignment(entry, imscc_dir, temp_manifest, output_dir)

        # Phase 5: discussions
        for entry in temp_manifest.values():
            if entry.category == "discussion":
                convert_discussion(entry, imscc_dir, temp_manifest, output_dir)

        # Phase 6: modules
        modules = parse_module_meta(imscc_dir)
        for module in modules:
            generate_module_file(module, temp_manifest, output_dir)

        # Phase 7: course settings
        create_course_settings(imscc_dir, temp_manifest, output_dir)

        print(f"\nDone. Wrote course repo to: {output_dir}")
    finally:
        if tmp is not None:
            tmp.cleanup()
