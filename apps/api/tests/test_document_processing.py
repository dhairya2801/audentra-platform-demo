from __future__ import annotations

import asyncio
import base64
from io import BytesIO

import fitz  # type: ignore[import-untyped]
import pytest
from PIL import Image

from audentra.infrastructure.documents import (
    DocumentPreprocessingError,
    DocumentPreprocessingOptions,
    NormalizedImageRegion,
    SignatureBox,
    SigningInput,
    create_signed_onboarding_pdf,
    extract_student_document_image_region,
    preprocess_student_document,
    select_page_indexes,
)


def _pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    try:
        for index in range(page_count):
            page = document.new_page()
            page.insert_text((72, 72), f"Official Transcript page {index + 1}")
        return bytes(document.tobytes(no_new_id=True))
    finally:
        document.close()


def test_source_image_is_passed_through_for_multimodal_input() -> None:
    prepared = asyncio.run(preprocess_student_document(b"\x89PNG", "image/png"))

    assert prepared.extracted_text == ""
    assert prepared.page_count is None
    assert prepared.images[0].mime_type == "image/png"
    assert prepared.images[0].data_base64 == base64.b64encode(b"\x89PNG").decode()


def test_unreadable_pdf_returns_only_the_stable_error_code() -> None:
    with pytest.raises(DocumentPreprocessingError) as raised:
        asyncio.run(preprocess_student_document(b"%PDF-not-a-real-pdf", "application/pdf"))

    assert raised.value.code == "PDF_INVALID"
    assert "%PDF" not in str(raised.value)


def test_pdf_text_and_evenly_sampled_images_are_bounded() -> None:
    prepared = asyncio.run(
        preprocess_student_document(
            _pdf_bytes(6),
            "application/pdf",
            DocumentPreprocessingOptions(
                max_text_pages=2,
                max_image_pages=3,
                max_image_dimension=640,
            ),
        )
    )

    assert prepared.page_count == 6
    assert prepared.rendered_page_numbers == (1, 3, 6)
    assert prepared.text_truncated is True
    assert "Official Transcript page 1" in prepared.extracted_text
    assert len(prepared.images) == 3
    assert all(max(image.width or 0, image.height or 0) == 640 for image in prepared.images)
    assert select_page_indexes(10, 4) == [0, 3, 6, 9]


def test_normalized_region_becomes_a_640_by_800_jpeg() -> None:
    source = Image.new("RGB", (200, 120), (32, 96, 160))
    encoded = BytesIO()
    source.save(encoded, format="PNG")
    portrait = asyncio.run(
        extract_student_document_image_region(
            encoded.getvalue(),
            "image/png",
            NormalizedImageRegion(x=0.25, y=0.1, width=0.5, height=0.8),
        )
    )

    with Image.open(BytesIO(portrait)) as result:
        assert result.format == "JPEG"
        assert result.size == (640, 800)


def test_typed_signature_pdf_is_valid_and_deterministic() -> None:
    signing_input = SigningInput(
        template_bytes=_pdf_bytes(),
        signer_name="Alex Morgan",
        signature_method="typed",
        signed_at="2026-07-24T12:00:00.000Z",
        audit_receipt="onboarding-signature-test",
        signature_box=SignatureBox(x=0.098, y=0.488, width=0.53, height=0.054),
    )

    signed = asyncio.run(create_signed_onboarding_pdf(signing_input))
    replay = asyncio.run(create_signed_onboarding_pdf(signing_input))
    prepared = asyncio.run(
        preprocess_student_document(
            signed,
            "application/pdf",
            DocumentPreprocessingOptions(max_image_pages=0),
        )
    )

    assert signed.startswith(b"%PDF-")
    assert signed == replay
    assert "Alex Morgan" in prepared.extracted_text
    assert "Electronically signed" in prepared.extracted_text
