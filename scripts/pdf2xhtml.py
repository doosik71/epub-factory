#!/usr/bin/env python3
"""Convert a PDF directly into reflowed XHTML files (no pdf2htmlEX needed).

Same output and reflow rules as pdfhtml2xhtml.py, but reads the PDF itself
instead of pdf2htmlEX's HTML: pdfplumber supplies each word's page position
and font size, and pypdf supplies the bookmark (outline) tree used to split
the document into chapters. See reflow.py for the shared reflow logic.

Usage:
    python pdf2xhtml.py <input.pdf>

For an input file at <dir>/<stem>.pdf, output is written to <dir>/<stem>/:
    001-<slug>.xhtml
    002-<slug>.xhtml
    ...
    index.yaml   -- {title, author, chapters}, see pdfhtml2xhtml.py for the
                    exact shape. Edit this file to control the final EPUB's
                    metadata and table of contents.
"""

import argparse
import sys
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

import reflow
from reflow import normalize_ws


def extract_lines(pdf):
    """Flatten every page's words into an ordered list of line records.

    Coordinates are converted to PDF's native bottom-left-origin space (y
    increases upward), matching the outline destinations read by pypdf.
    """
    lines = []
    for page_no, page in enumerate(pdf.pages, start=1):
        words = page.extract_words(extra_attrs=["size"], keep_blank_chars=False)
        page_lines = []
        for word in words:
            text = normalize_ws(word["text"])
            if not text:
                continue
            page_lines.append(
                {
                    "page": page_no,
                    "left": word["x0"],
                    "bottom": page.height - word["bottom"],
                    "size": word.get("size", 0.0),
                    "text": text,
                }
            )
        lines.extend(reflow.merge_same_row(page_lines))
    return lines


def extract_outline(reader):
    """Read the PDF bookmark tree as a document-ordered list of chapters.

    `level` reflects the outline's nesting depth (outermost entries are
    level 1), so it's a starting point for the EPUB's table of contents
    rather than a guaranteed clean hierarchy.
    """
    chapters = []

    def walk(items, level):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            try:
                page_no = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            y_target = float(item.top) if item.top is not None else float("inf")
            chapters.append(
                {
                    "title": normalize_ws(item.title) or "untitled",
                    "page": page_no,
                    "y_target": y_target,
                    "level": level,
                }
            )

    walk(reader.outline, 1)
    return chapters


def convert(input_path):
    input_path = Path(input_path)
    output_dir = input_path.with_suffix("")

    with pdfplumber.open(input_path) as pdf:
        lines = extract_lines(pdf)
    print(f"[info] extracted {len(lines)} text lines")

    reader = PdfReader(input_path)
    chapters = extract_outline(reader)
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
    parser.add_argument("input_pdf", help="PDF file to convert")
    args = parser.parse_args()
    sys.exit(convert(args.input_pdf))


if __name__ == "__main__":
    main()
