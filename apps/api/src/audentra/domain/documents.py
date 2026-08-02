"""Document validation, retry classification, and deterministic PDF helpers."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

from audentra.core.errors import ApiError, BadRequestError

MAXIMUM_DOCUMENT_BYTES = 10_485_760
ALLOWED_MIME_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
ALLOWED_DOCUMENT_CATEGORIES = frozenset(
    {"identity", "residency", "transcript", "financial_aid", "health", "consent", "other"}
)


@dataclass(frozen=True, slots=True)
class ExtractionFailure:
    code: str
    retryable: bool
    automatic_retryable: bool
    warning: str


def safe_file_name(value: str) -> str:
    name = re.sub(r'[\\/\x00-\x1f\x7f"]', "_", value).strip()[:255]
    if not name:
        raise BadRequestError("INVALID_FILE_NAME", "The document file name is invalid")
    return name


def validate_document_upload(file_name: str, mime_type: str, category: str, content: bytes) -> str:
    """Validate metadata and magic bytes, returning a normalized filename."""

    name = safe_file_name(file_name)
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ApiError(415, "UNSUPPORTED_DOCUMENT_TYPE", "Use a PDF, JPEG, or PNG document")
    if category not in ALLOWED_DOCUMENT_CATEGORIES:
        raise BadRequestError("INVALID_DOCUMENT_CATEGORY", "Choose a valid document category")
    if not content or len(content) > MAXIMUM_DOCUMENT_BYTES:
        raise ApiError(413, "DOCUMENT_TOO_LARGE", "Documents must be no larger than 10 MB")
    valid = (
        (mime_type == "application/pdf" and content.startswith(b"%PDF-"))
        or (mime_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"))
        or (mime_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
    )
    if not valid:
        raise ApiError(
            415,
            "FILE_SIGNATURE_MISMATCH",
            "The file contents do not match the selected PDF, JPEG, or PNG type",
        )
    return name


def document_type_for_category(category: str) -> str:
    return {
        "identity": "identity",
        "residency": "residency",
        "transcript": "transcript",
        "financial_aid": "financial_aid",
        "health": "immunization",
        "consent": "ferpa",
        "other": "other",
    }.get(category, "other")


def can_retry_extraction(extraction: object) -> bool:
    if not isinstance(extraction, dict):
        return False
    return extraction.get("status") == "pending_configuration" or (
        extraction.get("status") == "failed" and extraction.get("retryable") is not False
    )


def classify_extraction_failure(error: BaseException) -> ExtractionFailure:
    detail = f"{type(error).__name__} {error}".lower()
    if re.search(r"timeout|timed out|abort|etimedout|deadline", detail):
        return ExtractionFailure(
            "timeout",
            True,
            True,
            "Parsing took too long. You can retry without uploading the file again.",
        )
    if re.search(r"\b413\b|tokens per minute|rate_limit_exceeded|request too large", detail):
        return ExtractionFailure(
            "provider_unavailable",
            True,
            False,
            "The parsing request exceeded the provider's current token allowance. You can retry "
            "the stored file after the parsing configuration is adjusted.",
        )
    if re.search(
        r"\b429\b|\b5\d\d\b|network|fetch failed|econn|enotfound|temporar(?:y|ily)|unavailable",
        detail,
    ):
        return ExtractionFailure(
            "provider_unavailable",
            True,
            True,
            "The parsing service is temporarily unavailable. You can retry without uploading "
            "the file again.",
        )
    if re.search(r"empty (?:completion|structured extraction)|no readable content", detail):
        return ExtractionFailure(
            "invalid_response",
            True,
            True,
            "The parsing service returned an unusable result. You can retry without uploading "
            "the file again.",
        )
    if isinstance(error, (SyntaxError, ValueError)) or re.search(
        r"invalid json|unexpected token|invalid response", detail
    ):
        return ExtractionFailure(
            "invalid_response",
            True,
            False,
            "The parsing service returned an unusable result. You can retry without uploading "
            "the file again.",
        )
    if re.search(
        r"\b400\b|\b404\b|\b415\b|\b422\b|unsupported|not supported|capability|file-parser", detail
    ):
        return ExtractionFailure(
            "unsupported_capability",
            False,
            False,
            "The current parsing setup cannot process this file. The original file remains "
            "available for staff review.",
        )
    return ExtractionFailure(
        "unknown",
        True,
        False,
        "The parsing attempt could not be completed. You can retry without uploading the "
        "file again.",
    )


def failed_extraction(file_name: str, category: str, error: BaseException) -> dict[str, object]:
    failure = classify_extraction_failure(error)
    return {
        "status": "failed",
        "documentType": document_type_for_category(category)
        if category != "other"
        else ("transcript" if "transcript" in file_name.lower() else "other"),
        "summary": (
            "The original file was stored, but structured extraction could not be completed."
        ),
        "studentName": None,
        "institutionName": None,
        "issueDate": None,
        "academicTerm": None,
        "fields": [],
        "courses": [],
        "visualRegions": [],
        "warnings": [failure.warning],
        "model": None,
        "provider": "local",
        "processedAt": "2026-07-24T12:00:00.000Z",
        "verifiedAt": None,
        "failureCode": failure.code,
        "retryable": failure.retryable,
    }


def deterministic_document_id(value: str) -> str:
    data = bytearray(hashlib.sha256(value.encode()).digest()[:16])
    data[6] = (data[6] & 0x0F) | 0x50
    data[8] = (data[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(data)))


def create_signed_pdf(title: str, signer_name: str, signed_at: str, audit_receipt: str) -> bytes:
    """Create a small, standards-compliant PDF without a blocking third-party renderer."""

    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    lines = [
        title,
        f"Electronically signed by {signer_name}",
        f"Signed at {signed_at}",
        audit_receipt,
    ]
    commands = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -24 Td")
        commands.append(f"({escape(line)}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            b"<< /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(pdf)
