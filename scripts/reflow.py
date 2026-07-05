"""Shared reflow logic used by pdfhtml2xhtml.py and pdf2xhtml.py.

Both scripts turn a page-based, position-annotated stream of text lines into
paragraph/heading XHTML chapters plus an index.yaml manifest. This module
holds everything that only depends on that generic line/chapter shape:

    line    = {"page": int, "left": float, "bottom": float, "size": float, "text": str}
              "bottom"/"left" are page coordinates in points, measured from
              the page's bottom-left corner (PDF's native coordinate space).
    chapter = {"title": str, "page": int, "y_target": float, "level": int}
              "y_target" is in the same bottom-left coordinate space as a
              line's "bottom", used to decide where each chapter starts.

The two callers differ only in how they extract lines/chapters from their
respective input format (pdf2htmlEX HTML vs. a PDF read directly).
"""

import re
from html import escape

import yaml

ROW_MERGE_EPS = 1.5  # pt: text fragments within this vertical distance are one visual row
PAGE_NUMBER_LINE_RE = re.compile(r"^[\-‐-―\s]*\d{1,4}[\-‐-―\s]*$")
BULLET_RE = re.compile(
    r"^("
    r"[\-–—○●□■◇◆▸▷▶‣•·ㅇ]"
    r"|\(?[0-9]{1,3}[.\)]"
    r"|[①-⑳]"
    r"|[Ⅰ-Ⅹ]"
    r"|[가-힣][.\)]"
    r"|[IVXLCivxlc]{1,6}[.\)]"
    r")(\s|$)"
)

HEADING_SIZE_RATIO = 1.05
HEADING_LEVEL_TOLERANCE = 0.08
MAX_HEADING_LEVEL = 6


def normalize_ws(text):
    return re.sub(r"\s+", " ", text).strip()


def merge_same_row(page_lines):
    """Group text fragments into visual rows (Y-proximity clustering, anchored
    to each row's first/topmost fragment to avoid drift) and concatenate each
    row's fragments left-to-right into one line.

    Fragment coordinates can jitter slightly even within one visual line (e.g.
    per-glyph font metric differences in raw PDF extraction), so rows must be
    clustered by proximity before sorting left-to-right -- sorting once by
    (-bottom, left) is not enough, as a same-row fragment with a marginally
    different bottom can sort ahead of/behind fragments to its left/right.

    `page_lines` can be in any order; the result is ordered top-to-bottom.
    """
    if not page_lines:
        return []
    ordered = sorted(page_lines, key=lambda l: -l["bottom"])
    rows = []
    current_row = []
    anchor = None
    for item in ordered:
        if current_row and abs(anchor - item["bottom"]) < ROW_MERGE_EPS:
            current_row.append(item)
        else:
            if current_row:
                rows.append(current_row)
            current_row = [item]
            anchor = item["bottom"]
    if current_row:
        rows.append(current_row)

    merged = []
    for row in rows:
        row.sort(key=lambda l: l["left"])
        merged.append(
            {
                "page": row[0]["page"],
                "left": row[0]["left"],
                "bottom": max(item["bottom"] for item in row),
                "size": max(item["size"] for item in row),
                "text": normalize_ws(" ".join(item["text"] for item in row)),
            }
        )
    return merged


def assign_chapters(lines, chapters):
    """Tag each line with the index of the last chapter boundary it falls under."""

    def row_key(page, y):
        return (page, -y)

    boundaries = [row_key(c["page"], c["y_target"]) for c in chapters]
    assignments = [-1] * len(lines)
    bi = 0
    current = -1
    for li, line in enumerate(lines):
        line_row = row_key(line["page"], line["bottom"])
        while bi < len(boundaries) and boundaries[bi] <= line_row:
            current = bi
            bi += 1
        assignments[li] = current
    return assignments


def compute_body_size(lines):
    weight = {}
    for line in lines:
        bucket = round(line["size"], 1)
        weight[bucket] = weight.get(bucket, 0) + len(line["text"])
    if not weight:
        return 1.0
    return max(weight, key=weight.get)


