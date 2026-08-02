"""Safe, bounded PDF/image operations without subprocess or Node.js bridges."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import math
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Literal, cast

import fitz  # type: ignore[import-untyped]
from PIL import Image, ImageOps, UnidentifiedImageError

MAXIMUM_INPUT_BYTES = 10 * 1024 * 1024
MAXIMUM_IMAGE_PIXELS = 50_000_000
SIGNED_PDF_MINIMUM_BYTES = 100
_AUDIT_RECEIPT = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")

PreprocessingErrorCode = Literal[
    "PDF_PREPROCESSOR_UNAVAILABLE",
    "PDF_INVALID",
    "PDF_ENCRYPTED",
    "PDF_PREPROCESSING_FAILED",
]

_SAFE_MESSAGES: dict[PreprocessingErrorCode, str] = {
    "PDF_PREPROCESSOR_UNAVAILABLE": "The local PDF preprocessor is not available",
    "PDF_INVALID": "The uploaded PDF is invalid or unreadable",
    "PDF_ENCRYPTED": "Password-protected PDFs cannot be parsed",
    "PDF_PREPROCESSING_FAILED": "The PDF could not be preprocessed safely",
}


class DocumentPreprocessingError(RuntimeError):
    """A stable, non-sensitive document processing failure."""

    def __init__(self, code: PreprocessingErrorCode, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or _SAFE_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class DocumentPreprocessingOptions:
    max_text_characters: int = 40_000
    max_text_pages: int = 20
    max_image_pages: int = 6
    max_image_dimension: int = 1_400
    jpeg_quality: int = 72

    def resolved(self) -> DocumentPreprocessingOptions:
        return DocumentPreprocessingOptions(
            max_text_characters=_bounded_integer(self.max_text_characters, 40_000, 2_000, 100_000),
            max_text_pages=_bounded_integer(self.max_text_pages, 20, 1, 100),
            max_image_pages=_bounded_integer(self.max_image_pages, 6, 0, 8),
            max_image_dimension=_bounded_integer(self.max_image_dimension, 1_400, 640, 2_048),
            jpeg_quality=_bounded_integer(self.jpeg_quality, 72, 40, 90),
        )


@dataclass(frozen=True, slots=True)
class PreparedImage:
    page_number: int | None
    mime_type: str
    data_base64: str
    width: int | None
    height: int | None

    def to_contract(self) -> dict[str, object]:
        return {
            "pageNumber": self.page_number,
            "mimeType": self.mime_type,
            "dataBase64": self.data_base64,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    extracted_text: str
    page_count: int | None
    rendered_page_numbers: tuple[int, ...]
    text_truncated: bool
    images: tuple[PreparedImage, ...]

    def to_contract(self) -> dict[str, object]:
        return {
            "extractedText": self.extracted_text,
            "pageCount": self.page_count,
            "renderedPageNumbers": list(self.rendered_page_numbers),
            "textTruncated": self.text_truncated,
            "images": [image.to_contract() for image in self.images],
        }


@dataclass(frozen=True, slots=True)
class NormalizedImageRegion:
    x: float
    y: float
    width: float
    height: float
    page_number: int | None = None
    kind: Literal["profile_photo"] = "profile_photo"

    def __post_init__(self) -> None:
        if self.kind != "profile_photo":
            raise TypeError("A profile_photo visual region is required")
        values = (self.x, self.y, self.width, self.height)
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
            raise TypeError("Identity-photo bounds must be between 0 and 1")
        if (
            self.width < 0.02
            or self.height < 0.02
            or self.x + self.width > 1.001
            or self.y + self.height > 1.001
        ):
            raise TypeError("Identity-photo bounds are invalid")
        if self.page_number is not None and not 1 <= self.page_number <= 8:
            raise TypeError("Identity-photo page number is invalid")


@dataclass(frozen=True, slots=True)
class SignatureBox:
    x: float
    y: float
    width: float
    height: float
    page_number: int = 1

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
            raise TypeError("Normalized signature bounds are required")
        if self.width < 0.05 or self.height < 0.025:
            raise TypeError("Normalized signature bounds are too small")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise TypeError("Normalized signature bounds exceed the page")
        # Legacy signing is intentionally restricted to the first page.
        if self.page_number != 1:
            raise TypeError("Onboarding signatures must be placed on page 1")


@dataclass(frozen=True, slots=True)
class SigningInput:
    template_bytes: bytes
    signer_name: str
    signature_method: Literal["typed", "drawn"]
    signed_at: str
    audit_receipt: str
    signature_box: SignatureBox
    signature_image_data: str | None = None

    def __post_init__(self) -> None:
        if not SIGNED_PDF_MINIMUM_BYTES <= len(self.template_bytes) <= MAXIMUM_INPUT_BYTES:
            raise TypeError("A bounded PDF template is required")
        if not 2 <= len(self.signer_name.strip()) <= 240:
            raise TypeError("A bounded signer name is required")
        if self.signature_method not in {"typed", "drawn"}:
            raise TypeError("A supported signature method is required")
        _parse_signing_timestamp(self.signed_at)
        if not _AUDIT_RECEIPT.fullmatch(self.audit_receipt):
            raise TypeError("A bounded audit receipt is required")
        if self.signature_method == "drawn" and self.signature_image_data is None:
            raise TypeError("A PNG signature image is required")


def select_page_indexes(page_count: int, maximum: int) -> list[int]:
    """Evenly sample pages, preserving the legacy PyMuPDF algorithm."""

    if maximum <= 0:
        return []
    if page_count <= maximum:
        return list(range(page_count))
    if maximum == 1:
        return [0]
    selected = {round(index * (page_count - 1) / (maximum - 1)) for index in range(maximum)}
    return sorted(selected)


async def preprocess_student_document(
    data: bytes,
    mime_type: str,
    options: DocumentPreprocessingOptions | None = None,
) -> PreparedDocument:
    """Prepare an uploaded PDF or image on a worker thread."""

    _validate_document_input(data, mime_type)
    resolved = (options or DocumentPreprocessingOptions()).resolved()
    return await asyncio.to_thread(_preprocess_student_document, data, mime_type, resolved)


async def extract_student_document_image_region(
    data: bytes,
    mime_type: str,
    region: NormalizedImageRegion,
) -> bytes:
    """Crop a normalized profile-photo region into a bounded JPEG portrait."""

    _validate_document_input(data, mime_type)
    return await asyncio.to_thread(_extract_image_region, data, mime_type, region)


async def create_signed_onboarding_pdf(signing_input: SigningInput) -> bytes:
    """Apply a deterministic typed or drawn signature on a worker thread."""

    return await asyncio.to_thread(_create_signed_onboarding_pdf, signing_input)


def _preprocess_student_document(
    data: bytes,
    mime_type: str,
    options: DocumentPreprocessingOptions,
) -> PreparedDocument:
    if mime_type != "application/pdf":
        return PreparedDocument(
            extracted_text="",
            page_count=None,
            rendered_page_numbers=(),
            text_truncated=False,
            images=(
                PreparedImage(
                    page_number=None,
                    mime_type=mime_type,
                    data_base64=base64.b64encode(data).decode("ascii"),
                    width=None,
                    height=None,
                ),
            ),
        )

    document = _open_pdf(data)
    try:
        _validate_open_pdf(document)
        text_parts: list[str] = []
        text_length = 0
        text_truncated = False
        text_page_limit = min(document.page_count, options.max_text_pages)
        for index in range(text_page_limit):
            page_text = document.load_page(index).get_text("text", sort=True)
            page_text = page_text.replace("\x00", "").strip()
            if not page_text:
                continue
            section = f"\n\n--- Page {index + 1} ---\n{page_text}"
            remaining = options.max_text_characters - text_length
            if remaining <= 0:
                text_truncated = True
                break
            if len(section) > remaining:
                text_parts.append(section[:remaining])
                text_truncated = True
                break
            text_parts.append(section)
            text_length += len(section)
        if document.page_count > text_page_limit:
            text_truncated = True

        page_indexes = select_page_indexes(document.page_count, options.max_image_pages)
        images = tuple(
            _render_page(
                document.load_page(index),
                options.max_image_dimension,
                options.jpeg_quality,
            )
            for index in page_indexes
        )
        return PreparedDocument(
            extracted_text="".join(text_parts).strip(),
            page_count=document.page_count,
            rendered_page_numbers=tuple(index + 1 for index in page_indexes),
            text_truncated=text_truncated,
            images=images,
        )
    except DocumentPreprocessingError:
        raise
    except Exception as error:
        raise DocumentPreprocessingError("PDF_PREPROCESSING_FAILED") from error
    finally:
        document.close()


def _render_page(page: fitz.Page, max_dimension: int, jpeg_quality: int) -> PreparedImage:
    source = page.rect
    scale = min(max_dimension / max(source.width, source.height), 4.0)
    scale = max(scale, 0.5)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
    )
    image_bytes = pixmap.tobytes("jpeg", jpg_quality=jpeg_quality)
    # Calculate the digest here even though the public compatibility contract
    # omits it. Doing so catches an unexpected non-bytes renderer response.
    hashlib.sha256(image_bytes).digest()
    return PreparedImage(
        page_number=page.number + 1,
        mime_type="image/jpeg",
        data_base64=base64.b64encode(image_bytes).decode("ascii"),
        width=pixmap.width,
        height=pixmap.height,
    )


def _extract_image_region(
    data: bytes,
    mime_type: str,
    region: NormalizedImageRegion,
) -> bytes:
    image_bytes = data
    if mime_type == "application/pdf":
        document = _open_pdf(data)
        try:
            _validate_open_pdf(document)
            page_index = (region.page_number or 1) - 1
            if page_index >= document.page_count:
                raise DocumentPreprocessingError(
                    "PDF_PREPROCESSING_FAILED",
                    "The identity-photo page could not be rendered",
                )
            prepared = _render_page(document.load_page(page_index), 2_048, 88)
            image_bytes = base64.b64decode(prepared.data_base64)
        finally:
            document.close()

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAXIMUM_IMAGE_PIXELS:
                raise DocumentPreprocessingError(
                    "PDF_PREPROCESSING_FAILED",
                    "The identity-photo image has no readable dimensions",
                )
            left = min(width - 1, max(0, math.floor(region.x * width)))
            top = min(height - 1, max(0, math.floor(region.y * height)))
            crop_width = max(1, min(width - left, math.ceil(region.width * width)))
            crop_height = max(1, min(height - top, math.ceil(region.height * height)))
            cropped = image.crop((left, top, left + crop_width, top + crop_height))
            portrait = ImageOps.fit(
                cropped.convert("RGB"),
                (640, 800),
                method=Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            portrait.save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue()
    except DocumentPreprocessingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise DocumentPreprocessingError("PDF_PREPROCESSING_FAILED") from error


def _create_signed_onboarding_pdf(signing_input: SigningInput) -> bytes:
    document = _open_pdf(signing_input.template_bytes)
    try:
        _validate_open_pdf(document)
        page = document.load_page(0)
        bounds = page.rect
        box = signing_input.signature_box
        signature_rect = fitz.Rect(
            bounds.x0 + bounds.width * box.x,
            bounds.y0 + bounds.height * box.y,
            bounds.x0 + bounds.width * (box.x + box.width),
            bounds.y0 + bounds.height * (box.y + box.height),
        )
        page.draw_rect(
            signature_rect,
            color=(0.10, 0.25, 0.19),
            fill=(1, 1, 1),
            width=0.8,
            overlay=True,
        )

        if signing_input.signature_method == "drawn":
            image_bytes = _decode_signature_image(signing_input.signature_image_data)
            try:
                page.insert_image(
                    signature_rect + (3, 3, -3, -3),  # noqa: RUF005
                    stream=image_bytes,
                    keep_proportion=True,
                    overlay=True,
                )
            except Exception as error:
                raise DocumentPreprocessingError("PDF_PREPROCESSING_FAILED") from error
        else:
            safe_name = html.escape(signing_input.signer_name)
            page.insert_htmlbox(
                signature_rect + (5, 2, -5, -2),  # noqa: RUF005
                (
                    "<div style='font-family: cursive; font-size: 16pt; "
                    "font-style: italic; color: #173f31;'>"
                    f"{safe_name}</div>"
                ),
                scale_low=0.6,
                overlay=True,
            )

        date_rect = fitz.Rect(
            page.rect.width * 0.67,
            signature_rect.y0,
            page.rect.width * 0.91,
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
            date_rect + (5, 5, -5, -5),  # noqa: RUF005
            f"{_display_date(signing_input.signed_at)}\nElectronically signed",
            fontname="helv",
            fontsize=8.5,
            color=(0.08, 0.14, 0.22),
            align=fitz.TEXT_ALIGN_LEFT,
            overlay=True,
        )
        metadata = document.metadata or {}
        metadata.update(
            {
                "author": signing_input.signer_name,
                "subject": "Electronically signed onboarding document",
                "keywords": (
                    "electronic-signature,"
                    f"audit-receipt:{signing_input.audit_receipt},"
                    f"signed-at:{signing_input.signed_at}"
                ),
            }
        )
        document.set_metadata(metadata)
        signed = cast(
            bytes,
            document.tobytes(garbage=4, deflate=True, clean=True, no_new_id=True),
        )
        if (
            len(signed) < SIGNED_PDF_MINIMUM_BYTES
            or len(signed) > MAXIMUM_INPUT_BYTES
            or not signed.startswith(b"%PDF-")
        ):
            raise DocumentPreprocessingError("PDF_PREPROCESSING_FAILED")
        return signed
    except DocumentPreprocessingError:
        raise
    except Exception as error:
        raise DocumentPreprocessingError("PDF_PREPROCESSING_FAILED") from error
    finally:
        document.close()


def _open_pdf(data: bytes) -> fitz.Document:
    try:
        return fitz.open(stream=data, filetype="pdf")
    except Exception as error:
        raise DocumentPreprocessingError("PDF_INVALID") from error


def _validate_open_pdf(document: fitz.Document) -> None:
    if document.needs_pass:
        raise DocumentPreprocessingError("PDF_ENCRYPTED")
    if document.page_count < 1:
        raise DocumentPreprocessingError("PDF_INVALID")


def _validate_document_input(data: bytes, mime_type: str) -> None:
    if not isinstance(data, bytes):
        raise TypeError("Document preprocessing requires bytes")
    if mime_type not in {"application/pdf", "image/jpeg", "image/png"}:
        raise TypeError("Unsupported document MIME type")
    if not 1 <= len(data) <= MAXIMUM_INPUT_BYTES:
        raise TypeError("Document preprocessing input must be between 1 byte and 10 MB")


def _decode_signature_image(value: str | None) -> bytes:
    prefix = "data:image/png;base64,"
    if value is None or not value.startswith(prefix):
        raise TypeError("A PNG signature image is required")
    try:
        image_bytes = base64.b64decode(value.removeprefix(prefix), validate=True)
    except (binascii.Error, ValueError) as error:
        raise TypeError("A PNG signature image is required") from error
    if not 20 <= len(image_bytes) <= 100_000:
        raise TypeError("The signature image is outside the allowed size")
    return image_bytes


def _parse_signing_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise TypeError("A valid signing timestamp is required") from error


def _display_date(value: str) -> str:
    return _parse_signing_timestamp(value).strftime("%b %d, %Y")


def _bounded_integer(value: int, fallback: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return max(minimum, min(maximum, value))
