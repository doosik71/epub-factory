#!/usr/bin/env python3
"""Convert a PDF file straight into an EPUB.

Thin orchestration wrapper around pdf2xhtml.py and xhtml2epub.py:
  1. pdf2xhtml.convert() reflows <input>.pdf into <input>/ (chapters +
     index.yaml), exactly as running pdf2xhtml.py directly would.
  2. xhtml2epub.build_epub() packages that index.yaml into an EPUB.

Usage:
    python pdf2epub.py <input.pdf> [-o OUTPUT_DIR]

The EPUB is named "<title> by <author>.epub" and written into OUTPUT_DIR, or
into the same folder as <input.pdf> if -o is omitted (unlike xhtml2epub.py
run standalone, which would default to the generated <input>/ chapter folder).
"""

import argparse
import sys
from pathlib import Path

import pdf2xhtml
import xhtml2epub


def convert(input_pdf, output_dir=None):
    input_path = Path(input_pdf)

    rc = pdf2xhtml.convert(input_path)
    if rc != 0:
        return rc

    index_path = input_path.with_suffix("") / "index.yaml"
    epub_output_dir = output_dir if output_dir else input_path.parent
    return xhtml2epub.build_epub(index_path, epub_output_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_pdf", help="PDF file to convert")
    parser.add_argument(
        "-o",
        "--output-dir",
        help="directory to write the .epub into (default: same folder as input_pdf)",
    )
    args = parser.parse_args()
    sys.exit(convert(args.input_pdf, args.output_dir))


if __name__ == "__main__":
    main()
