#!/usr/bin/env python3
"""Apply a student-approved electronic signature to an onboarding PDF."""

from __future__ import annotations

import argparse
import base64
import html
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import fitz
except ImportError:
    print(json.dumps({"code": "PDF_PREPROCESSOR_UNAVAILABLE"}), file=sys.stderr)
    raise SystemExit(2)


def fail(code: str) -> None:
    print(json.dumps({"code": code}), file=sys.stderr)
    raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--signer-name", required=True)
    parser.add_argument("--signature-method", choices=("typed", "drawn"), required=True)
    parser.add_argument("--signature-image")
    parser.add_argument("--signed-at", required=True)
    parser.add_argument("--audit-receipt", required=True)
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--width", type=float, required=True)
    parser.add_argument("--height", type=float, required=True)
    return parser.parse_args()


def normalized_rect(page: fitz.Page, args: argparse.Namespace) -> fitz.Rect:
    bounds = page.rect
    values = (args.x, args.y, args.width, args.height)
    if any(value < 0 or value > 1 for value in values):
        fail("PDF_PREPROCESSING_FAILED")
    if args.width < 0.05 or args.height < 0.025:
        fail("PDF_PREPROCESSING_FAILED")
    if args.x + args.width > 1 or args.y + args.height > 1:
        fail("PDF_PREPROCESSING_FAILED")
    return fitz.Rect(
        bounds.x0 + bounds.width * args.x,
        bounds.y0 + bounds.height * args.y,
        bounds.x0 + bounds.width * (args.x + args.width),
        bounds.y0 + bounds.height * (args.y + args.height),
    )


def display_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("PDF_PREPROCESSING_FAILED")
    return parsed.strftime("%b %d, %Y")


def apply_signature(args: argparse.Namespace) -> None:
    try:
        document = fitz.open(Path(args.input))
    except Exception:
        fail("PDF_INVALID")
    try:
        if document.needs_pass:
            fail("PDF_ENCRYPTED")
        if document.page_count < 1:
            fail("PDF_INVALID")

        page = document.load_page(0)
        signature_rect = normalized_rect(page, args)
        page.draw_rect(
            signature_rect,
            color=(0.10, 0.25, 0.19),
            fill=(1, 1, 1),
            width=0.8,
            overlay=True,
        )

        if args.signature_method == "drawn":
            if not args.signature_image:
                fail("PDF_PREPROCESSING_FAILED")
            try:
                image_bytes = Path(args.signature_image).read_bytes()
                page.insert_image(
                    signature_rect + (3, 3, -3, -3),
                    stream=image_bytes,
                    keep_proportion=True,
                    overlay=True,
                )
            except Exception:
                fail("PDF_PREPROCESSING_FAILED")
        else:
            safe_name = html.escape(args.signer_name)
            page.insert_htmlbox(
                signature_rect + (5, 2, -5, -2),
                (
                    "<div style='font-family: cursive; font-size: 16pt; "
                    "font-style: italic; color: #173f31;'>"
                    f"{safe_name}</div>"
                ),
                scale_low=0.6,
                overlay=True,
            )

        page_width = page.rect.width
        date_rect = fitz.Rect(
            page_width * 0.67,
            signature_rect.y0,
            page_width * 0.91,
            signature_rect.y1,
        )
        page.draw_rect(
            date_rect,
            color=(0.10, 0.25, 0.19),
            fill=(1, 1, 1),
            width=0.8,
            overlay=True,
        )
        page.insert_textbox(
            date_rect + (5, 5, -5, -5),
            f"{display_date(args.signed_at)}\nElectronically signed",
            fontname="helv",
            fontsize=8.5,
            color=(0.08, 0.14, 0.22),
            align=fitz.TEXT_ALIGN_LEFT,
            overlay=True,
        )

        metadata = document.metadata or {}
        metadata.update(
            {
                "author": args.signer_name,
                "subject": "Electronically signed onboarding document",
                "keywords": (
                    "electronic-signature,"
                    f"audit-receipt:{args.audit_receipt},"
                    f"signed-at:{args.signed_at}"
                ),
            }
        )
        document.set_metadata(metadata)
        document.save(
            Path(args.output),
            garbage=4,
            deflate=True,
            clean=True,
            # The service uses a deterministic storage key for each immutable
            # student/template/version tuple. Preserve the source document ID
            # so concurrent retries also produce byte-identical output.
            no_new_id=True,
        )
    except SystemExit:
        raise
    except Exception:
        fail("PDF_PREPROCESSING_FAILED")
    finally:
        document.close()


if __name__ == "__main__":
    apply_signature(parse_args())
