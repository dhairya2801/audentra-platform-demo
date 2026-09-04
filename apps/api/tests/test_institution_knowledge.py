"""The institutional knowledge corpus: loading, validation, retrieval shaping.

No database here: these tests pin the seed/import contract (front matter,
cross-references, sections, the facet vocabulary) and the pure parts of the
retrieval policy (query shaping, keyword hits, applicability verdicts, facet
inference). The Postgres-backed search is covered by the postgres-marked test.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from audentra.infrastructure.postgres.knowledge_repository import (
    _applicability,
    _keyword_hits,
    _query_terms,
    _tsquery,
    student_facets,
)
from audentra.infrastructure.seeding.institution_knowledge import (
    KnowledgeCorpusError,
    default_corpus_root,
    load_corpus,
)

ASTER_DEMO = default_corpus_root("aster-demo")


def test_packaged_corpus_loads_and_cross_references_resolve() -> None:
    corpus = load_corpus(ASTER_DEMO)
    assert corpus.tenant_slug == "aster-demo"
    assert len(corpus.documents) >= 80
    assert len(corpus.offices) >= 12
    assert len(corpus.calendar) >= 60
    codes = {document.code for document in corpus.documents}
    offices = {office.code for office in corpus.offices}
    for document in corpus.documents:
        assert document.owner_office in offices
        assert set(document.related) <= codes
        assert document.sections, document.code
        assert document.keywords, f"{document.code} has no keywords"
    for entry in corpus.calendar:
        assert entry.document is None or entry.document in codes
    # Every staff component that exists in the population has an office.
    components = {office.component for office in corpus.offices if office.component}
    assert {"Registrar", "Financial Aid", "Housing", "International Student Services"} <= components


def test_packaged_corpus_agrees_with_the_generator_facts() -> None:
    """The fact sheet the documents must agree with (see knowledge/README.md)."""

    corpus = load_corpus(ASTER_DEMO)
    by_code = {document.code: document for document in corpus.documents}
    deposit = by_code["enrollment-deposit-policy"].body
    assert "$500" in deposit and "1 June 2026" in deposit and "1 December 2026" in deposit
    billing = by_code["billing-and-payment-policy"].body
    assert "$250" in billing and "$45" in billing and "28 August 2026" in billing
    tuition = by_code["tuition-and-fees-2026-2027"].body
    for amount in ("$18,400", "$28,900", "$31,200", "$33,300", "$43,800", "$47,400"):
        assert amount in tuition
    sap = by_code["satisfactory-academic-progress-policy"].body
    assert "2.00" in sap and "67%" in sap and "180 attempted credits" in sap
    halls = by_code["residence-halls-guide"].body
    for hall in (
        "Alder Hall",
        "Birchwood Commons",
        "Cedarcroft House",
        "Dunmore Hall",
        "Elmridge Commons",
        "Fernhollow House",
    ):
        assert hall in halls
    calendar = {entry.code: entry for entry in corpus.calendar}
    assert calendar["fall2026-classes-begin"].starts_on == date(2026, 8, 31)
    assert calendar["fall2026-add-drop-deadline"].starts_on == date(2026, 9, 11)
    assert calendar["spring2027-classes-begin"].starts_on == date(2027, 1, 19)


def test_program_plans_and_help_articles_load() -> None:
    corpus = load_corpus(ASTER_DEMO)
    assert len(corpus.programs) == 14
    assert {plan.code for plan in corpus.programs} >= {"BS-CS", "BSN", "BFA-DES"}
    assert all(plan.requirements for plan in corpus.programs)
    assert len(corpus.help_articles) >= 6
    assert {article.category for article in corpus.help_articles} == {
        "getting_started",
        "documents",
        "payments",
        "support",
    }


def _write_corpus(root: Path, *, document: str, office_code: str = "REG") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "documents").mkdir(exist_ok=True)
    (root / "offices.yaml").write_text(
        "schema_version: 1\ntenant: t\nconfiguration: offices\noffices:\n"
        f"  - code: {office_code}\n    name: Office of the Registrar\n    short_name: Registrar\n"
        "    component: Registrar\n    location: Larkin Hall 150\n    description: Records.\n",
        encoding="utf-8",
    )
    (root / "calendar.yaml").write_text(
        "schema_version: 1\ntenant: t\nconfiguration: academic_calendar\nentries: []\n",
        encoding="utf-8",
    )
    (root / "documents" / "sample-policy.md").write_text(document, encoding="utf-8")


_GOOD = """---
code: sample-policy
kind: policy
title: Sample
summary: A sample.
owner_office: REG
audience: student
version: "2026.1"
effective_from: 2026-05-01
applies_to:
  residency: [international]
