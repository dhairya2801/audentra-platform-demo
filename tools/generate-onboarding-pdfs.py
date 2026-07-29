"""Generate the tenant onboarding document packet and signature metadata."""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.acroform import AcroForm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps" / "web" / "public" / "documents" / "onboarding"
PAGE_WIDTH, PAGE_HEIGHT = letter


def footer_for(tenant: dict[str, str]):
    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setStrokeColor(HexColor("#D8DED8"))
        canvas.line(0.75 * inch, 0.56 * inch, 7.75 * inch, 0.56 * inch)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(HexColor("#66756D"))
        canvas.drawString(
            0.75 * inch,
            0.39 * inch,
            f"{tenant['name']} - Student Enrollment",
        )
        canvas.drawRightString(
            7.75 * inch,
            0.39 * inch,
            f"Page {document.page}",
        )
        canvas.restoreState()

    return footer


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "AsterTitle",
            parent=base["Title"],
            fontName="Times-Bold",
            fontSize=22,
            leading=25,
            textColor=HexColor("#173F31"),
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "AsterSubtitle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=HexColor("#9A6D13"),
            spaceAfter=14,
        ),
        "heading": ParagraphStyle(
            "AsterHeading",
            parent=base["Heading2"],
            fontName="Times-Bold",
            fontSize=13,
            leading=16,
            textColor=HexColor("#173F31"),
            spaceBefore=9,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "AsterBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=HexColor("#26352E"),
            spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "AsterSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=10,
            textColor=HexColor("#5E6C65"),
        ),
    }


