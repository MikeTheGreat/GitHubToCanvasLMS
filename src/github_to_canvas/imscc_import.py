"""Import a Canvas course from a local .imscc file into a Markdown repo."""
from __future__ import annotations

import json
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
import tomli_w

from .canvas_api import NUMERIC_TAB_IDS, TOOL_TAB_PREFIX


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

        # --- QTI objectbank files (Canvas question banks) ---
        # non_cc_assessments/ files may be objectbanks (question pools) or quiz-linked
        # QTI files. Read the file and check the root child to distinguish them.
        if res_type.startswith("associatedcontent/") and href.startswith("non_cc_assessments/"):
            bank_title = ""
            is_objectbank = False
            try:
                ob_tree = ET.parse(imscc_dir / href)
                ob_root = ob_tree.getroot()
                first_child = next(
                    (c for c in ob_root if _strip_ns(c.tag) in ("objectbank", "assessment")),
                    None,
                )
                if first_child is not None and _strip_ns(first_child.tag) == "objectbank":
                    is_objectbank = True
                    for field_el in first_child.iter():
                        if _strip_ns(field_el.tag) != "qtimetadatafield":
                            continue
                        lbl = next((c for c in field_el if _strip_ns(c.tag) == "fieldlabel"), None)
                        ent = next((c for c in field_el if _strip_ns(c.tag) == "fieldentry"), None)
                        if lbl is not None and ent is not None:
                            if (lbl.text or "").strip() == "bank_title":
                                bank_title = (ent.text or "").strip()
                                break
            except (OSError, ET.ParseError):
                pass
            if is_objectbank:
                slug = _slugify(bank_title) if bank_title else _slugify(identifier)
                result[identifier] = TempEntry(
                    imscc_id=identifier,
                    category="question_bank",
                    imscc_path=href,
                    local_path=f"question_banks/{slug}/{slug}.toml",
                    title=bank_title or identifier,
                )
            continue

        # --- Course settings root resource (no html href) ---
        if res_type.startswith("associatedcontent/") and not href.endswith(".html") and not href.endswith(".xml"):
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="course_settings",
                imscc_path=href,
                local_path="course_settings/course_settings.toml",
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
            file_hrefs = [
                f_el.get("href", "")
                for f_el in res
                if _strip_ns(f_el.tag) == "file"
            ]
            meta_href = href  # standard format: href points directly to assessment_meta.xml

            # Canvas export format: href="" and assessment_meta.xml is in a <dependency>
            if not meta_href:
                for dep_el in res:
                    if _strip_ns(dep_el.tag) == "dependency":
                        dep_id = dep_el.get("identifierref", "")
                        dep_res = resource_map.get(dep_id)
                        if dep_res is not None:
                            dep_href = dep_res.get("href", "")
                            if dep_href.endswith("assessment_meta.xml"):
                                meta_href = dep_href
                                # Gather dependency file hrefs (.xml.qti has richer metadata)
                                for f_el in dep_res:
                                    if _strip_ns(f_el.tag) == "file":
                                        f_href = f_el.get("href", "")
                                        if f_href and f_href not in file_hrefs:
                                            file_hrefs.append(f_href)
                                break

            title = _title_from_xml_element(imscc_dir / meta_href, "title") if meta_href else identifier
            slug = _slugify(title) if title else identifier

            # Prefer non-CC QTI (.xml.qti) which has question_type and points_possible labels;
            # fall back to any .xml that isn't the meta file.
            qti_path = next((h for h in file_hrefs if h.endswith(".xml.qti")), None)
            if qti_path is None:
                qti_path = next(
                    (h for h in file_hrefs if h != meta_href and h.endswith(".xml")),
                    "",
                )

            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="quiz",
                imscc_path=meta_href,
                local_path=f"quizzes/{slug}/{slug}.md",
                title=title,
                metadata={"meta_path": meta_href, "qti_path": qti_path},
            )
            continue

        # --- External URLs ---
        if res_type == "imswl_xmlv1p1":
            title = _title_from_xml_element(imscc_dir / href, "title") if href else identifier
            # read the URL, target, and windowFeatures from the webLink XML (spec §4.8)
            url = ""
            target = ""
            window_features = ""
            try:
                wl_tree = ET.parse(imscc_dir / href)
                for el in wl_tree.getroot().iter():
                    if _strip_ns(el.tag) == "url":
                        url = el.get("href", "")
                        target = el.get("target", "")
                        window_features = el.get("windowFeatures", "")
                        break
            except (OSError, ET.ParseError):
                pass
            meta: dict[str, Any] = {"url": url}
            if target:
                meta["target"] = target
            if window_features:
                meta["window_features"] = window_features
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="external_url",
                imscc_path=href,
                local_path="",
                title=title,
                metadata=meta,
            )
            continue

        # --- LTI tools ---
        if res_type.startswith("imsbasiclti_"):
            # Canvas exports set href="" on LTI resources; the actual file is in <file> children.
            file_hrefs = [
                f_el.get("href", "")
                for f_el in res
                if _strip_ns(f_el.tag) == "file"
            ]
            result[identifier] = TempEntry(
                imscc_id=identifier,
                category="lti",
                imscc_path=file_hrefs[0] if file_hrefs else href,
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
# Canvas placeholder tokens used in navigation link hrefs
_CANVAS_COURSE_ID_TOKEN_RE = re.compile(r'\$CANVAS_COURSE_ID\$')
_CANVAS_COURSE_REFERENCE_TOKEN_RE = re.compile(r'\$CANVAS_COURSE_REFERENCE\$')

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
    course_id: int | str | None = None,
    base_url: str | None = None,
) -> str:
    """Rewrite $CANVAS_OBJECT_REFERENCE$, $IMS-CC-FILEBASE$, and Canvas course tokens.

    output_local_path is the relative output path of the file being converted
    (e.g. 'pages/my-page.md').  Used to compute relative '../' prefix.

    Canvas embeds two placeholder tokens in navigation link hrefs during IMSCC export:
    - $CANVAS_COURSE_REFERENCE$ — the full course base URL; replaced with base_url.
    - $CANVAS_COURSE_ID$        — the numeric course ID; replaced with course_id.

    After replacement the resulting full URLs are matched by
    _replace_canvas_course_url_in_md_files which converts them to snippet references.
    """
    # $CANVAS_COURSE_REFERENCE$ → full base URL (replace first so $CANVAS_COURSE_ID$
    # inside the replacement can be handled on the same pass if needed)
    if base_url is not None:
        html = _CANVAS_COURSE_REFERENCE_TOKEN_RE.sub(base_url, html)
    # $CANVAS_COURSE_ID$ → bare numeric course ID
    if course_id is not None:
        html = _CANVAS_COURSE_ID_TOKEN_RE.sub(str(course_id), html)

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

# Attributes Canvas's RCE injects on <img> tags that have no meaning outside Canvas
_IMG_TAG_RE = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE | re.DOTALL)
_CANVAS_IMG_ATTRS_RE = re.compile(
    r'\s+(?:api-endpoint|api-returntype|loading|data-api-endpoint|data-api-returntype)'
    r'(?:="[^"]*"|=\'[^\']*\')?',
    re.IGNORECASE,
)