keywords: [sample]
---
Intro paragraph.

## The rule

Body text.

## Exceptions

More text.
"""


def test_sections_split_at_headings_with_leading_prose_as_overview(tmp_path: Path) -> None:
    _write_corpus(tmp_path, document=_GOOD)
    corpus = load_corpus(tmp_path)
    (document,) = corpus.documents
    assert [section.heading for section in document.sections] == [
        "Overview",
        "The rule",
        "Exceptions",
    ]
    assert document.applies_to == {"residency": ["international"]}
    assert document.content_sha256 == load_corpus(tmp_path).documents[0].content_sha256


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda text: text.replace("owner_office: REG", "owner_office: XYZ"), "not an office"),
        (lambda text: text.replace("residency: [international]", "planet: [mars]"), "facet"),
        (
            lambda text: text.replace("residency: [international]", "residency: [martian]"),
            "unknown values",
        ),
        (lambda text: text.replace("kind: policy", "kind: memo"), "kind"),
        (lambda text: text.replace("code: sample-policy", "code: other-code"), "file name"),
        (
            lambda text: text.replace("effective_from: 2026-05-01", "effective_from: yesterday"),
            "ISO date",
        ),
    ],
)
def test_corpus_validation_rejects_malformed_documents(
    tmp_path: Path, mutation: Callable[[str], str], message: str
) -> None:
    _write_corpus(tmp_path, document=mutation(_GOOD))
    with pytest.raises(KnowledgeCorpusError, match=message):
        load_corpus(tmp_path)


def test_related_document_must_exist(tmp_path: Path) -> None:
    _write_corpus(
        tmp_path,
        document=_GOOD.replace("keywords: [sample]", "keywords: [sample]\nrelated: [ghost]"),
    )
    with pytest.raises(KnowledgeCorpusError, match="ghost"):
        load_corpus(tmp_path)


# ---------------------------------------------------------------------------
# Retrieval shaping
# ---------------------------------------------------------------------------


def test_query_terms_drop_stopwords_and_expand_synonyms() -> None:
    terms = _query_terms("Do I have to get my shots before I can register?")
    assert "shots" in terms.tokens and "register" in terms.tokens
    assert "i" not in terms.tokens and "the" not in terms.tokens
    assert "immunization" in terms.expansions and "registration" in terms.expansions
    query = _tsquery(terms)
    assert " | " in query
    assert "immuni" in query  # prefix lexeme for a long word
    assert "policy" not in query.split(" | ")  # generic words never match alone


def test_keyword_hits_are_whole_phrase_matches() -> None:
    hits = _keyword_hits(
        ["deposit", "deposit deadline", "refund"], "what happens if i miss the deposit deadline"
    )
    assert hits == ["deposit", "deposit deadline"]
    assert _keyword_hits(["fund"], "refund policy") == []


def test_applicability_verdicts() -> None:
    facets = student_facets(
        residency_status="international",
        citizenship_status="international",
        class_year=2030,
        admit_term_name="Fall 2026",
        admit_term_starts_on=date(2026, 8, 31),
        housing_preference="on_campus",
        program_code="BS-EE",
        program_department="Engineering",
    )
    assert facets.class_standing == "first_year"
    assert _applicability({}, facets)["verdict"] == "applies"
    assert _applicability({"residency": ["international"]}, facets)["verdict"] == "applies"
    assert _applicability({"residency": ["domestic"]}, facets)["verdict"] == "does_not_apply"
    assert _applicability({"class_standing": ["first_year"]}, facets)["verdict"] == "applies"
    assert _applicability({"program": ["BSN"]}, facets)["verdict"] == "does_not_apply"
    assert _applicability({"residency": ["domestic"]}, None)["verdict"] == "unknown"
    transfer = student_facets(
        residency_status="domestic",
        citizenship_status="us_citizen",
        class_year=2028,
        admit_term_name="Fall 2026",
        admit_term_starts_on=date(2026, 8, 31),
        housing_preference=None,
        program_code=None,
        program_department=None,
    )
    assert transfer.class_standing == "transfer"
    assert (
        _applicability({"class_standing": ["first_year"]}, transfer)["verdict"] == "does_not_apply"
    )
    assert _applicability({"housing_plan": ["on_campus"]}, transfer)["verdict"] == "unknown"
    spring = student_facets(
        residency_status="domestic",
        citizenship_status="us_citizen",
        class_year=2031,
        admit_term_name="Spring 2027",
        admit_term_starts_on=date(2027, 1, 19),
        housing_preference="undecided",
        program_code="BBA",
        program_department="Business",
    )
    assert spring.class_standing == "first_year"