def signature_table(style: dict[str, ParagraphStyle], field_name: str) -> Table:
    table = Table(
        [
            [
                Paragraph(
                    "<b>Student signature</b><br/>"
                    "Your signature will be placed in this box after confirmation.",
                    style["small"],
                ),
                Paragraph("<b>Date signed</b><br/>Applied by the portal", style["small"]),
            ],
            ["", ""],
        ],
        colWidths=[4.5 * inch, 2.0 * inch],
        rowHeights=[0.42 * inch, 0.58 * inch],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, HexColor("#739181")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#CBD7D0")),
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EFF5F1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    table._aster_field_name = field_name  # type: ignore[attr-defined]
    return table


def build_ferpa(
    path: Path,
    style: dict[str, ParagraphStyle],
    tenant: dict[str, str],
) -> None:
    short_upper = tenant["shortName"].upper()
    story = [
        Paragraph("FERPA Information Release Authorization", style["title"]),
        Paragraph(
            f"{short_upper} UNIVERSITY - OPTIONAL STUDENT AUTHORIZATION",
            style["subtitle"],
        ),
        Paragraph(
            "The Family Educational Rights and Privacy Act protects the privacy "
            f"of student education records. This authorization lets {tenant['name']} "
            "share only the record categories and with only the people you selected "
            "during onboarding.",
            style["body"],
        ),
        Paragraph("What this authorization covers", style["heading"]),
        Paragraph(
            "Your saved onboarding choices identify each authorized person, their "
            "relationship to you, the permitted record categories, the purpose, and "
            f"the expiration rule. {tenant['shortName']} will verify identity before "
            "releasing records.",
            style["body"],
        ),
        Paragraph("Your choices and rights", style["heading"]),
        Paragraph(
            "This authorization is voluntary. You may refuse to authorize disclosure, "
            "limit the covered categories, or revoke future disclosure through the "
            "Registrar. Revocation does not undo a disclosure already made in reliance "
            "on a valid authorization.",
            style["body"],
        ),
        Paragraph(
            "This authorization does not permit an authorized person to make enrollment, "
            "academic, financial, housing, conduct, or medical decisions for you.",
            style["body"],
        ),
        Paragraph("Student confirmation", style["heading"]),
        Paragraph(
            "By signing, I confirm that I reviewed the people, scopes, purpose, and "
            f"expiration shown in the portal. I authorize {tenant['name']} to disclose "
            "the selected education records as described.",
            style["body"],
        ),
        Spacer(1, 0.08 * inch),
        KeepTogether(signature_table(style, "ferpa_student_signature")),
        Spacer(1, 0.12 * inch),
        Paragraph(
            f"Reference: Registrar - {tenant['registrarEmail']} - This "
            f"{tenant['shortName']} form is a portal template and is not the East-West "
            "University form used as a design reference.",
            style["small"],
        ),
    ]
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.72 * inch,
        title=f"{tenant['name']} FERPA Information Release Authorization",
        author=tenant["name"],
    )
    footer = footer_for(tenant)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def build_enrollment(
    path: Path,
    style: dict[str, ParagraphStyle],
    tenant: dict[str, str],
) -> None:
    short_upper = tenant["shortName"].upper()
    story = [
        Paragraph("Enrollment Information Acknowledgment", style["title"]),
        Paragraph(
            f"{short_upper} UNIVERSITY - ONBOARDING PACKET",
            style["subtitle"],
        ),
        Paragraph(
            "This acknowledgment records that you reviewed the information supplied "
            "during onboarding. It does not replace an admission offer decision, a "
            "housing contract, a financial-aid agreement, or a payment authorization.",
            style["body"],
        ),
        Paragraph("Information reviewed", style["heading"]),
        Paragraph(
            "Identity and contact details; permanent address; housing and roommate "
            "preferences; campus-life interests; emergency contacts; and any optional "
            "family permissions selected in the portal.",
            style["body"],
        ),
        Paragraph("Accuracy and updates", style["heading"]),
        Paragraph(
            "I confirm that the information is accurate to the best of my knowledge. "
            "I understand that I can update eligible profile fields and contact the "
            "responsible university office when an official record requires correction.",
            style["body"],
        ),
        Paragraph("Electronic signature consent", style["heading"]),
        Paragraph(
            "I agree to use an electronic signature for this onboarding packet. I "
            "understand that the portal records the document version, signature method, "
            "date, and audit receipt associated with this confirmation.",
            style["body"],
        ),
        Spacer(1, 0.08 * inch),
        KeepTogether(signature_table(style, "enrollment_student_signature")),
        Spacer(1, 0.12 * inch),
        Paragraph(
            f"Questions: Enrollment Services - {tenant['supportEmail']}",
            style["small"],
        ),
    ]
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.72 * inch,
        title=f"{tenant['name']} Enrollment Information Acknowledgment",
        author=tenant["name"],
    )
    footer = footer_for(tenant)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def render_preview(pdf_path: Path, preview_path: Path) -> None:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[0]
        try:
            image = page.render(scale=2.2).to_pil()
            image.save(preview_path, format="PNG", optimize=True)
        finally:
            page.close()
    finally:
        document.close()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    style = styles()
    tenants = [
        {
            "slug": "aster",
            "name": "Aster University",
            "shortName": "Aster",
            "supportEmail": "enrollment@aster.edu",
            "registrarEmail": "registrar@aster.edu",
        },
        {
            "slug": "harvard",
            "name": "Harvard University",
            "shortName": "Harvard",
            "supportEmail": "studentservices@harvard.edu",
            "registrarEmail": "registrar@harvard.edu",
        },
    ]
    manifest: dict[str, list[dict[str, object]]] = {}
    for tenant in tenants:
        documents = [
            {
                "id": "ferpa_release",
                "name": "FERPA Information Release",
                "file": f"{tenant['slug']}-ferpa-release.pdf",
                "preview": f"{tenant['slug']}-ferpa-release-page-1.png",
                "signatureBox": {
                    "x": 0.098,
                    "y": 0.488,
                    "width": 0.53,
                    "height": 0.054,
                    "page": 1,
                },
            },
            {
                "id": "enrollment_acknowledgment",
                "name": "Enrollment Information Acknowledgment",
                "file": f"{tenant['slug']}-enrollment-acknowledgment.pdf",
                "preview": (
                    f"{tenant['slug']}-enrollment-acknowledgment-page-1.png"
                ),
                "signatureBox": {
                    "x": 0.098,
                    "y": 0.447,
                    "width": 0.53,
                    "height": 0.054,
                    "page": 1,
                },
            },
        ]
        ferpa_pdf = OUTPUT / str(documents[0]["file"])
        enrollment_pdf = OUTPUT / str(documents[1]["file"])
        build_ferpa(ferpa_pdf, style, tenant)
        build_enrollment(enrollment_pdf, style, tenant)
        render_preview(ferpa_pdf, OUTPUT / str(documents[0]["preview"]))
        render_preview(
            enrollment_pdf,
            OUTPUT / str(documents[1]["preview"]),
        )
        manifest[tenant["slug"]] = documents
    (OUTPUT / "manifest.json").write_text(
        json.dumps({"tenants": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(tenants) * 2} onboarding PDFs in {OUTPUT}")


if __name__ == "__main__":
    main()