def _strip_canvas_img_attrs(html: str) -> str:
    """Remove Canvas-internal img attributes before Pandoc sees the HTML."""
    def _clean(m: re.Match) -> str:
        return _CANVAS_IMG_ATTRS_RE.sub("", m.group(0))
    return _IMG_TAG_RE.sub(_clean, html)


def _extract_html_body(html: str) -> str:
    """Return the contents of <body>...</body>, or the full string if no body tag."""
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else html.strip()


def _html_to_markdown(html: str) -> str:
    html = _strip_canvas_img_attrs(html)
    return pypandoc.convert_text(
        html,
        to="markdown",
        format="html",
        extra_args=["--wrap=none"],
    )


_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)


def _shift_headings_down(markdown: str, context: str = "") -> str:
    """Increase every ATX heading level by one (H1→H2, …, H5→H6, H6→H6).

    Canvas LMS silently converts H1 to a styled paragraph, so imported
    content should start at H2.  Existing H6 headings cannot be shifted
    further; a warning is printed and they are left at H6.
    """
    has_h6 = False

    def _bump(m: re.Match) -> str:
        nonlocal has_h6
        hashes = m.group(1)
        if len(hashes) >= 6:
            has_h6 = True
            return m.group(0)
        return "#" + hashes + " "

    result = _ATX_HEADING_RE.sub(_bump, markdown)
    if has_h6:
        label = f" in {context}" if context else ""
        print(f"  WARNING: H6 heading found{label}; cannot shift deeper — left at H6")
    return result


def convert_page(
    entry: TempEntry,
    imscc_dir: Path,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
    course_id: int | str | None = None,
    base_url: str | None = None,
) -> None:
    """Convert a wiki_content page HTML file to pages/{stem}.md."""
    html_path = imscc_dir / entry.imscc_path
    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    body_html = _extract_html_body(raw_html)
    body_html = rewrite_imscc_links(body_html, temp_manifest, entry.local_path, course_id, base_url)
    markdown = _html_to_markdown(body_html)
    markdown = _shift_headings_down(markdown, entry.local_path)

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

    def _ns(el: ET.Element) -> str:
        if el.tag.startswith("{"):
            return el.tag.split("}")[0][1:]
        return ""

    def _text(tag: str) -> str:
        ns_uri = _ns(root)
        el = root.find(f".//{{{ns_uri}}}{tag}") if ns_uri else None
        if el is None:
            el = root.find(f".//{tag}")
        return (el.text or "").strip() if el is not None else ""

    def _bool(tag: str) -> bool | None:
        raw = _text(tag)
        if raw.lower() == "true":
            return True
        if raw.lower() == "false":
            return False
        return None

    def _int(tag: str) -> int | None:
        raw = _text(tag)
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

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

    result: dict[str, Any] = {
        "title": title,
        "published": workflow == "published",
        "points_possible": points,
        "due_at": due_at,
        "lock_at": lock_at,
        "unlock_at": unlock_at,
        "submission_types": submission_types,
        "grading_type": grading_type,
    }

    # Group assignment
    group_cat = _int("group_category_id")
    if group_cat:
        result["group_category_id"] = group_cat
    for bool_tag in ("grade_group_students_individually",):
        val = _bool(bool_tag)
        if val:
            result[bool_tag] = val

    # Anonymous / moderated grading
    for bool_tag in ("anonymous_grading", "moderated_grading",
                     "grader_comments_visible_to_graders",
                     "graders_anonymous_to_graders",
                     "grader_names_visible_to_final_grader"):
        val = _bool(bool_tag)
        if val:
            result[bool_tag] = val
    grader_count = _int("grader_count")
    if grader_count:
        result["grader_count"] = grader_count
    final_grader = _int("final_grader_id")
    if final_grader:
        result["final_grader_id"] = final_grader

    # Peer reviews
    for bool_tag in ("peer_reviews", "automatic_peer_reviews",
                     "anonymous_peer_reviews", "intra_group_peer_reviews"):
        val = _bool(bool_tag)
        if val:
            result[bool_tag] = val
    peer_count = _int("peer_review_count")
    if peer_count:
        result["peer_review_count"] = peer_count
    peer_assign_at = _text("peer_reviews_assign_at") or None
    if peer_assign_at:
        result["peer_reviews_assign_at"] = peer_assign_at

    return result


def convert_assignment(
    entry: TempEntry,
    imscc_dir: Path,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
    course_id: int | str | None = None,
    base_url: str | None = None,
) -> None:
    """Convert an assignment HTML + settings XML to assignments/{stem}.md."""
    settings_path = imscc_dir / entry.metadata["settings_path"]
    fm_fields = parse_assignment_settings(settings_path)

    html_path = imscc_dir / entry.imscc_path
    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    body_html = _extract_html_body(raw_html)
    body_html = rewrite_imscc_links(body_html, temp_manifest, entry.local_path, course_id, base_url)
    markdown = _html_to_markdown(body_html)
    markdown = _shift_headings_down(markdown, entry.local_path)

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
    course_id: int | str | None = None,
    base_url: str | None = None,
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

    body_html = rewrite_imscc_links(body_html, temp_manifest, entry.local_path, course_id, base_url)
    markdown = _html_to_markdown(body_html)
    markdown = _shift_headings_down(markdown, entry.local_path)

    # Extract attachments (spec §4.7)
    attach_el = (
        topic_root.find(f"{{{ns}}}attachments") if ns else topic_root.find("attachments")
    )
    if attach_el is not None:
        attach_tag = f"{{{ns}}}attachment" if ns else "attachment"
        hrefs = [a.get("href", "") for a in attach_el.findall(attach_tag) if a.get("href")]
        if hrefs:
            depth = len(Path(entry.local_path).parts) - 1
            rel_prefix = "../" * depth
            lines = [markdown.rstrip(), "", "## Attachments", ""]
            for href in hrefs:
                filename = Path(href).name
                lines.append(f"- [{filename}]({rel_prefix}assets/{href})")
            markdown = "\n".join(lines)

    frontmatter = _build_frontmatter(fm_fields)
    out_path = output_dir / entry.local_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + "\n" + markdown + "\n", encoding="utf-8")
    print(f"Converting discussion: {entry.local_path}")


# ---------------------------------------------------------------------------
# Group 6b: Quiz converter
# ---------------------------------------------------------------------------

_NS_CANVAS_QUIZ = "http://canvas.instructure.com/xsd/cccv1p0"
_NS_QTI = "http://www.imsglobal.org/xsd/ims_qtiasiv1p2"


