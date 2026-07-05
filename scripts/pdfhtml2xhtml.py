#!/usr/bin/env python3
"""Convert pdf2htmlEX output (PDF-to-HTML) into reflowed XHTML files.

The input is the fixed-layout HTML produced by pdf2htmlEX: every page is a
stack of absolutely positioned, character-level <div> elements drawn over a
raster background image. This script discards the positioning/background
noise, reconstructs paragraphs and headings from the real text content, and
splits the result into one XHTML file per PDF bookmark (outline) entry.

Usage:
    python pdfhtml2xhtml.py <input.html>

For an input file at <dir>/<stem>.html, output is written to <dir>/<stem>/:
    001-<slug>.xhtml
    002-<slug>.xhtml
    ...
    index.yaml   -- {title, author, chapters} where chapters is an ordered
                    {level, label, file} list describing the chapter tree,
                    derived from the PDF's bookmark nesting. title/author are
                    a best-effort guess from the document's first headings.
                    Edit this file to control the final EPUB's metadata and
                    table of contents (reorder entries, rename labels, change
                    levels, fix title/author).
"""

import argparse
import json
import re
import sys
from html import escape
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

FS_RE = re.compile(r"^fs([0-9a-zA-Z]+)$")
M_RE = re.compile(r"^m([0-9a-zA-Z]+)$")
X_RE = re.compile(r"^x([0-9a-zA-Z]+)$")
Y_RE = re.compile(r"^y([0-9a-zA-Z]+)$")

CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
SIMPLE_CLASS_SELECTOR_RE = re.compile(r"^\.([A-Za-z0-9_]+)$")
MATRIX_SCALE_RE = re.compile(r"matrix\(\s*([-\d.]+)")

ROW_MERGE_EPS = 1.5  # px: text fragments within this vertical distance are one visual row
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


