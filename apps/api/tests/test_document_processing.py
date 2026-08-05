from __future__ import annotations

import asyncio
import base64
import struct
import subprocess
import sys
import zlib
from io import BytesIO

import fitz  # type: ignore[import-untyped]
import pytest
from PIL import Image, ImageOps, PngImagePlugin

from audentra.domain.documents import classify_extraction_failure
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
from audentra.integrations.ai.provider import ProviderCompletionError

SAFE_NORMALIZATION_RSS_GROWTH_BYTES = 128 * 1024 * 1024
_MEMORY_PROBE = r"""
import asyncio
import ctypes
import sys
import threading

from audentra.infrastructure.documents import (
    DocumentPreprocessingOptions,
    preprocess_student_document,
)

if sys.platform == "win32":
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    def current_rss_bytes():
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        succeeded = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        )
        if not succeeded:
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.WorkingSetSize
else:
    import os
    import resource

    def current_rss_bytes():
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm", encoding="ascii") as stats:
                return int(stats.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return maximum_rss if sys.platform == "darwin" else maximum_rss * 1024

payload = sys.stdin.buffer.read()
baseline_rss = current_rss_bytes()
peak_rss = baseline_rss
stop_sampling = threading.Event()

def sample_memory():
    global peak_rss
    while not stop_sampling.wait(0.001):
        peak_rss = max(peak_rss, current_rss_bytes())

sampler = threading.Thread(target=sample_memory, daemon=True)
sampler.start()
try:
    asyncio.run(
        preprocess_student_document(
            payload,
            sys.argv[1],
            DocumentPreprocessingOptions(max_image_dimension=2_048, jpeg_quality=88),
        )
    )
finally:
    stop_sampling.set()
    sampler.join()
peak_rss = max(peak_rss, current_rss_bytes())

print(baseline_rss, peak_rss)
"""


def _pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    try:
        for index in range(page_count):
            page = document.new_page()
            page.insert_text((72, 72), f"Official Transcript page {index + 1}")
        return bytes(document.tobytes(no_new_id=True))
    finally:
        document.close()


def _header_only_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def _normalization_memory(data: bytes, mime_type: str) -> tuple[int, int]:
    completed = subprocess.run(  # noqa: S603 - the current locked Python executable is trusted.
        [sys.executable, "-c", _MEMORY_PROBE, mime_type],
        input=data,
        capture_output=True,
        check=True,
        timeout=60,
    )
    baseline_rss, peak_rss = completed.stdout.split()
    return int(baseline_rss), int(peak_rss)


def test_source_image_is_normalized_to_a_bounded_rgb_jpeg() -> None:
    source = Image.new("RGBA", (3_000, 1_000), (32, 96, 160, 128))
    encoded = BytesIO()
    source.save(encoded, format="PNG")
    original = encoded.getvalue()

    prepared = asyncio.run(
        preprocess_student_document(
            original,
            "image/png",
            DocumentPreprocessingOptions(max_image_dimension=2_048, jpeg_quality=88),
        )
    )

    assert prepared.extracted_text == ""
    assert prepared.page_count is None
    assert prepared.images[0].mime_type == "image/jpeg"
    normalized = base64.b64decode(prepared.images[0].data_base64)
    assert original.startswith(b"\x89PNG\r\n\x1a\n")
    assert normalized != original
    with Image.open(BytesIO(normalized)) as result:
        assert result.format == "JPEG"
        assert result.mode == "RGB"
        assert max(result.size) == 2_048
        assert result.size == (prepared.images[0].width, prepared.images[0].height)