def parse_quiz_meta(meta_path: Path) -> tuple[dict[str, Any], str]:
    """Extract frontmatter fields from assessment_meta.xml."""
    if not meta_path.exists():
        return {}, ""
    try:
        tree = ET.parse(meta_path)
        root = tree.getroot()
        ns = _xml_ns(root)

        def _text(tag: str) -> str:
            el = root.find(f"{{{ns}}}{tag}") if ns else root.find(tag)
            return (el.text or "").strip() if el is not None else ""

        title = _text("title")
        workflow = _text("workflow_state")
        quiz_type = _text("quiz_type") or "assignment"
        pts_raw = _text("points_possible")
        time_raw = _text("time_limit")
        attempts_raw = _text("allowed_attempts")
        shuffle_raw = _text("shuffle_answers")
        show_correct_raw = _text("show_correct_answers")
        desc_raw = _text("description")

        result: dict[str, Any] = {
            "title": title,
            "published": workflow == "published",
            "quiz_type": quiz_type,
        }
        if pts_raw:
            try:
                result["points_possible"] = float(pts_raw)
            except ValueError:
                pass
        if time_raw:
            try:
                result["time_limit"] = int(time_raw)
            except ValueError:
                pass
        if attempts_raw:
            try:
                result["allowed_attempts"] = int(attempts_raw)
            except ValueError:
                pass
        if shuffle_raw:
            result["shuffle_answers"] = shuffle_raw.lower() == "true"
        if show_correct_raw:
            result["show_correct_answers"] = show_correct_raw.lower() == "true"
        return result, (desc_raw or "")
    except (OSError, ET.ParseError):
        return {}, ""


def _qti_text(el: ET.Element, ns: str) -> str:
    """Get text content from a QTI mattext element, stripping the namespace."""
    for mat in el.iter():
        if _strip_ns(mat.tag) == "mattext" and mat.text:
            return mat.text.strip()
    return ""


_CC_PROFILE_MAP: dict[str, str] = {
    "cc.multiple_choice.v0p1": "multiple_choice_question",
    "cc.true_false.v0p1": "true_false_question",
    "cc.essay.v0p1": "essay_question",
    "cc.multiple_response.v0p1": "multiple_response_question",
    "cc.fib.v0p1": "fill_in_blank_question",
    "cc.pattern_match.v0p1": "pattern_match_question",
}

_SUPPORTED_QUESTION_TYPES = frozenset({
    "multiple_choice_question",
    "true_false_question",
    "essay_question",
    "multiple_response_question",
    "fill_in_blank_question",
    "pattern_match_question",
})


def _parse_qti_items(root_el: ET.Element) -> list[dict[str, Any]]:
    """Parse all <item> elements under root_el and return question dicts.

    Handles multiple_choice, true_false, essay, multiple_response,
    fill_in_blank, pattern_match question types; extracts itemfeedback,
    sample solutions, and original_answer_ids.
    """
    questions: list[dict[str, Any]] = []

    for item_el in root_el.iter():
        if _strip_ns(item_el.tag) != "item":
            continue

        title = item_el.get("title", "")

        question_type = "essay_question"
        points: float = 0.0
        original_answer_ids: list[int] = []

        for field_el in item_el.iter():
            if _strip_ns(field_el.tag) != "qtimetadatafield":
                continue
            label_el = next(
                (c for c in field_el if _strip_ns(c.tag) == "fieldlabel"), None
            )
            entry_el = next(
                (c for c in field_el if _strip_ns(c.tag) == "fieldentry"), None
            )
            if label_el is None or entry_el is None:
                continue
            label = (label_el.text or "").strip()
            entry = (entry_el.text or "").strip()
            if label == "question_type":
                question_type = entry
            elif label == "cc_profile":
                question_type = _CC_PROFILE_MAP.get(entry, question_type)
            elif label == "points_possible":
                try:
                    points = float(entry)
                except ValueError:
                    pass
            elif label == "original_answer_ids" and entry:
                for id_str in entry.split(","):
                    try:
                        original_answer_ids.append(int(id_str.strip()))
                    except ValueError:
                        pass

        if question_type not in _SUPPORTED_QUESTION_TYPES:
            print(f"  WARNING: Skipping unsupported question type {question_type!r}: {title!r}")
            continue

        # Question text (from <presentation><material>)
        question_text = ""
        for pres_el in item_el:
            if _strip_ns(pres_el.tag) != "presentation":
                continue
            for mat_el in pres_el:
                if _strip_ns(mat_el.tag) == "material":
                    question_text = _qti_text(mat_el, "")
                    break
            break

        # Type-specific answer extraction
        answers: list[tuple[str, str]] = []
        correct_ident: str = ""
        correct_idents: list[str] = []
        fib_answers: list[str] = []
        match_type: str = ""

        if question_type in ("multiple_choice_question", "true_false_question"):
            for rl_el in item_el.iter():
                if _strip_ns(rl_el.tag) == "render_choice":
                    for label_el in rl_el:
                        if _strip_ns(label_el.tag) == "response_label":
                            ident = label_el.get("ident", "")
                            text = _qti_text(label_el, "")
                            answers.append((ident, text))
            for cond_el in item_el.iter():
                if _strip_ns(cond_el.tag) == "varequal":
                    correct_ident = (cond_el.text or "").strip()
                    break

        elif question_type == "multiple_response_question":
            for rl_el in item_el.iter():
                if _strip_ns(rl_el.tag) == "render_choice":
                    for label_el in rl_el:
                        if _strip_ns(label_el.tag) == "response_label":
                            ident = label_el.get("ident", "")
                            text = _qti_text(label_el, "")
                            answers.append((ident, text))
            # Correct idents are direct <varequal> children of the <and> block
            for cond_el in item_el.iter():
                if _strip_ns(cond_el.tag) == "and":
                    for child in cond_el:
                        if _strip_ns(child.tag) == "varequal":
                            ident = (child.text or "").strip()
                            if ident:
                                correct_idents.append(ident)
                    break

        elif question_type == "fill_in_blank_question":
            for cond_el in item_el.iter():
                if _strip_ns(cond_el.tag) == "varequal":
                    ans = (cond_el.text or "").strip()
                    if ans:
                        fib_answers.append(ans)

        elif question_type == "pattern_match_question":
            match_type = "substring"
            for cond_el in item_el.iter():
                if _strip_ns(cond_el.tag) == "varsubstring":
                    pat = (cond_el.text or "").strip()
                    if pat:
                        fib_answers.append(pat)

        # Feedback extraction from <itemfeedback> elements (spec §4.10.7)
        feedback: dict[str, str] = {}
        solution: str = ""
        for fb_el in item_el:
            if _strip_ns(fb_el.tag) != "itemfeedback":
                continue
            fb_ident = fb_el.get("ident", "")
            if not fb_ident:
                continue
            if fb_ident == "solution":
                fb_text = _qti_text(fb_el, "")
                if fb_text:
                    solution = fb_text
            else:
                fb_text = _qti_text(fb_el, "")
                if fb_text:
                    feedback[fb_ident] = fb_text

        q_slug = _slugify(title) if title else f"question-{len(questions) + 1}"

        questions.append({
            "title": title,
            "question_type": question_type,
            "points_possible": points,
            "question_text": question_text,
            "answers": answers,
            "correct_ident": correct_ident,
            "correct_idents": correct_idents,
            "fib_answers": fib_answers,
            "match_type": match_type,
            "original_answer_ids": original_answer_ids,
            "feedback": feedback,
            "solution": solution,
            "slug": q_slug,
        })

    return questions


def parse_qti_questions(qti_path: Path) -> list[dict[str, Any]]:
    """Parse a QTI 1.2 XML file and return a list of question dicts."""
    if not qti_path.exists():
        return []
    try:
        tree = ET.parse(qti_path)
        root = tree.getroot()
    except (OSError, ET.ParseError) as e:
        print(f"  WARNING: Could not parse QTI file {qti_path}: {e}")
        return []
    return _parse_qti_items(root)


