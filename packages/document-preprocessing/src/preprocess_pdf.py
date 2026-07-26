#!/usr/bin/env python3
"""Extract bounded text and rendered pages from an untrusted student PDF."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    print(
        json.dumps({"code": "PDF_PREPROCESSOR_UNAVAILABLE"}),
        file=sys.stderr,
    )
    raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--max-text-characters", type=int, required=True)
    parser.add_argument("--max-text-pages", type=int, required=True)
    parser.add_argument("--max-image-pages", type=int, required=True)
    parser.add_argument("--max-image-dimension", type=int, required=True)
    parser.add_argument("--jpeg-quality", type=int, required=True)
    return parser.parse_args()


def select_page_indexes(page_count: int, maximum: int) -> list[int]:
    if maximum <= 0:
        return []
    if page_count <= maximum:
        return list(range(page_count))
    if maximum == 1:
        return [0]
    selected = {
        round(index * (page_count - 1) / (maximum - 1))
        for index in range(maximum)
    }
    return sorted(selected)


def render_page(
    page: fitz.Page,
    max_dimension: int,
    jpeg_quality: int,
) -> dict[str, object]:
    source = page.rect
    scale = min(max_dimension / max(source.width, source.height), 2.0)
    scale = max(scale, 0.5)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
    )
    image_bytes = pixmap.tobytes("jpeg", jpg_quality=jpeg_quality)
    return {
        "pageNumber": page.number + 1,
        "mimeType": "image/jpeg",
        "dataBase64": base64.b64encode(image_bytes).decode("ascii"),
        "width": pixmap.width,
        "height": pixmap.height,
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
    }


def preprocess(args: argparse.Namespace) -> dict[str, object]:
    path = Path(args.input)
    try:
        document = fitz.open(path)
    except Exception:
        fail("PDF_INVALID")

    try:
        if document.needs_pass:
            fail("PDF_ENCRYPTED")
        if document.page_count < 1:
            fail("PDF_INVALID")

        text_parts: list[str] = []
        text_length = 0
        text_truncated = False
        text_page_limit = min(document.page_count, args.max_text_pages)
        for index in range(text_page_limit):
            page_text = document.load_page(index).get_text("text", sort=True)
            page_text = page_text.replace("\x00", "").strip()
            if not page_text:
                continue
            section = f"\n\n--- Page {index + 1} ---\n{page_text}"
            remaining = args.max_text_characters - text_length
            if remaining <= 0:
                text_truncated = True
                break
            if len(section) > remaining:
                text_parts.append(section[:remaining])
                text_length += remaining
                text_truncated = True
                break
            text_parts.append(section)
            text_length += len(section)
        if document.page_count > text_page_limit:
            text_truncated = True

        page_indexes = select_page_indexes(
            document.page_count,
            args.max_image_pages,
        )
        images = [
            render_page(
                document.load_page(index),
                args.max_image_dimension,
                args.jpeg_quality,
            )
            for index in page_indexes
        ]
        return {
            "extractedText": "".join(text_parts).strip(),
            "pageCount": document.page_count,
            "renderedPageNumbers": [index + 1 for index in page_indexes],
            "textTruncated": text_truncated,
            "images": images,
        }
    finally:
        document.close()


def fail(code: str) -> None:
    print(json.dumps({"code": code}), file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    args = parse_args()
    try:
        result = preprocess(args)
    except SystemExit:
        raise
    except Exception:
        fail("PDF_PREPROCESSING_FAILED")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
