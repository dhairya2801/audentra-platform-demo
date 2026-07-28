"""Generate a deterministic multi-page transcript for local AI smoke tests."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


OUTPUT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "synthetic-multi-page-transcript.pdf"
)

TERMS = [
    (
        "Fall 2024",
        [
            ("CS 101", "Introduction to Computer Science", "4.0", "A"),
            ("MATH 151", "Calculus I", "4.0", "A-"),
            ("WRIT 101", "Academic Writing", "3.0", "B+"),
            ("HIST 110", "World History", "3.0", "A"),
            ("BIO 101", "General Biology", "4.0", "B+"),
            ("ART 105", "Visual Culture", "3.0", "A-"),
            ("ECON 101", "Principles of Economics", "3.0", "B"),
            ("UNI 100", "First-Year Seminar", "1.0", "A"),
        ],
    ),
    (
        "Spring 2025",
        [
            ("CS 201", "Data Structures", "4.0", "A-"),
            ("MATH 152", "Calculus II", "4.0", "B+"),
            ("PHYS 121", "University Physics I", "4.0", "B"),
            ("COMM 120", "Public Speaking", "3.0", "A"),
            ("PSYC 101", "Introduction to Psychology", "3.0", "A-"),
            ("STAT 201", "Applied Statistics", "3.0", "B+"),
            ("PHIL 130", "Ethics and Technology", "3.0", "A"),
            ("MUS 101", "Music and Society", "3.0", "B+"),
        ],
    ),
    (
        "Fall 2025",
        [
            ("CS 230", "Computer Organization", "4.0", "B+"),
            ("CS 250", "Database Systems", "4.0", "A"),
            ("MATH 220", "Discrete Mathematics", "3.0", "A-"),
            ("PHYS 122", "University Physics II", "4.0", "B"),
            ("SOC 101", "Introduction to Sociology", "3.0", "A"),
            ("ENGL 220", "Literature and Culture", "3.0", "B+"),
            ("BUS 210", "Organizational Behavior", "3.0", "A-"),
            ("SPAN 102", "Elementary Spanish II", "4.0", "B+"),
        ],
    ),
    (
        "Spring 2026",
        [
            ("CS 310", "Algorithms", "4.0", "A"),
            ("CS 320", "Operating Systems", "4.0", "A-"),
            ("CS 330", "Computer Networks", "4.0", "B+"),
            ("MATH 260", "Linear Algebra", "3.0", "A"),
            ("CHEM 101", "General Chemistry", "4.0", "B"),
            ("ANTH 101", "Cultural Anthropology", "3.0", "A-"),
            ("DES 210", "Human-Centered Design", "3.0", "A"),
            ("LAW 205", "Technology and Society", "3.0", "B+"),
        ],
    ),
]


def build() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title="Synthetic Multi-Page Academic Transcript",
        author="VV Edgent local test fixture",
    )
    story = []
    total_credits = 0.0

    for page_number, (term, courses) in enumerate(TERMS, start=1):
        story.extend(
            [
                Paragraph("NORTHSTAR COLLEGE", styles["Title"]),
                Paragraph("OFFICIAL ACADEMIC TRANSCRIPT — TEST FIXTURE", styles["Heading2"]),
                Spacer(1, 0.08 * inch),
                Paragraph(
                    "<b>Student:</b> Taylor Jordan &nbsp;&nbsp; "
                    "<b>Student ID:</b> NSC-2024-01872",
                    styles["BodyText"],
                ),
                Paragraph(
                    "<b>Program:</b> Bachelor of Science in Computer Science &nbsp;&nbsp; "
                    f"<b>Term:</b> {term}",
                    styles["BodyText"],
                ),
                Spacer(1, 0.14 * inch),
            ]
        )
        rows = [["Course", "Title", "Credits", "Grade"]]
        rows.extend([list(course) for course in courses])
        term_credits = sum(float(course[2]) for course in courses)
        total_credits += term_credits
        table = Table(
            rows,
            colWidths=[0.9 * inch, 4.15 * inch, 0.7 * inch, 0.65 * inch],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#153A5B")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9AA9B5")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6F8")]),
                    ("ALIGN", (2, 1), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.extend(
            [
                table,
                Spacer(1, 0.14 * inch),
                Paragraph(
                    f"<b>Term credits earned:</b> {term_credits:.1f} &nbsp;&nbsp; "
                    f"<b>Cumulative credits earned:</b> {total_credits:.1f}",
                    styles["BodyText"],
                ),
                Spacer(1, 0.08 * inch),
                Paragraph(
                    f"Transcript page {page_number} of {len(TERMS)} · "
                    "This synthetic record contains no real student data.",
                    styles["Italic"],
                ),
            ]
        )
        if page_number < len(TERMS):
            story.append(PageBreak())

    document.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    build()