def _write_question_file(q: dict[str, Any], q_path: Path) -> None:
    """Write a single question Markdown file."""
    q_path.parent.mkdir(parents=True, exist_ok=True)
    qt = q["question_type"]
    answers: list[tuple[str, str]] = q.get("answers", [])
    correct_ident: str = q.get("correct_ident", "")
    feedback: dict[str, str] = q.get("feedback", {})
    solution: str = q.get("solution", "")

    fm_fields: dict[str, Any] = {
        "title": q["title"],
        "question_type": qt,
        "points_possible": q["points_possible"],
    }

    orig_ids = q.get("original_answer_ids")
    if orig_ids:
        fm_fields["original_answer_ids"] = orig_ids

    body_lines: list[str] = []

    if qt == "true_false_question":
        correct_val: Any = None
        for ident, text in answers:
            if ident == correct_ident:
                correct_val = text.lower() == "true"
                break
        if correct_val is None and correct_ident:
            correct_val = True
        fm_fields["correct"] = correct_val
        body_lines.append(q["question_text"] or "")

    elif qt == "multiple_choice_question":
        correct_idx: int | None = None
        for i, (ident, _) in enumerate(answers, start=1):
            if ident == correct_ident:
                correct_idx = i
                break
        fm_fields["correct"] = correct_idx
        body_lines.append(q["question_text"] or "")
        body_lines.append("")
        body_lines.append("## Answers")
        body_lines.append("")
        for _, text in answers:
            body_lines.append(f"1. {text}")

    elif qt == "multiple_response_question":
        correct_idents_set = set(q.get("correct_idents", []))
        correct_indices = [
            i for i, (ident, _) in enumerate(answers, start=1)
            if ident in correct_idents_set
        ]
        fm_fields["correct"] = correct_indices
        body_lines.append(q["question_text"] or "")
        body_lines.append("")
        body_lines.append("## Answers")
        body_lines.append("")
        for _, text in answers:
            body_lines.append(f"1. {text}")

    elif qt == "fill_in_blank_question":
        fm_fields["answers"] = q.get("fib_answers", [])
        body_lines.append(q["question_text"] or "")

    elif qt == "pattern_match_question":
        fm_fields["answers"] = q.get("fib_answers", [])
        fm_fields["match_type"] = "substring"
        body_lines.append(q["question_text"] or "")

    else:
        # essay
        body_lines.append(q["question_text"] or "")

    # Feedback section (spec §4.10.7)
    if feedback:
        body_lines.extend(["", "## Feedback", ""])
        if "general_fb" in feedback:
            body_lines.extend(["### General", "", feedback["general_fb"], ""])
        if "correct_fb" in feedback:
            body_lines.extend(["### Correct", "", feedback["correct_fb"], ""])
        if "general_incorrect_fb" in feedback:
            body_lines.extend(["### Incorrect", "", feedback["general_incorrect_fb"], ""])
        per_answer = {
            k: v for k, v in feedback.items()
            if k not in ("general_fb", "correct_fb", "general_incorrect_fb")
        }
        if per_answer and answers:
            body_lines.append("### Per-answer")
            body_lines.append("")
            for i, (ident, _) in enumerate(answers, start=1):
                fb_key = f"{ident}_fb"
                if fb_key in per_answer:
                    body_lines.append(f"- answer {i}: {per_answer[fb_key]}")

    # Sample solution for essay questions (spec §4.10.11.2)
    if solution:
        body_lines.extend(["", "## Sample Solution", "", solution])

    fm = _build_frontmatter(fm_fields)
    body = "\n".join(body_lines).strip()
    q_path.write_text(fm + "\n\n" + body + "\n", encoding="utf-8")


def convert_quiz(
    entry: TempEntry,
    imscc_dir: Path,
    output_dir: Path,
) -> None:
    """Convert assessment_meta.xml + QTI questions file to quizzes/{slug}/ folder."""
    meta_path_str = entry.metadata.get("meta_path", "")
    qti_path_str = entry.metadata.get("qti_path", "")

    meta_path = imscc_dir / meta_path_str if meta_path_str else None
    qti_path = imscc_dir / qti_path_str if qti_path_str else None

    fm_fields, description = parse_quiz_meta(meta_path) if meta_path else ({}, "")
    if not fm_fields:
        fm_fields = {"title": entry.title, "published": False, "quiz_type": "assignment"}

    questions = parse_qti_questions(qti_path) if qti_path else []

    # Derive slug from the local_path (e.g. "quizzes/a-quiz/a-quiz.md" → "a-quiz")
    slug = Path(entry.local_path).stem

    quiz_dir = output_dir / "quizzes" / slug
    quiz_dir.mkdir(parents=True, exist_ok=True)

    # Write question files
    q_links: list[str] = []
    for i, q in enumerate(questions, start=1):
        q_filename = q["slug"] + ".md"
        q_path = quiz_dir / "questions" / q_filename
        _write_question_file(q, q_path)
        q_links.append(f"{i}. [{q['title']}](questions/{q_filename})")
        print(f"  Converting question: quizzes/{slug}/questions/{q_filename}")

    # Write quiz-level file
    quiz_md_path = quiz_dir / f"{slug}.md"
    desc_block = (description.strip() + "\n\n") if description.strip() else ""
    body = desc_block + "\n".join(q_links) + "\n"
    quiz_md_path.write_text(_build_frontmatter(fm_fields) + "\n\n" + body, encoding="utf-8")
    print(f"Converting quiz: {entry.local_path}")


# ---------------------------------------------------------------------------
# Group 6c: Question bank converter
# ---------------------------------------------------------------------------