def strip_at_media_blocks(css_text):
    """Remove @media {...} blocks (e.g. print stylesheets that redefine the same
    class names in different units) so they can't shadow the on-screen rules."""
    out = []
    i, n = 0, len(css_text)
    while True:
        idx = css_text.find("@media", i)
        if idx == -1:
            out.append(css_text[i:])
            break
        out.append(css_text[i:idx])
        brace_start = css_text.find("{", idx)
        if brace_start == -1:
            break
        depth = 1
        j = brace_start + 1
        while j < n and depth > 0:
            if css_text[j] == "{":
                depth += 1
            elif css_text[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def parse_css(html):
    """Parse pdf2htmlEX's auto-generated single-class CSS rules into a lookup dict."""
    css_text = "".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    css_text = strip_at_media_blocks(css_text)
    props = {}
    for selector, body in CSS_RULE_RE.findall(css_text):
        m = SIMPLE_CLASS_SELECTOR_RE.match(selector.strip())
        if not m:
            continue
        decl = {}
        for item in body.split(";"):
            key, sep, value = item.partition(":")
            if sep:
                decl[key.strip()] = value.strip()
        props.setdefault(m.group(1), {}).update(decl)
    return props


def parse_px(value):
    m = re.match(r"[-\d.]+", value)
    return float(m.group(0)) if m else 0.0


def find_class_value(classes, css, prefix_re, prop):
    for cls in classes:
        if prefix_re.match(cls):
            decl = css.get(cls)
            if decl and prop in decl:
                return parse_px(decl[prop])
    return None


def find_scale(classes, css):
    for cls in classes:
        if M_RE.match(cls):
            decl = css.get(cls)
            if not decl:
                continue
            transform = decl.get("transform", "")
            m = MATRIX_SCALE_RE.search(transform)
            if m:
                return abs(float(m.group(1)))
            if transform.strip() == "none":
                return 1.0
    return 1.0


def normalize_ws(text):
    return re.sub(r"\s+", " ", text).strip()


def extract_lines(soup, css):
    """Flatten every page's text into an ordered list of line records."""
    container = soup.find(id="page-container") or soup
    pages = container.find_all("div", class_="pf")
    lines = []
    for page_div in pages:
        try:
            page_no = int(page_div.get("data-page-no", "0"), 16)
        except ValueError:
            page_no = 0
        page_lines = []
        for c_div in page_div.find_all(class_="c"):
            c_classes = c_div.get("class", [])
            c_left = find_class_value(c_classes, css, X_RE, "left") or 0.0
            c_bottom = find_class_value(c_classes, css, Y_RE, "bottom") or 0.0
            for t_div in c_div.find_all(class_="t", recursive=False):
                t_classes = t_div.get("class", [])
                t_left = find_class_value(t_classes, css, X_RE, "left") or 0.0
                t_bottom = find_class_value(t_classes, css, Y_RE, "bottom") or 0.0
                fs = find_class_value(t_classes, css, FS_RE, "font-size") or 0.0
                scale = find_scale(t_classes, css)
                text = normalize_ws(t_div.get_text())
                if not text:
                    continue
                page_lines.append(
                    {
                        "page": page_no,
                        "left": c_left + t_left,
                        "bottom": c_bottom + t_bottom,
                        "size": fs * scale,
                        "text": text,
                    }
                )
        page_lines.sort(key=lambda l: (-l["bottom"], l["left"]))
        lines.extend(merge_same_row(page_lines))
    return lines


def merge_same_row(page_lines):
    """Merge text fragments that sit side by side on the same visual line."""
    merged = []
    for item in page_lines:
        if merged and abs(merged[-1]["bottom"] - item["bottom"]) < ROW_MERGE_EPS:
            merged[-1]["text"] = normalize_ws(merged[-1]["text"] + " " + item["text"])
            merged[-1]["size"] = max(merged[-1]["size"], item["size"])
        else:
            merged.append(dict(item))
    return merged


def build_page_id_to_no(soup):
    mapping = {}
    for div in soup.find_all(class_="pf"):
        page_id = div.get("id")
        page_no = div.get("data-page-no")
        if page_id and page_no is not None:
            try:
                mapping[page_id] = int(page_no, 16)
            except ValueError:
                pass
    return mapping


def parse_dest_y(a):
    y_target = float("inf")
    dest_raw = a.get("data-dest-detail")
    if dest_raw:
        try:
            dest = json.loads(dest_raw)
        except ValueError:
            dest = None
        if isinstance(dest, list):
            mode = dest[1] if len(dest) > 1 else None
            if mode == "FitH" and len(dest) > 2:
                y_target = float(dest[2])
            elif mode == "XYZ" and len(dest) > 3:
                y_target = float(dest[3])
    return y_target


def extract_outline(soup, page_id_to_no):
    """Read the PDF bookmark tree as a document-ordered list of chapters.

    `level` reflects the <ul> nesting depth in the bookmark tree (outermost
    entries are level 1), so it's a starting point for the EPUB's table of
    contents rather than a guaranteed clean hierarchy.
    """
    outline_div = soup.find(id="outline")
    if not outline_div:
        return []
    root_ul = outline_div.find("ul", recursive=False)
    if not root_ul:
        return []

    chapters = []

    def walk(ul, level):
        for li in ul.find_all("li", recursive=False):
            a = li.find("a", recursive=False)
            if a is not None:
                href = a.get("href", "")
                m = re.search(r"#(pf\w+)", href)
                page_no = page_id_to_no.get(m.group(1)) if m else None
                if page_no is not None:
                    chapters.append(
                        {
                            "title": normalize_ws(a.get_text()) or "untitled",
                            "page": page_no,
                            "y_target": parse_dest_y(a),
                            "level": level,
                        }
                    )
            sub_ul = li.find("ul", recursive=False)
            if sub_ul:
                walk(sub_ul, level + 1)

    walk(root_ul, 1)
    return chapters


def assign_chapters(lines, chapters):
    """Tag each line with the index of the last bookmark boundary it falls under."""
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
    no real metadata in pdf2htmlEX output to confirm this, so callers should
    treat the result as a draft to be corrected by hand."""
    headings = [
        normalize_ws(" ".join(block["texts"]))
        for blocks in chapter_blocks
        for block in blocks
        if block["heading"]
    ]
    title = headings[0] if headings else re.sub(r"[+_]", " ", input_stem).strip()
    author = headings[1] if len(headings) > 1 else ""
    return title, author


def convert(input_path):
    input_path = Path(input_path)
    output_dir = input_path.with_suffix("")
    html = input_path.read_text(encoding="utf-8")

    soup = BeautifulSoup(html, "lxml")
    css = parse_css(html)

    lines = extract_lines(soup, css)
    print(f"[info] extracted {len(lines)} text lines")

    page_id_to_no = build_page_id_to_no(soup)
    chapters = extract_outline(soup, page_id_to_no)
    if not chapters:
        print("[error] no PDF bookmark/outline found; cannot split into chapters", file=sys.stderr)
        return 1
    print(f"[info] found {len(chapters)} bookmark entries")

    assignments = assign_chapters(lines, chapters)
    body_size = compute_body_size(lines)
    print(f"[info] detected body text size = {body_size}")

    lines_by_chapter = [[] for _ in chapters]
    for line, chapter_idx in zip(lines, assignments):
        if chapter_idx >= 0:
            lines_by_chapter[chapter_idx].append(line)

    chapter_blocks = [group_into_blocks(cl, body_size) for cl in lines_by_chapter]
    size_to_level = assign_heading_levels(chapter_blocks)

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

    title, author = guess_title_author(chapter_blocks, input_path.stem)
    index_doc = {"title": title, "author": author, "chapters": index}
    index_path = output_dir / "index.yaml"
    with index_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(index_doc, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"[info] wrote {len(index)} XHTML file(s) and index.yaml to {output_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_html", help="pdf2htmlEX-generated HTML file to convert")
    args = parser.parse_args()
    sys.exit(convert(args.input_html))


if __name__ == "__main__":
    main()
