"""Bounded document preprocessing implemented directly in Python."""

from .processing import (
    DocumentPreprocessingError,
    DocumentPreprocessingOptions,
    NormalizedImageRegion,
    PreparedDocument,
    PreparedImage,
    SignatureBox,
    SigningInput,
    create_signed_onboarding_pdf,
    extract_student_document_image_region,
    preprocess_student_document,
    select_page_indexes,
)

__all__ = [
    "DocumentPreprocessingError",
    "DocumentPreprocessingOptions",
    "NormalizedImageRegion",
    "PreparedDocument",
    "PreparedImage",
    "SignatureBox",
    "SigningInput",
    "create_signed_onboarding_pdf",
    "extract_student_document_image_region",
    "preprocess_student_document",
    "select_page_indexes",
]