def convert_question_bank(
    entry: TempEntry,
    imscc_dir: Path,
    output_dir: Path,
) -> None:
    """Convert a QTI objectbank file to question_banks/{slug}/ folder."""
    qti_path = imscc_dir / entry.imscc_path
    try:
        tree = ET.parse(qti_path)
        root_el = tree.getroot()
    except (OSError, ET.ParseError) as e:
        print(f"  WARNING: Could not parse question bank {qti_path}: {e}")
        return

    bank_el = next(
        (c for c in root_el if _strip_ns(c.tag) == "objectbank"), None
    )
    if bank_el is None:
        return

    # Parse bank metadata from <qtimetadata>
    bank_meta: dict[str, Any] = {}
    meta_el = next((c for c in bank_el if _strip_ns(c.tag) == "qtimetadata"), None)
    if meta_el is not None:
        for mf_el in meta_el:
            if _strip_ns(mf_el.tag) != "qtimetadatafield":
                continue
            lbl = next((c for c in mf_el if _strip_ns(c.tag) == "fieldlabel"), None)
            ent = next((c for c in mf_el if _strip_ns(c.tag) == "fieldentry"), None)
            if lbl is not None and ent is not None:
                key = (lbl.text or "").strip()
                val = (ent.text or "").strip()
                if key in ("bank_title", "bank_context_uuid", "bank_state") and val:
                    bank_meta[key] = val

    questions = _parse_qti_items(bank_el)

    slug = Path(entry.local_path).stem
    bank_dir = output_dir / "question_banks" / slug
    bank_dir.mkdir(parents=True, exist_ok=True)

    (bank_dir / f"{slug}.toml").write_text(
        tomli_w.dumps(bank_meta), encoding="utf-8"
    )

    for q in questions:
        q_filename = q["slug"] + ".md"
        q_path = bank_dir / "questions" / q_filename
        _write_question_file(q, q_path)
        print(f"  Converting question: question_banks/{slug}/questions/{q_filename}")

    print(f"Converting question bank: question_banks/{slug}/{slug}.toml")


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

        if ct in ("ExternalUrl", "ContextExternalTool"):
            url = item.url or "#"
            extra = ""
            if ct == "ExternalUrl":
                entry = temp_manifest.get(item.identifierref)
                if entry is not None and entry.category == "external_url":
                    parts: list[str] = []
                    if entry.metadata.get("target"):
                        parts.append(f'target="{entry.metadata["target"]}"')
                    if entry.metadata.get("window_features"):
                        parts.append(f'windowFeatures="{entry.metadata["window_features"]}"')
                    if parts:
                        extra = " <!-- " + " ".join(parts) + " -->"
            lines.append(f"- [{item.title}]({url}){extra}")
            continue

        if ct == "Attachment":
            print(
                f"  WARNING: Skipping {ct} item: {item.title!r} "
                f"({item.identifierref}) in module {module.title!r}"
            )
            lines.append(f"# SKIPPED: {ct} - \"{item.title}\" ({item.identifierref})")
            continue

        if ct in ("WikiPage", "Assignment", "Discussion", "DiscussionTopic", "Quizzes::Quiz"):
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

_NS_LOMIMSCC = "http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest"


def _el_text(parent: ET.Element, tag: str, ns: str = "") -> str:
    """Get text of a direct child element, with optional namespace."""
    el = parent.find(f"{{{ns}}}{tag}") if ns else parent.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _parse_manifest_metadata(manifest_path: Path) -> dict[str, str]:
    """Extract last_modified and copyright info from imsmanifest.xml lom metadata."""
    result: dict[str, str] = {}
    try:
        tree = ET.parse(manifest_path)
        root = tree.getroot()
        ns = _NS_LOMIMSCC

        dt_el = root.find(f".//{{{ns}}}dateTime")
        if dt_el is not None and dt_el.text:
            result["last_modified"] = dt_el.text.strip()

        cr_el = root.find(f".//{{{ns}}}copyrightAndOtherRestrictions/{{{ns}}}value")
        if cr_el is not None and cr_el.text:
            result["copyright_restrictions"] = cr_el.text.strip()

        desc_el = root.find(f".//{{{ns}}}rights/{{{ns}}}description/{{{ns}}}string")
        if desc_el is not None and desc_el.text:
            result["copyright_description"] = desc_el.text.strip()
    except (OSError, ET.ParseError):
        pass
    return result