def test_large_transparent_png_is_resized_in_place_before_rgb_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Image.new("RGBA", (4_000, 3_000), (32, 96, 160, 128)) as source:
        encoded = BytesIO()
        source.save(encoded, format="PNG")
    original = encoded.getvalue()

    baseline_rss, peak_rss = _normalization_memory(original, "image/png")
    assert 0 < peak_rss - baseline_rss < SAFE_NORMALIZATION_RSS_GROWTH_BYTES

    transpose_modes: list[bool] = []
    allocation_events: list[tuple[str, tuple[int, int]]] = []
    original_transpose = ImageOps.exif_transpose
    original_thumbnail = Image.Image.thumbnail
    original_new = Image.new

    def tracking_transpose(image: Image.Image, *, in_place: bool = False) -> Image.Image | None:
        transpose_modes.append(in_place)
        if in_place:
            original_transpose(image, in_place=True)
            return None
        return original_transpose(image)

    def tracking_thumbnail(
        image: Image.Image,
        size: tuple[float, float],
        resample: Image.Resampling = Image.Resampling.BICUBIC,
        reducing_gap: float | None = 2.0,
    ) -> None:
        original_thumbnail(image, size, resample, reducing_gap)
        allocation_events.append(("thumbnail", image.size))

    def tracking_new(
        mode: str,
        size: tuple[int, int] | list[int],
        color: float | tuple[float, ...] | str | None = 0,
    ) -> Image.Image:
        dimensions = (int(size[0]), int(size[1]))
        allocation_events.append(("new", dimensions))
        return original_new(mode, size, color)

    monkeypatch.setattr(ImageOps, "exif_transpose", tracking_transpose)
    monkeypatch.setattr(Image.Image, "thumbnail", tracking_thumbnail)
    monkeypatch.setattr(Image, "new", tracking_new)

    prepared = asyncio.run(
        preprocess_student_document(
            original,
            "image/png",
            DocumentPreprocessingOptions(max_image_dimension=2_048, jpeg_quality=88),
        )
    )

    assert transpose_modes == [True]
    assert allocation_events[:3] == [
        ("thumbnail", (2_730, 2_048)),
        ("thumbnail", (2_048, 1_536)),
        ("new", (2_048, 1_536)),
    ]
    normalized = base64.b64decode(prepared.images[0].data_base64)
    with Image.open(BytesIO(normalized)) as result:
        assert result.mode == "RGB"
        assert result.size == (2_048, 1_536)


def test_live_12mp_phone_jpeg_shape_stays_below_preview_memory_budget() -> None:
    with Image.new("RGB", (3_000, 4_000), (32, 96, 160)) as source:
        encoded = BytesIO()
        source.save(encoded, format="JPEG", quality=88)
    original = encoded.getvalue()

    baseline_rss, peak_rss = _normalization_memory(original, "image/jpeg")
    assert 0 < peak_rss - baseline_rss < SAFE_NORMALIZATION_RSS_GROWTH_BYTES

    prepared = asyncio.run(
        preprocess_student_document(
            original,
            "image/jpeg",
            DocumentPreprocessingOptions(max_image_dimension=2_048, jpeg_quality=88),
        )
    )

    normalized = base64.b64decode(prepared.images[0].data_base64)
    with Image.open(BytesIO(normalized)) as result:
        assert result.size == (1_500, 2_000)
        assert result.height > result.width
        assert result.getexif().get(274) is None


def test_oversized_decoded_image_is_rejected_before_raster_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_load(_image: object) -> None:
        raise AssertionError("oversized raster must not be decoded")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", reject_load)

    with pytest.raises(DocumentPreprocessingError) as raised:
        asyncio.run(preprocess_student_document(_header_only_png(5_000, 3_000), "image/png"))

    assert raised.value.code == "IMAGE_INVALID"


def test_source_image_applies_exif_orientation_before_normalizing() -> None:
    source = Image.new("RGB", (120, 60), (32, 96, 160))
    exif = Image.Exif()
    exif[274] = 6
    encoded = BytesIO()
    source.save(encoded, format="JPEG", exif=exif)

    prepared = asyncio.run(preprocess_student_document(encoded.getvalue(), "image/jpeg"))
    normalized = base64.b64decode(prepared.images[0].data_base64)

    with Image.open(BytesIO(normalized)) as result:
        assert result.size == (60, 120)
        assert result.getexif().get(274) is None


def test_corrupt_source_image_returns_only_the_stable_error_code() -> None:
    with pytest.raises(DocumentPreprocessingError) as raised:
        asyncio.run(preprocess_student_document(b"\x89PNG\r\n\x1a\nnot-an-image", "image/png"))

    assert raised.value.code == "IMAGE_INVALID"
    assert "not-an-image" not in str(raised.value)


@pytest.mark.parametrize(
    "message",
    [
        "AI provider returned no valid JSON extraction",
        "AI provider returned incomplete JSON extraction",
        "OpenRouter returned an incomplete structured extraction",
    ],
)
def test_structured_response_failures_trigger_the_automatic_retry(message: str) -> None:
    failure = classify_extraction_failure(ProviderCompletionError(message))

    assert failure.code == "invalid_response"
    assert failure.retryable is True
    assert failure.automatic_retryable is True


def test_corrupt_image_failure_is_terminal_and_student_safe() -> None:
    failure = classify_extraction_failure(
        DocumentPreprocessingError("IMAGE_INVALID", "The uploaded image is invalid or unreadable")
    )

    assert failure.code == "unsupported_capability"
    assert failure.retryable is False
    assert failure.automatic_retryable is False


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