def group_into_blocks(lines, body_size):
    """Merge consecutive same-style lines into paragraph/heading blocks."""
    blocks = []
    current = None
    for line in lines:
        text = line["text"]
        if PAGE_NUMBER_LINE_RE.match(text):
            continue
        size = line["size"]
        is_heading = size > body_size * HEADING_SIZE_RATIO
        starts_new = (
            current is None
            or is_heading != current["heading"]
            or abs(size - current["size"]) > current["size"] * HEADING_LEVEL_TOLERANCE + 0.5
            or BULLET_RE.match(text)
        )
        if starts_new:
            if current:
                blocks.append(current)
            current = {"heading": is_heading, "size": size, "texts": [text]}
        else:
            current["texts"].append(text)
    if current:
        blocks.append(current)
    return blocks


def assign_heading_levels(chapter_blocks):
    """Cluster heading font sizes (across the whole document) into h1..h6 levels."""
    sizes = sorted(
        {b["size"] for blocks in chapter_blocks for b in blocks if b["heading"]},
        reverse=True,
    )
    clusters = []
    for size in sizes:
        if clusters and size >= clusters[-1][-1] * (1 - HEADING_LEVEL_TOLERANCE):
            clusters[-1].append(size)
        else:
            clusters.append([size])
    size_to_level = {}
    for level, cluster in enumerate(clusters, start=1):
        for size in cluster:
            size_to_level[size] = min(level, MAX_HEADING_LEVEL)
    return size_to_level


def render_xhtml(title, blocks, size_to_level):
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!DOCTYPE html>",
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ko" lang="ko">',
        "<head>",
        '<meta charset="utf-8"/>',
        f"<title>{escape(title, quote=False)}</title>",
        "</head>",
        "<body>",
    ]
    for block in blocks:
        text = normalize_ws(" ".join(block["texts"]))
        if not text:
            continue
        tag = f"h{size_to_level.get(block['size'], MAX_HEADING_LEVEL)}" if block["heading"] else "p"
        parts.append(f"<{tag}>{escape(text, quote=False)}</{tag}>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


def slugify(title, max_len=40):
    slug = re.sub(r'[\\/:*?"<>|]', "", title)
    slug = re.sub(r"\s+", "_", slug).strip("_")
    return slug[:max_len] or "chapter"


def guess_title_author(chapter_blocks, input_stem):
    """Best-effort metadata guess: the document's first two headings, in order,
    are usually the title and then an author/organization/date line. There is
    no reliable metadata to confirm this, so callers should treat the result
    as a draft to be corrected by hand."""
    headings = [
        normalize_ws(" ".join(block["texts"]))
        for blocks in chapter_blocks
        for block in blocks
        if block["heading"]
    ]
    title = headings[0] if headings else re.sub(r"[+_]", " ", input_stem).strip()
    author = headings[1] if len(headings) > 1 else ""
    return title, author


def write_chapters(output_dir, chapters, chapter_blocks, size_to_level, input_stem):
    """Write one XHTML file per non-empty chapter plus index.yaml. Returns the
    number of chapters written."""
    output_dir.mkdir(parents=True, exist_ok=True)

    index = []
    seq = 0
    for chapter, blocks in zip(chapters, chapter_blocks):
        if not blocks:
            print(f"[skip] empty chapter: {chapter['title']!r}")
            continue
        seq += 1
        out_name = f"{seq:03d}-{slugify(chapter['title'])}.xhtml"
        xhtml = render_xhtml(chapter["title"], blocks, size_to_level)
        (output_dir / out_name).write_text(xhtml, encoding="utf-8")
        index.append({"level": chapter["level"], "label": chapter["title"], "file": out_name})

    title, author = guess_title_author(chapter_blocks, input_stem)
    index_doc = {"title": title, "author": author, "chapters": index}
    index_path = output_dir / "index.yaml"
    with index_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(index_doc, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    return len(index)