def _coerce_xml_value(text: str) -> Any:
    """Convert an XML text value to bool, int, float, or str."""
    if not text:
        return None
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_course_settings_full(xml_path: Path) -> dict[str, Any]:
    """Extract all fields from course_settings.xml."""
    if not xml_path.exists():
        return {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = _xml_ns(root)

        result: dict[str, Any] = {}
        for child in root:
            tag = _strip_ns(child.tag)

            if tag == "default_post_policy":
                sub: dict[str, Any] = {}
                for sub_child in child:
                    sub_tag = _strip_ns(sub_child.tag)
                    val = _coerce_xml_value((sub_child.text or "").strip())
                    if val is not None:
                        sub[sub_tag] = val
                if sub:
                    result[tag] = sub
                continue

            text = (child.text or "").strip()
            val = _coerce_xml_value(text)
            if val is not None:
                result[tag] = val

        return result
    except (OSError, ET.ParseError):
        return {}


def _parse_grading_standards(xml_path: Path) -> list[dict[str, Any]]:
    """Parse grading_standards.xml into a list of grading standard dicts."""
    if not xml_path.exists():
        return []
    standards: list[dict[str, Any]] = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = _xml_ns(root)
        gs_tag = f"{{{ns}}}gradingStandard" if ns else "gradingStandard"

        for gs_el in root.findall(gs_tag):
            title = _el_text(gs_el, "title", ns)
            data_raw = _el_text(gs_el, "data", ns)
            points_based_raw = _el_text(gs_el, "points_based", ns)
            scaling_raw = _el_text(gs_el, "scaling_factor", ns)

            gs: dict[str, Any] = {"title": title}
            if data_raw:
                try:
                    gs["data"] = json.loads(data_raw)
                except json.JSONDecodeError:
                    gs["data"] = data_raw
            if points_based_raw:
                gs["points_based"] = points_based_raw.lower() == "true"
            if scaling_raw:
                try:
                    gs["scaling_factor"] = float(scaling_raw)
                except ValueError:
                    pass
            standards.append(gs)
    except (OSError, ET.ParseError):
        pass
    return standards


def _parse_assignment_groups(xml_path: Path) -> list[dict[str, Any]]:
    """Parse assignment_groups.xml into a list of assignment group dicts."""
    if not xml_path.exists():
        return []
    groups: list[dict[str, Any]] = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = _xml_ns(root)
        ag_tag = f"{{{ns}}}assignmentGroup" if ns else "assignmentGroup"

        for ag_el in root.findall(ag_tag):
            g: dict[str, Any] = {"title": _el_text(ag_el, "title", ns)}
            pos_raw = _el_text(ag_el, "position", ns)
            if pos_raw.isdigit():
                g["position"] = int(pos_raw)
            wt_raw = _el_text(ag_el, "group_weight", ns)
            if wt_raw:
                try:
                    g["group_weight"] = float(wt_raw)
                except ValueError:
                    pass

            rules_el = ag_el.find(f"{{{ns}}}rules") if ns else ag_el.find("rules")
            if rules_el is not None:
                rules: list[dict[str, Any]] = []
                rule_tag = f"{{{ns}}}rule" if ns else "rule"
                for rule_el in rules_el.findall(rule_tag):
                    rule: dict[str, Any] = {}
                    dt = _el_text(rule_el, "drop_type", ns)
                    if dt:
                        rule["drop_type"] = dt
                    dc_raw = _el_text(rule_el, "drop_count", ns)
                    if dc_raw.isdigit():
                        rule["drop_count"] = int(dc_raw)
                    if rule:
                        rules.append(rule)
                if rules:
                    g["rules"] = rules

            groups.append(g)
    except (OSError, ET.ParseError):
        pass
    return groups


def _parse_late_policy(xml_path: Path) -> dict[str, Any]:
    """Parse late_policy.xml into a flat dict."""
    if not xml_path.exists():
        return {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        result: dict[str, Any] = {}
        for child in root:
            tag = _strip_ns(child.tag)
            text = (child.text or "").strip()
            if not text:
                continue
            val = _coerce_xml_value(text)
            if val is not None:
                result[tag] = val
        return result
    except (OSError, ET.ParseError):
        return {}


def _parse_context(xml_path: Path) -> dict[str, Any]:
    """Parse context.xml into a dict (canvas_domain, course_id, etc.)."""
    if not xml_path.exists():
        return {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        result: dict[str, Any] = {}
        for child in root:
            tag = _strip_ns(child.tag)
            text = (child.text or "").strip()
            if text:
                try:
                    result[tag] = int(text)
                except ValueError:
                    result[tag] = text
        return result
    except (OSError, ET.ParseError):
        return {}


def _parse_rubrics(xml_path: Path) -> list[dict[str, Any]]:
    """Parse rubrics.xml into a list of rubric dicts (with nested criteria and ratings)."""
    if not xml_path.exists():
        return []
    rubrics: list[dict[str, Any]] = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = _xml_ns(root)
        r_tag = f"{{{ns}}}rubric" if ns else "rubric"

        for r_el in root.findall(r_tag):
            r: dict[str, Any] = {}
            identifier = r_el.get("identifier", "")
            if identifier:
                r["identifier"] = identifier
            for field in (
                "title", "read_only", "reusable", "public", "points_possible",
                "hide_score_total", "free_form_criterion_comments", "rating_order",
            ):
                text = _el_text(r_el, field, ns)
                if text:
                    r[field] = _coerce_xml_value(text)

            criteria_el = r_el.find(f"{{{ns}}}criteria") if ns else r_el.find("criteria")
            criteria: list[dict[str, Any]] = []
            if criteria_el is not None:
                crit_tag = f"{{{ns}}}criterion" if ns else "criterion"
                for crit_el in criteria_el.findall(crit_tag):
                    c: dict[str, Any] = {}
                    for field in ("criterion_id", "description", "long_description"):
                        text = _el_text(crit_el, field, ns)
                        if text:
                            c[field] = text
                    pts_text = _el_text(crit_el, "points", ns)
                    if pts_text:
                        c["points"] = _coerce_xml_value(pts_text)

                    ratings_el = crit_el.find(f"{{{ns}}}ratings") if ns else crit_el.find("ratings")
                    ratings: list[dict[str, Any]] = []
                    if ratings_el is not None:
                        rating_tag = f"{{{ns}}}rating" if ns else "rating"
                        for rating_el in ratings_el.findall(rating_tag):
                            rat: dict[str, Any] = {}
                            for field in ("id", "description", "long_description"):
                                text = _el_text(rating_el, field, ns)
                                if text:
                                    rat[field] = text
                            rpts_text = _el_text(rating_el, "points", ns)
                            if rpts_text:
                                rat["points"] = _coerce_xml_value(rpts_text)
                            if rat:
                                ratings.append(rat)
                    if ratings:
                        c["ratings"] = ratings
                    criteria.append(c)
            if criteria:
                r["criteria"] = criteria
            rubrics.append(r)
    except (OSError, ET.ParseError):
        pass
    return rubrics


def _parse_files_meta(xml_path: Path) -> dict[str, Any]:
    """Parse files_meta.xml into a dict with 'folders' and 'files' lists."""
    if not xml_path.exists():
        return {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = _xml_ns(root)

        result: dict[str, Any] = {}

        folders_el = root.find(f"{{{ns}}}folders") if ns else root.find("folders")
        folders: list[dict[str, Any]] = []
        if folders_el is not None:
            folder_tag = f"{{{ns}}}folder" if ns else "folder"
            for folder_el in folders_el.findall(folder_tag):
                f: dict[str, Any] = {"path": folder_el.get("path", "")}
                hidden_text = _el_text(folder_el, "hidden", ns)
                if hidden_text:
                    f["hidden"] = hidden_text.lower() == "true"
                folders.append(f)
        if folders:
            result["folders"] = folders

        files_el = root.find(f"{{{ns}}}files") if ns else root.find("files")
        files: list[dict[str, Any]] = []
        if files_el is not None:
            file_tag = f"{{{ns}}}file" if ns else "file"
            for file_el in files_el.findall(file_tag):
                fi: dict[str, Any] = {"identifier": file_el.get("identifier", "")}
                for field in ("locked", "hidden", "display_name", "unlock_at"):
                    text = _el_text(file_el, field, ns)
                    if text:
                        fi[field] = _coerce_xml_value(text)
                files.append(fi)
        if files:
            result["files"] = files

        return result
    except (OSError, ET.ParseError):
        return {}


def _parse_events(xml_path: Path) -> list[dict[str, Any]]:
    """Parse events.xml into a list of event dicts."""
    if not xml_path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = _xml_ns(root)
        ev_tag = f"{{{ns}}}event" if ns else "event"

        for ev_el in root.findall(ev_tag):
            all_day_raw = _el_text(ev_el, "all_day", ns)
            all_day_date = _el_text(ev_el, "all_day_date", ns)
            ev: dict[str, Any] = {
                "title": _el_text(ev_el, "title", ns),
                "description": _el_text(ev_el, "description", ns),
                "start_at": _el_text(ev_el, "start_at", ns),
                "end_at": _el_text(ev_el, "end_at", ns),
                "all_day": all_day_raw.lower() == "true",
            }
            if all_day_date:
                ev["all_day_date"] = all_day_date
            events.append(ev)
    except (OSError, ET.ParseError):
        pass
    return events


def _write_rubrics_toml(rubrics: list[dict[str, Any]], output_dir: Path) -> None:
    """Write course_settings/rubrics.toml from a list of rubric dicts."""
    cs_dir = output_dir / "course_settings"
    cs_dir.mkdir(parents=True, exist_ok=True)
    (cs_dir / "rubrics.toml").write_text(
        tomli_w.dumps({"rubrics": rubrics}), encoding="utf-8"
    )
    print("Writing: course_settings/rubrics.toml")


def _write_files_meta_toml(files_meta: dict[str, Any], output_dir: Path) -> None:
    """Write course_settings/files_meta.toml from parsed files_meta data."""
    cs_dir = output_dir / "course_settings"
    cs_dir.mkdir(parents=True, exist_ok=True)
    (cs_dir / "files_meta.toml").write_text(
        tomli_w.dumps(files_meta), encoding="utf-8"
    )
    print("Writing: course_settings/files_meta.toml")


def _convert_tab_configuration(
    raw: Any, tool_titles: dict[str, str]
) -> list[dict[str, Any]]:
    """Convert Canvas's `tab_configuration` JSON string into a human-readable list.

    Canvas exports an escaped JSON string of ``{"id": <int|str>, "hidden": <bool>}``
    entries, where built-in tabs use internal numeric ids and external tools use
    ``context_external_tool_<imscc-resource-id>``.  We rewrite this into an ordered
    list of dicts suitable for the ``[[tab_configuration]]`` array-of-tables:

    - built-in tabs become ``{"id": "assignments"}`` (the live Tabs API string id);
    - external tools become ``{"label": "Zoom", "id": "context_external_tool_g…"}``
      where the label is resolved from the cartridge's BLTI title (``tool_titles``,
      keyed by resource id) and the id is retained for provenance;
    - ``hidden = true`` is emitted only for hidden tabs (omitted otherwise);
    - display order is preserved.

    Canvas does **not** export the names of external tools that are only used in
    course navigation (it ships no BLTI resource for them), so most real cartridges
    can't resolve a label.  Those tabs are written with an empty ``label = ""`` slot
    for the user to fill in by hand, and a single summary warning is printed.
    """
    if isinstance(raw, str):
        try:
            entries = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            print("  WARNING: course tab_configuration is not valid JSON; dropping it")
            return []
    elif isinstance(raw, list):
        entries = raw
    else:
        return []

    result: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("id")
        out: dict[str, Any] = {}

        if isinstance(raw_id, bool):
            continue
        if isinstance(raw_id, int):
            api_id = NUMERIC_TAB_IDS.get(raw_id)
            if api_id is None:
                print(
                    f"  WARNING: unknown course-navigation tab id {raw_id!r}; "
                    "keeping the numeric id"
                )
                out["id"] = raw_id
            else:
                out["id"] = api_id
        elif isinstance(raw_id, str) and raw_id.startswith(TOOL_TAB_PREFIX):
            resource_id = raw_id[len(TOOL_TAB_PREFIX):]
            label = tool_titles.get(resource_id)
            # Empty label is a deliberate fill-in slot when the name isn't in the
            # cartridge; sync ignores it until the user supplies a real label.
            out["label"] = label if label else ""
            out["id"] = raw_id
            if not label:
                unresolved.append(raw_id)
        elif isinstance(raw_id, str) and raw_id:
            out["id"] = raw_id
        else:
            continue

        if entry.get("hidden"):
            out["hidden"] = True
        result.append(out)

    if unresolved:
        print(
            f"  WARNING: {len(unresolved)} external-tool navigation tab(s) have no name "
            "in this cartridge — Canvas does not export the names of tools used only in "
            'course navigation. Each was written with an empty label = "". Fill in the '
            "tool's name as it appears in the destination course to position or hide it; "
            "otherwise sync leaves that tab untouched."
        )
    return result


def _resolve_tool_titles(
    imscc_dir: Path, temp_manifest: dict[str, TempEntry]
) -> dict[str, str]:
    """Map each LTI resource id to its human-readable BLTI title."""
    titles: dict[str, str] = {}
    for ident, entry in temp_manifest.items():
        if entry.category == "lti" and entry.imscc_path:
            titles[ident] = _title_from_xml_element(imscc_dir / entry.imscc_path, "title")
    return titles


def _write_course_settings_toml(
    course_settings: dict[str, Any],
    manifest_meta: dict[str, str],
    grading_standards: list[dict[str, Any]],
    assignment_groups: list[dict[str, Any]],
    late_policy: dict[str, Any],
    output_dir: Path,
) -> None:
    """Write course_settings/course_settings.toml with all course-level settings."""
    data: dict[str, Any] = {}

    # Identity and key settings first (deterministic ordering)
    for key in ("title", "course_code", "default_view", "license", "start_at", "conclude_at"):
        if key in course_settings:
            data[key] = course_settings[key]

    # Manifest metadata (last_modified, copyright info)
    for key, val in manifest_meta.items():
        data[key] = val

    # Remaining flat course settings, excluding nested sections handled below
    skip = {"title", "course_code", "default_view", "license", "start_at", "conclude_at",
            "default_post_policy", "tab_configuration"}
    for key, val in course_settings.items():
        if key not in data and key not in skip:
            data[key] = val

    # Inline nested sections
    if late_policy:
        data["late_policy"] = late_policy
    if "default_post_policy" in course_settings:
        data["default_post_policy"] = course_settings["default_post_policy"]

    # Array-of-tables sections last (tomli_w emits [[...]] for list-of-dicts)
    if grading_standards:
        data["grading_standards"] = grading_standards
    if assignment_groups:
        data["assignment_groups"] = assignment_groups
    tab_configuration = course_settings.get("tab_configuration")
    if tab_configuration:
        data["tab_configuration"] = tab_configuration

    cs_dir = output_dir / "course_settings"
    cs_dir.mkdir(parents=True, exist_ok=True)
    (cs_dir / "course_settings.toml").write_text(
        tomli_w.dumps(data), encoding="utf-8"
    )
    print("Writing: course_settings/course_settings.toml")


def _write_canvas_toml(context: dict[str, Any], output_dir: Path) -> None:
    """Write canvas.toml, using canvas_domain and course_id from context.xml when available."""
    canvas_domain = context.get("canvas_domain", "")
    course_id = context.get("course_id")

    base_url = f"https://{canvas_domain}" if canvas_domain else "https://yourschool.instructure.com"
    course_id_line = (
        f"course_id = {course_id}\n" if course_id
        else "course_id = 0  # TODO: set your course ID\n"
    )

    cs_dir = output_dir / "course_settings"
    cs_dir.mkdir(parents=True, exist_ok=True)
    (cs_dir / "canvas.toml").write_text(
        f'base_url = "{base_url}"\n'
        + course_id_line
        + "\n"
        "# [auth]\n"
        "# Prefer env var CANVAS_API_TOKEN; this is a fallback for local use only\n"
        '# api_token = ""\n',
        encoding="utf-8",
    )
    print("Writing: course_settings/canvas.toml")


def _write_events_md(
    events: list[dict[str, Any]],
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
    course_id: int | str | None = None,
    base_url: str | None = None,
) -> None:
    """Write course_settings/events.md from parsed course calendar events."""
    cs_dir = output_dir / "course_settings"
    cs_dir.mkdir(parents=True, exist_ok=True)

    fm = _build_frontmatter({"title": "Course Events"})
    lines: list[str] = [fm, ""]

    for event in events:
        title = event.get("title", "Untitled Event")
        start_at = event.get("start_at", "")
        all_day = event.get("all_day", False)
        all_day_date = event.get("all_day_date", "")
        description_html = event.get("description", "")

        lines.append(f"## {title}")
        lines.append("")
        if all_day and all_day_date:
            lines.append(f"**Date:** {all_day_date} (all day)")
        elif start_at:
            lines.append(f"**Date:** {start_at}")
        lines.append("")

        if description_html:
            body_html = rewrite_imscc_links(
                description_html, temp_manifest, "course_settings/events.md", course_id, base_url
            )
            md = _shift_headings_down(_html_to_markdown(body_html), "course_settings/events.md").strip()
            if md:
                lines.append(md)
                lines.append("")

    (cs_dir / "events.md").write_text("\n".join(lines), encoding="utf-8")
    print("Writing: course_settings/events.md")


def create_course_settings(
    imscc_dir: Path,
    temp_manifest: dict[str, TempEntry],
    output_dir: Path,
    course_id: int | str | None = None,
    base_url: str | None = None,
) -> None:
    """Write course_settings/{course_settings.toml, syllabus.md, events.md, canvas.toml}."""
    cs_dir = output_dir / "course_settings"
    cs_dir.mkdir(parents=True, exist_ok=True)

    # syllabus.html → course_settings/syllabus.md
    syllabus_src = imscc_dir / "course_settings" / "syllabus.html"
    if syllabus_src.exists():
        raw_html = syllabus_src.read_text(encoding="utf-8", errors="replace")
        body_html = _extract_html_body(raw_html)
        body_html = rewrite_imscc_links(body_html, temp_manifest, "course_settings/syllabus.md", course_id, base_url)
        markdown = _html_to_markdown(body_html)
        markdown = _shift_headings_down(markdown, "course_settings/syllabus.md")
        fm = _build_frontmatter({"title": "Syllabus", "published": True})
        (cs_dir / "syllabus.md").write_text(fm + "\n" + markdown + "\n", encoding="utf-8")
        print("Converting page: course_settings/syllabus.md")

    imscc_cs_dir = imscc_dir / "course_settings"
    manifest_meta = _parse_manifest_metadata(imscc_dir / "imsmanifest.xml")
    course_settings = _parse_course_settings_full(imscc_cs_dir / "course_settings.xml")
    if "tab_configuration" in course_settings:
        tool_titles = _resolve_tool_titles(imscc_dir, temp_manifest)
        course_settings["tab_configuration"] = _convert_tab_configuration(
            course_settings["tab_configuration"], tool_titles
        )
    grading_standards = _parse_grading_standards(imscc_cs_dir / "grading_standards.xml")
    assignment_groups = _parse_assignment_groups(imscc_cs_dir / "assignment_groups.xml")
    late_policy = _parse_late_policy(imscc_cs_dir / "late_policy.xml")
    context = _parse_context(imscc_cs_dir / "context.xml")
    events = _parse_events(imscc_cs_dir / "events.xml")
    rubrics = _parse_rubrics(imscc_cs_dir / "rubrics.xml")
    files_meta = _parse_files_meta(imscc_cs_dir / "files_meta.xml")

    _write_course_settings_toml(
        course_settings, manifest_meta, grading_standards, assignment_groups, late_policy,
        output_dir,
    )

    if events:
        _write_events_md(events, temp_manifest, output_dir, course_id, base_url)

    if rubrics:
        _write_rubrics_toml(rubrics, output_dir)

    if files_meta:
        _write_files_meta_toml(files_meta, output_dir)

    _write_canvas_toml(context, output_dir)


# ---------------------------------------------------------------------------
# Canvas course reference snippet helpers
# ---------------------------------------------------------------------------

def _write_canvas_course_reference_snippet(base_url: str, output_dir: Path) -> None:
    """Write snippets/inline/CANVAS_COURSE_REFERENCE.md containing the full course base URL.

    The base URL is ``https://<canvas_domain>/courses/<course_id>``.
    """
    snippet_dir = output_dir / "snippets" / "inline"
    snippet_dir.mkdir(parents=True, exist_ok=True)
    (snippet_dir / "CANVAS_COURSE_REFERENCE.md").write_text(base_url, encoding="utf-8")
    print("Writing: snippets/inline/CANVAS_COURSE_REFERENCE.md")


def _replace_canvas_course_url_in_md_files(output_dir: Path, base_url: str) -> None:
    """Replace Canvas course links in all .md files with a CANVAS_COURSE_REFERENCE snippet ref.

    Converts Markdown links of the form::

        [link text](https://domain/courses/ID/path)

    to::

        [link text]($depth_prefix/snippets/inline/CANVAS_COURSE_REFERENCE.md$/path "link text")

    The link text is repeated as a Markdown link title so the destination remains
    human-readable in editors where the snippet ref is not expanded.

    As a safety net, any bare ``$CANVAS_COURSE_REFERENCE$`` placeholder token that
    survived into the Markdown (e.g. if Pandoc mangled the in-HTML substitution) is
    converted the same way, so the sync/update step never sees an unresolved token.
    """
    # Full resolved URL form: [text](https://domain/courses/ID/path "title")
    url_pattern = re.compile(
        r"\[([^\]]+)\]\(" + re.escape(base_url) + r"(/[^\s)\"]*|)(?:\s+\"[^\"]*\")?\)"
    )
    # Bare placeholder token form: [text]($CANVAS_COURSE_REFERENCE$/path "title")
    token_pattern = re.compile(
        r"\[([^\]]+)\]\(\$CANVAS_COURSE_REFERENCE\$(/[^\s)\"]*|)(?:\s+\"[^\"]*\")?\)"
    )
    count = 0
    for md_file in sorted(output_dir.rglob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if base_url not in text and "$CANVAS_COURSE_REFERENCE$" not in text:
            continue
        rel = md_file.relative_to(output_dir)
        depth = len(rel.parts) - 1
        snippet_path = f"{'../' * depth}snippets/inline/CANVAS_COURSE_REFERENCE.md"

        def _sub(m: re.Match, _sp: str = snippet_path) -> str:
            link_text = m.group(1)
            path_suffix = m.group(2)
            title = link_text.replace('"', '\\"')
            return f'[{link_text}](${_sp}${path_suffix} "{title}")'

        new_text = url_pattern.sub(_sub, text)
        new_text = token_pattern.sub(_sub, new_text)
        md_file.write_text(new_text, encoding="utf-8")
        count += 1
    if count:
        print(f"Parameterized Canvas course URL in {count} file(s) via CANVAS_COURSE_REFERENCE snippet")


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

        # Extract domain + course ID early so we can parameterize URLs after all files are written
        context = _parse_context(imscc_dir / "course_settings" / "context.xml")
        canvas_domain = context.get("canvas_domain", "")
        course_id = context.get("course_id")
        base_url = f"https://{canvas_domain}/courses/{course_id}" if canvas_domain and course_id else None

        print("Parsing IMSCC manifest...")
        temp_manifest = parse_imsmanifest(imscc_dir)

        # Phase 2: assets
        copy_assets(imscc_dir, output_dir)

        # Phase 3: pages
        for entry in temp_manifest.values():
            if entry.category == "page":
                convert_page(entry, imscc_dir, temp_manifest, output_dir, course_id, base_url)

        # Phase 4: assignments
        for entry in temp_manifest.values():
            if entry.category == "assignment":
                convert_assignment(entry, imscc_dir, temp_manifest, output_dir, course_id, base_url)

        # Phase 5: discussions
        for entry in temp_manifest.values():
            if entry.category == "discussion":
                convert_discussion(entry, imscc_dir, temp_manifest, output_dir, course_id, base_url)

        # Phase 5b: quizzes
        for entry in temp_manifest.values():
            if entry.category == "quiz":
                convert_quiz(entry, imscc_dir, output_dir)

        # Phase 5c: question banks
        for entry in temp_manifest.values():
            if entry.category == "question_bank":
                convert_question_bank(entry, imscc_dir, output_dir)

        # Phase 6: modules
        modules = parse_module_meta(imscc_dir)
        for module in modules:
            generate_module_file(module, temp_manifest, output_dir)
        if modules:
            order_entries = [f'    "{_slugify(m.title)}.md"' for m in modules]
            order_toml = "order = [\n" + ",\n".join(order_entries) + ",\n]\n"
            cs_dir = output_dir / "course_settings"
            cs_dir.mkdir(parents=True, exist_ok=True)
            (cs_dir / "module_order.toml").write_text(order_toml, encoding="utf-8")
            print(f"Generating module order: course_settings/module_order.toml")

        # Phase 7: course settings
        create_course_settings(imscc_dir, temp_manifest, output_dir, course_id, base_url)

        # Phase 8: parameterize Canvas course URL via snippet
        if base_url:
            _replace_canvas_course_url_in_md_files(output_dir, base_url)
            _write_canvas_course_reference_snippet(base_url, output_dir)

        print(f"\nDone. Wrote course repo to: {output_dir}")
    finally:
        if tmp is not None:
            tmp.cleanup()
