#!/usr/bin/env python3
"""Package reflowed XHTML chapters (see pdfhtml2xhtml.py) into an EPUB.

Reads an index.yaml written by pdfhtml2xhtml.py:
    title: <str>
    author: <str>
    chapters:
      - level: <int>   # 1 = top-level; used to nest the table of contents
        label: <str>   # chapter title, used as the TOC entry text
        file: <str>    # xhtml filename, relative to index.yaml's folder

The chapter list's order becomes the EPUB reading order (spine); `level` only
affects how entries nest in the table of contents, so re-leveling, reordering,
renaming, or deleting entries in index.yaml directly controls the final EPUB.

Usage:
    python xhtml2epub.py <index.yaml> [-o OUTPUT_DIR]

Output is written as "<title> by <author>.epub" (missing title/author default
to "unknown") into OUTPUT_DIR, or next to index.yaml if -o is omitted.
"""

import argparse
import datetime
import re
import sys
import uuid
import zipfile
from html import escape
from pathlib import Path

import yaml

UNKNOWN = "unknown"


def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def load_index(index_path):
    with index_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    title = (data.get("title") or "").strip() or UNKNOWN
    author = (data.get("author") or "").strip() or UNKNOWN
    chapters = data.get("chapters") or []
    return title, author, chapters


def build_toc_tree(chapters):
    """Turn the flat (level, label, file) list into a nested tree using each
    entry's level as its nesting depth, tolerating gaps/jumps in level."""
    root = []
    stack = [(0, {"children": root})]
    for item in chapters:
        level = max(1, int(item.get("level", 1)))
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1]
        node = {"label": item["label"], "href": item["file"], "children": []}
        parent["children"].append(node)
        stack.append((level, node))
    return root


def render_nav_ol(nodes):
    if not nodes:
        return ""
    items = []
    for node in nodes:
        link = f'<a href="{escape(node["href"], quote=True)}">{escape(node["label"], quote=False)}</a>'
        items.append(f"<li>{link}{render_nav_ol(node['children'])}</li>")
    return "<ol>" + "".join(items) + "</ol>"


def render_nav_xhtml(title, tree):
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<!DOCTYPE html>",
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ko" lang="ko">',
            "<head>",
            '<meta charset="utf-8"/>',
            f"<title>{escape(title, quote=False)}</title>",
            "</head>",
            "<body>",
            '<nav epub:type="toc" id="toc">',
            "<h1>Table of Contents</h1>",
            render_nav_ol(tree),
            "</nav>",
            "</body>",
            "</html>",
        ]
    )


def render_ncx_points(nodes, counter):
    points = []
    for node in nodes:
        play_order = next(counter)
        nav_id = f"navPoint-{play_order}"
        children = render_ncx_points(node["children"], counter)
        points.append(
            f'<navPoint id="{nav_id}" playOrder="{play_order}">'
            f"<navLabel><text>{escape(node['label'], quote=False)}</text></navLabel>"
            f'<content src="{escape(node["href"], quote=True)}"/>'
            f"{children}</navPoint>"
        )
    return "".join(points)


def tree_depth(nodes):
    if not nodes:
        return 0
    return 1 + max((tree_depth(n["children"]) for n in nodes), default=0)


def render_toc_ncx(title, book_uid, tree):
    counter = iter(range(1, 10**9))
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
            "<head>",
            f'<meta name="dtb:uid" content="{escape(book_uid, quote=True)}"/>',
            f'<meta name="dtb:depth" content="{max(1, tree_depth(tree))}"/>',
            '<meta name="dtb:totalPageCount" content="0"/>',
            '<meta name="dtb:maxPageNumber" content="0"/>',
            "</head>",
            f"<docTitle><text>{escape(title, quote=False)}</text></docTitle>",
            f"<navMap>{render_ncx_points(tree, counter)}</navMap>",
            "</ncx>",
        ]
    )


def render_content_opf(title, author, book_uid, chapters):
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine_items = []
    for i, chapter in enumerate(chapters, start=1):
        item_id = f"chap{i:03d}"
        manifest_items.append(
            f'<item id="{item_id}" href="{escape(chapter["file"], quote=True)}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{item_id}"/>')

    modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">',
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
            f'<dc:identifier id="book-id">{escape(book_uid, quote=False)}</dc:identifier>',
            f"<dc:title>{escape(title, quote=False)}</dc:title>",
            f"<dc:creator>{escape(author, quote=False)}</dc:creator>",
            "<dc:language>ko</dc:language>",
            f'<meta property="dcterms:modified">{modified}</meta>',
            "</metadata>",
            f"<manifest>{''.join(manifest_items)}</manifest>",
            f'<spine toc="ncx">{"".join(spine_items)}</spine>',
            "</package>",
        ]
    )


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def build_epub(index_path, output_dir=None):
    index_path = Path(index_path)
    index_dir = index_path.parent
    title, author, chapters = load_index(index_path)
    if not chapters:
        print("[error] index.yaml has no chapters", file=sys.stderr)
        return 1

    for chapter in chapters:
        chapter_path = index_dir / chapter["file"]
        if not chapter_path.exists():
            print(f"[error] chapter file not found: {chapter_path}", file=sys.stderr)
            return 1

    tree = build_toc_tree(chapters)
    book_uid = f"urn:uuid:{uuid.uuid4()}"

    out_dir = Path(output_dir) if output_dir else index_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = sanitize_filename(f"{title} by {author}.epub")
    out_path = out_dir / out_name

    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", render_content_opf(title, author, book_uid, chapters), zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", render_nav_xhtml(title, tree), zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", render_toc_ncx(title, book_uid, tree), zipfile.ZIP_DEFLATED)
        for chapter in chapters:
            content = (index_dir / chapter["file"]).read_text(encoding="utf-8")
            zf.writestr(f"OEBPS/{chapter['file']}", content, zipfile.ZIP_DEFLATED)

    print(f"[info] wrote {out_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index_yaml", help="index.yaml produced by pdfhtml2xhtml.py")
    parser.add_argument(
        "-o",
        "--output-dir",
        help="directory to write the .epub into (default: same folder as index.yaml)",
    )
    args = parser.parse_args()
    sys.exit(build_epub(args.index_yaml, args.output_dir))


if __name__ == "__main__":
    main()
