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
from pathlib import Path

from bs4 import BeautifulSoup

import reflow
from reflow import normalize_ws

FS_RE = re.compile(r"^fs([0-9a-zA-Z]+)$")
M_RE = re.compile(r"^m([0-9a-zA-Z]+)$")
X_RE = re.compile(r"^x([0-9a-zA-Z]+)$")
Y_RE = re.compile(r"^y([0-9a-zA-Z]+)$")

CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
SIMPLE_CLASS_SELECTOR_RE = re.compile(r"^\.([A-Za-z0-9_]+)$")
MATRIX_SCALE_RE = re.compile(r"matrix\(\s*([-\d.]+)")


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
        lines.extend(reflow.merge_same_row(page_lines))
    return lines


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

    assignments = reflow.assign_chapters(lines, chapters)
    body_size = reflow.compute_body_size(lines)
    print(f"[info] detected body text size = {body_size}")

    lines_by_chapter = [[] for _ in chapters]
    for line, chapter_idx in zip(lines, assignments):
        if chapter_idx >= 0:
            lines_by_chapter[chapter_idx].append(line)

    chapter_blocks = [reflow.group_into_blocks(cl, body_size) for cl in lines_by_chapter]
    size_to_level = reflow.assign_heading_levels(chapter_blocks)

    written = reflow.write_chapters(output_dir, chapters, chapter_blocks, size_to_level, input_path.stem)
    print(f"[info] wrote {written} XHTML file(s) and index.yaml to {output_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_html", help="pdf2htmlEX-generated HTML file to convert")
    args = parser.parse_args()
    sys.exit(convert(args.input_html))


if __name__ == "__main__":
    main()
