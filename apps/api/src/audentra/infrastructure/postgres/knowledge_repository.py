"""Retrieval over the institutional knowledge corpus.

This is the "separately reviewed retrieval policy" the managed-content
invariants ask for before institution knowledge enters model context. The
policy, in one place:

* Only ``published`` documents whose effective window includes today are
  retrievable. Students see ``student`` and ``all`` audiences; staff see
  everything.
* Retrieval is lexical and explainable: PostgreSQL full-text rank over the
  sections, a bonus for the document's curated keywords appearing in the
  question, and a bonus when the document's ``applies_to`` facets match the
  student the question is about. No embedding service sits in the request
  path, so a policy answer never depends on a second external provider.
* Every hit carries provenance the composer must keep: code, title, version,
  effective date and owning office (with its location and mailbox), and an
  ``applicability`` verdict (applies / does_not_apply / unknown) computed from
  the student's own record — so "does this apply to me?" is answered from
  data, not inferred by the model.
* Results are bounded: at most ``limit`` documents, at most two sections per
  document, each section clipped, so the evidence handed to the model stays
  within the same budget as any other tool read.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

JsonDict = dict[str, Any]

DEFAULT_LIMIT = 4
MAX_LIMIT = 8
SECTIONS_PER_DOCUMENT = 2
# Sections fetched per document before the Python re-rank (keyword phrases and
# the student's facets decide the final two); the top document keeps three.
SECTION_CANDIDATES = 8
TOP_DOCUMENT_SECTIONS = 3
SECTION_CHARACTERS = 1_300
SUMMARY_CHARACTERS = 320

_TOKEN = re.compile(r"[a-z0-9][a-z0-9'-]*")
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "about",
        "is",
        "are",
        "am",
        "was",
        "were",
        "be",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "will",
        "my",
        "me",
        "i",
        "you",
        "your",
        "it",
        "this",
        "that",
        "these",
        "those",
        "what",
        "when",
        "where",
        "who",
        "how",
        "why",
        "if",
        "any",
        "some",
        "there",
        "here",
        "have",
        "has",
        "had",
        "get",
        "got",
        "need",
        "still",
        "also",
        "than",
        "then",
        "into",
        "from",
        "at",
        "by",
        "as",
        "not",
        "no",
        "yes",
        "please",
        "tell",
        "show",
        "give",
        "know",
        "want",
    }
)
# Words that would drag every policy in; ranked only through the tsquery.
_GENERIC = frozenset({"student", "students", "university", "aster", "policy", "rule", "rules"})

# Question-language → corpus-language. The corpus uses institutional words;
# students do not. Each entry adds terms to the tsquery (OR-ed), never
# replaces the student's own words.
_SYNONYMS: Mapping[str, tuple[str, ...]] = {
    "shots": ("immunization", "vaccine"),
    "vaccines": ("immunization",),
    "vaccine": ("immunization",),
    "vaccinations": ("immunization",),
    "vaccination": ("immunization",),
    "immunisation": ("immunization",),
    "immunizations": ("immunization",),
    "meningitis": ("meningococcal",),
    "mmr": ("measles",),
    "dorm": ("residence", "hall", "housing"),
    "dorms": ("residence", "hall", "housing"),
    "room": ("housing", "residence"),
    "freshman": ("first-year",),
    "freshmen": ("first-year",),
    "money": ("aid", "bill", "balance"),
    "fafsa": ("financial", "aid", "verification"),
    "pell": ("grant",),
    "loan": ("loans",),
    "loans": ("loan",),
    "visa": ("international", "i-20"),
    "i20": ("i-20",),
    "sevis": ("international", "check-in"),
    "checkin": ("check-in",),
    "orientation": ("orientation",),
    "advisor": ("adviser",),
    "advisors": ("adviser",),
    "counselor": ("counselor", "adviser"),
    "drop": ("withdraw", "add/drop"),
    "dropping": ("withdraw", "add/drop"),
    "quit": ("withdraw", "withdrawal", "departed"),
    "leave": ("withdraw", "withdrawal", "leave"),
    "refund": ("refund", "refundable"),
    "late": ("deadline", "overdue", "past"),
    "miss": ("deadline", "missed"),
    "missed": ("deadline",),
    "missing": ("deadline",),
    "pay": ("payment", "bill"),
    "payout": ("disbursement", "disburse"),
    "disbursed": ("disbursement",),
    "disburse": ("disbursement",),
    "paying": ("payment", "bill"),
    "paid": ("payment", "posted"),
    "bill": ("billing", "tuition"),
    "tuition": ("tuition", "billing"),
    "cost": ("tuition", "fees", "cost"),
    "price": ("tuition", "fees", "cost"),
    "hold": ("hold", "holds"),
    "holds": ("hold",),
    "blocked": ("hold", "blocker", "gate"),
    "register": ("registration",),
    "registering": ("registration",),
    "enroll": ("registration", "enrollment"),
    "enrol": ("registration", "enrollment"),
    "classes": ("courses", "registration"),
    "class": ("course",),
    "gpa": ("gpa", "standing", "progress"),
    "probation": ("probation", "standing", "progress"),
    "sap": ("progress",),
    "scholarship": ("scholarship", "merit"),
    "grades": ("grading", "grades"),
    "transcript": ("transcript",),
    "parents": ("parent", "ferpa"),
    "parent": ("ferpa", "guardian"),
    "mom": ("parent", "ferpa"),
    "dad": ("parent", "ferpa"),
    "disability": ("accommodation", "accessibility"),
    "accommodations": ("accommodation",),
    "adhd": ("accommodation", "accessibility"),
    "cheating": ("integrity",),
    "plagiarism": ("integrity",),
    "commute": ("commuting", "off-campus", "residency"),
    "commuting": ("off-campus", "residency"),
    "home": ("family", "commuting", "off-campus"),
    "job": ("employment", "work-study"),
    "work": ("employment", "work-study"),
    "insurance": ("insurance", "waiver"),
    "meal": ("meal", "dining"),
    "food": ("meal", "dining"),
    "roommate": ("roommate", "room"),
    "deferral": ("defer",),
    "left": ("departed", "departure", "reassign"),
    "resigned": ("departed",),
    "defer": ("deferral",),
    "gap": ("deferral",),
    "deposit": ("deposit", "enrollment"),
    "verification": ("verification", "fafsa"),
    "who": ("office", "contact"),
    "contact": ("office", "email"),
    "where": ("location", "office"),
    "hours": ("hours", "office"),
    "phone": ("contact", "email"),
    "call": ("contact", "email"),
    "id": ("id", "identity", "card"),
    "card": ("id", "card"),
    "email": ("email", "contact"),
    "major": ("major", "program"),
    "minor": ("minor", "program"),
    "calendar": ("calendar", "dates"),
    "semester": ("term",),
    "term": ("term",),
    "break": ("break", "holiday"),
    "finals": ("examinations", "final"),
    "exam": ("examinations", "testing"),
    "exams": ("examinations", "testing"),
}

# Facets a student record can be reduced to. The values are the vocabulary
# documents use in `applies_to`; the mapping from raw record values lives in
# `student_facets`.
_FACET_KEYS = (
    "residency",
    "citizenship",
    "class_standing",
    "admit_term",
    "housing_plan",
    "program",
    "department",
)


@dataclass(frozen=True, slots=True)
class StudentFacets:
    residency: str | None = None
    citizenship: str | None = None
    class_standing: str | None = None
    admit_term: str | None = None
    housing_plan: str | None = None
    program: str | None = None
    department: str | None = None
    # Human-readable so the composer can restate the basis for "applies to you".
    description: dict[str, str] = field(default_factory=dict)

    def value(self, facet: str) -> str | None:
        return getattr(self, facet, None)


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    audience: str  # "student" | "staff"
    limit: int = DEFAULT_LIMIT
    kinds: tuple[str, ...] = ()
    office: str | None = None
    # Words that describe the person asking (a staff member's component), so
    # a table row about their own office outranks another office's row.
    viewer_terms: tuple[str, ...] = ()


class PostgresInstitutionKnowledgeRepository:
    """Bounded, explainable retrieval over institution_* tables."""

    def __init__(self, engine: AsyncEngine, *, clock: Any = None) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))

    async def available(self, tenant_id: str) -> bool:
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT 1 FROM institution_knowledge_document "
                        "WHERE tenant_id = :tenant_id AND status = 'published' LIMIT 1"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).first()
        return row is not None

    async def search(
        self,
        tenant_id: str,
        query: SearchQuery,
        *,
        facets: StudentFacets | None = None,
    ) -> JsonDict:
        today = self._clock().date()
        terms = _query_terms(query.text)
        tsquery = _tsquery(terms)
        limit = max(1, min(int(query.limit or DEFAULT_LIMIT), MAX_LIMIT))
        audiences = (
            ["student", "all"] if query.audience == "student" else ["student", "all", "internal"]
        )
        kinds = list(query.kinds) or None
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        WITH candidates AS (
                          SELECT d.id, d.code, d.kind, d.title, d.summary, d.owner_office_code,
                                 d.audience, d.version, d.effective_from, d.effective_until,
                                 d.applies_to, d.related_codes, d.keywords,
                                 ts_rank_cd(d.search, query, 32) AS doc_rank
                          FROM institution_knowledge_document d,
                               to_tsquery('english', :tsquery) AS query
                          WHERE d.tenant_id = :tenant_id AND d.status = 'published'
                            AND d.audience = ANY(CAST(:audiences AS text[]))
                            AND d.effective_from <= :today
                            AND (d.effective_until IS NULL OR d.effective_until >= :today)
                            AND (CAST(:kinds AS text[]) IS NULL
                                 OR d.kind = ANY(CAST(:kinds AS text[])))
                            AND (CAST(:office AS text) IS NULL OR d.owner_office_code = :office)
                            AND (d.search @@ query OR EXISTS (
                              SELECT 1 FROM institution_knowledge_section s
                              WHERE s.document_id = d.id AND s.search @@ query))
                        ),
                        ranked_sections AS (
                          SELECT s.document_id, s.ordinal, s.heading, s.body,
                                 ts_rank_cd(s.search, query, 32) AS section_rank,
                                 row_number() OVER (
                                   PARTITION BY s.document_id
                                   ORDER BY ts_rank_cd(s.search, query, 32) DESC, s.ordinal
                                 ) AS position
                          FROM institution_knowledge_section s
                          JOIN candidates c ON c.id = s.document_id,
                               to_tsquery('english', :tsquery) AS query
                        )
                        SELECT c.*,
                               COALESCE((SELECT max(section_rank) FROM ranked_sections r
                                         WHERE r.document_id = c.id), 0) AS best_section_rank,
                               (SELECT json_agg(json_build_object(
                                   'ordinal', r.ordinal, 'heading', r.heading, 'body', r.body,
                                   'rank', r.section_rank) ORDER BY r.position)
                                FROM ranked_sections r
                                WHERE r.document_id = c.id AND r.position <= :sections) AS sections
                        FROM candidates c
                        """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "tsquery": tsquery,
                            "audiences": audiences,
                            "today": today,
                            "kinds": kinds,
                            "office": query.office,
                            "sections": SECTION_CANDIDATES,
                        },
                    )
                )
                .mappings()
                .all()
                if tsquery
                else []
            )
            offices = await self._offices(connection, tenant_id)
            calendar = await self._calendar_matches(
                connection, tenant_id, terms, today, facets=facets
            )

        question_tokens = {token for token in terms.tokens if token not in _GENERIC}
        # Normalise the lexical rank inside this result set so the curated
        # signals (keywords, applicability, kind) are comparable across
        # questions: a strong keyword hit outranks a diffuse text match.
        raw_fts = {
            str(row["code"]): float(row["doc_rank"] or 0.0)
            + 1.5 * float(row["best_section_rank"] or 0.0)
            for row in rows
        }
        fts_scale = max(raw_fts.values(), default=0.0) or 1.0
        scored: list[tuple[float, JsonDict]] = []
        for row in rows:
            keywords = [str(keyword) for keyword in (row["keywords"] or [])]
            keyword_hits = _keyword_hits(keywords, terms.text)
            applies = _applicability(_mapping(row["applies_to"]), facets)
            title_tokens = set(_TOKEN.findall(str(row["title"]).lower()))
            title_overlap = len(
                [token for token in question_tokens if token in title_tokens and len(token) > 3]
            )
            score = (
                raw_fts[str(row["code"])] / fts_scale
                + 0.5 * min(len(keyword_hits), 3)
                # A multi-word keyword is a curated phrase match, worth more.
                + 0.25 * min(sum(1 for hit in keyword_hits if " " in hit or "/" in hit), 2)
                + (0.3 if applies["verdict"] == "applies" and applies["facets"] else 0.0)
                # A document that does not apply is still the answer to "does
                # this apply to me?" — the verdict carries the fact; only a
                # nudge below documents that do apply.
                - (0.1 if applies["verdict"] == "does_not_apply" else 0.0)
                + (0.1 if row["kind"] in ("policy", "procedure") else 0.0)
                + 0.15 * min(title_overlap, 2)
            )
            office = offices.get(str(row["owner_office_code"]))
            sections = _select_sections(
                row["sections"] or [], keywords, terms, facets, query.viewer_terms
            )
            situation = _situation_section(row["sections"] or [], facets)
            scored.append(
                (
                    score,
                    {
                        "code": str(row["code"]),
                        "kind": str(row["kind"]),
                        "title": str(row["title"]),
                        "summary": _clip(str(row["summary"]), SUMMARY_CHARACTERS),
                        "version": str(row["version"]),
                        "effectiveFrom": row["effective_from"].isoformat(),
                        "effectiveUntil": (
                            row["effective_until"].isoformat() if row["effective_until"] else None
                        ),
                        "audience": str(row["audience"]),
                        "owner": _office_brief(office, str(row["owner_office_code"])),
                        "applicability": applies,
                        "matchedKeywords": keyword_hits,
                        # The section written for this student's situation
                        # (an "International students" heading for an F-1
                        # student), ahead of the ranked sections.
                        "situationSection": situation,
                        "sections": sections,
                        "related": [str(code) for code in (row["related_codes"] or [])][:6],
                    },
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        documents = [document for _, document in scored[:limit]]
        for index, document in enumerate(documents):
            keep = TOP_DOCUMENT_SECTIONS if index == 0 else SECTIONS_PER_DOCUMENT
            document["sections"] = document["sections"][:keep]
        matched_offices = _office_matches(offices, terms)
        return {
            "query": query.text,
            "searchedAs": tsquery or "",
            "audience": query.audience,
            "today": today.isoformat(),
            "documents": documents,
            "totalMatches": len(scored),
            "calendar": calendar,
            "offices": matched_offices,
            "studentFacets": _facet_payload(facets),
            "answerGuidance": _answer_guidance(facets),
            "retrievalPolicy": (
                "Published documents in effect today, ranked by full-text match, "
                "curated keywords and applicability to the student; sections clipped."
            ),
        }

    async def viewer_component(self, tenant_id: str, staff_id: str) -> str | None:
        """The signed-in staff member's component, for 'our office' questions."""

        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT component FROM staff_member "
                        "WHERE tenant_id = :tenant_id AND id = :staff_id"
                    ),
                    {"tenant_id": tenant_id, "staff_id": staff_id},
                )
            ).first()
        return str(row[0]) if row and row[0] else None

    async def facets_for_student(self, tenant_id: str, student_id: str) -> StudentFacets | None:
        """The student's record reduced to applicability facets (one query)."""

        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT s.class_year,
                               o.payload->>'residencyStatus' AS residency,
                               o.payload->>'citizenshipStatus' AS citizenship,
                               o.payload->>'housingPreference' AS housing,
                               t.name AS term_name, t.starts_on AS term_starts_on,
                               p.code AS program_code, p.department AS program_department
                        FROM student s
                        LEFT JOIN student_onboarding o
                          ON o.student_id = s.id AND o.tenant_id = s.tenant_id
                        LEFT JOIN LATERAL (
                          SELECT ao.academic_term_id, ao.program_id
                          FROM admission_offer ao
                          WHERE ao.student_id = s.id AND ao.tenant_id = s.tenant_id
                          ORDER BY ao.created_at DESC LIMIT 1
                        ) offer ON true
                        LEFT JOIN academic_term t ON t.id = offer.academic_term_id
                        LEFT JOIN program p ON p.id = offer.program_id
                        WHERE s.tenant_id = :tenant_id AND s.id = :student_id
                        """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return student_facets(
            residency_status=row["residency"],
            citizenship_status=row["citizenship"],
            class_year=int(row["class_year"]) if row["class_year"] is not None else None,
            admit_term_name=row["term_name"],
            admit_term_starts_on=row["term_starts_on"],
            housing_preference=row["housing"],
            program_code=row["program_code"],
            program_department=row["program_department"],
        )

    async def offices(self, tenant_id: str) -> list[JsonDict]:
        async with self._engine.connect() as connection:
            offices = await self._offices(connection, tenant_id)
        return [
            _office_brief(office, code)
            for code, office in sorted(offices.items(), key=lambda item: item[1]["display_order"])
        ]

    async def calendar(
        self,
        tenant_id: str,
        *,
        window_days: int = 60,
        categories: Sequence[str] = (),
        audience: Sequence[str] = (),
    ) -> list[JsonDict]:
        today = self._clock().date()
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT code, term, label, category, audience, starts_on, ends_on,
                               starts_at, ends_at, owner_office_code, document_code, description
                        FROM academic_calendar_entry
                        WHERE tenant_id = :tenant_id
                          AND starts_on BETWEEN :from_date AND :to_date
                          AND (CAST(:categories AS text[]) IS NULL
                               OR category = ANY(CAST(:categories AS text[])))
                          AND (CAST(:audience AS text[]) IS NULL
                               OR audience = ANY(CAST(:audience AS text[])))
                        ORDER BY starts_on, starts_at NULLS FIRST, label
                        """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "from_date": today - timedelta(days=7),
                            "to_date": today + timedelta(days=window_days),
                            "categories": list(categories) or None,
                            "audience": list(audience) or None,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [_calendar_row(dict(row), today) for row in rows]

    # -- internals ---------------------------------------------------------

    async def _offices(self, connection: Any, tenant_id: str) -> dict[str, JsonDict]:
        rows = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT code, name, short_name, component, location, hours, email,
                           head_title, sla_business_days, description, services, display_order
                    FROM institution_office
                    WHERE tenant_id = :tenant_id AND active = true
                    ORDER BY display_order
                    """
                    ),
                    {"tenant_id": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return {str(row["code"]): dict(row) for row in rows}

    async def _calendar_matches(
        self,
        connection: Any,
        tenant_id: str,
        terms: _Terms,
        today: date,
        *,
        facets: StudentFacets | None = None,
    ) -> list[JsonDict]:
        """Calendar entries whose label matches the question.

        A student's own admit term comes first (a Spring admit asking when
        classes start must see the Spring date), then the nearest dates.
        """

        if not terms.tokens:
            return []
        pattern = "|".join(re.escape(token) for token in terms.tokens if len(token) > 2)
        if not pattern:
            return []
        rows = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT code, term, label, category, audience, starts_on, ends_on,
                               starts_at, ends_at, owner_office_code, document_code, description
                        FROM academic_calendar_entry
                        WHERE tenant_id = :tenant_id
                          AND (label ~* :pattern OR coalesce(description, '') ~* :pattern
                               OR category ~* :pattern)
                        ORDER BY (CASE WHEN CAST(:term AS text) IS NOT NULL AND term = :term
                                       THEN 0 ELSE 1 END),
                                 abs(starts_on - CAST(:today AS date)), starts_on
                        LIMIT 6
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "pattern": pattern,
                        "today": today,
                        "term": facets.admit_term if facets else None,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [_calendar_row(dict(row), today) for row in rows]


# ---------------------------------------------------------------------------
# Query shaping
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Terms:
    text: str
    tokens: tuple[str, ...]
    expansions: tuple[str, ...]


def _query_terms(raw: str) -> _Terms:
    lowered = raw.lower().replace("\u2019", "'")
    tokens = [token for token in _TOKEN.findall(lowered) if token not in _STOP and len(token) > 1]
    expansions: list[str] = []
    for token in tokens:
        for alternative in _SYNONYMS.get(token, ()):
            if alternative not in tokens and alternative not in expansions:
                expansions.append(alternative)
    # A hyphenated phrase in the corpus ("add/drop", "first-year") is indexed
    # as separate lexemes; keep the original tokens and let the tsquery OR them.
    return _Terms(text=lowered, tokens=tuple(dict.fromkeys(tokens)), expansions=tuple(expansions))


def _tsquery(terms: _Terms) -> str:
    """OR of every meaningful token (with prefix matching for long words).

    OR rather than AND: a student's sentence carries many words that no
    document contains verbatim; ranking, not boolean matching, decides.
    Generic words are excluded so "policy" alone does not match everything.
    """

    parts: list[str] = []
    for token in (*terms.tokens, *terms.expansions):
        cleaned = re.sub(r"[^a-z0-9'-]", "", token)
        cleaned = cleaned.replace("'", "").replace("-", " ").strip()
        if not cleaned or cleaned in _GENERIC:
            continue
        words = cleaned.split()
        if len(words) > 1:
            parts.append("(" + " & ".join(_lexeme(word) for word in words) + ")")
        else:
            parts.append(_lexeme(words[0]))
    unique = list(dict.fromkeys(part for part in parts if part))
    return " | ".join(unique)


def _lexeme(word: str) -> str:
    if len(word) >= 6:
        return f"{word[:6]}:*"
    return word


# Words a section must mention to be the one that answers *this* student:
# an international student's insurance question is answered by the
# "International students" section, not the general waiver text.
_FACET_SECTION_TERMS: Mapping[str, Mapping[str, str]] = {
    "residency": {"international": r"international|f-1|j-1|visa"},
    "class_standing": {"transfer": r"transfer", "first_year": r"first[- ]year|freshm"},
    "admit_term": {"Spring 2027": r"spring", "Fall 2026": r"fall"},
}


_SITUATION_HEADINGS: Mapping[str, Mapping[str, str]] = {
    "residency": {"international": r"international|f-1|j-1|visa"},
    "class_standing": {"transfer": r"transfer", "first_year": r"first[- ]year|freshm"},
    "admit_term": {"Spring 2027": r"\bspring\b"},
}


def _situation_section(
    candidates: Sequence[Mapping[str, Any]], facets: StudentFacets | None
) -> JsonDict | None:
    """The section whose *heading* names the student's situation, if any."""

    if facets is None:
        return None
    for facet, patterns in _SITUATION_HEADINGS.items():
        pattern = patterns.get(facets.value(facet) or "")
        if not pattern:
            continue
        for section in candidates:
            heading = str(section.get("heading") or "")
            if re.search(pattern, heading, re.IGNORECASE):
                return {
                    "heading": heading,
                    "text": _clip(str(section.get("body") or ""), SECTION_CHARACTERS),
                    "why": f"this student's {facet.replace('_', ' ')} is {facets.value(facet)}",
                }
    return None


def _select_sections(
    candidates: Sequence[Mapping[str, Any]],
    keywords: Sequence[str],
    terms: _Terms,
    facets: StudentFacets | None,
    viewer_terms: Sequence[str] = (),
) -> list[JsonDict]:
    """Re-rank a document's candidate sections for this question and student."""

    question_tokens = {token for token in terms.tokens if token not in _GENERIC and len(token) > 2}
    ranked: list[tuple[float, int, JsonDict]] = []
    for index, section in enumerate(candidates):
        heading = str(section.get("heading") or "")
        body = str(section.get("body") or "")
        lowered = (heading + " " + body).lower()
        score = float(section.get("rank") or 0.0)
        score += 0.4 * len(_keyword_hits(keywords, lowered)[:3])
        score += 0.15 * len([token for token in question_tokens if token in lowered][:4])
        if facets is not None:
            for facet, patterns in _FACET_SECTION_TERMS.items():
                value = facets.value(facet)
                pattern = patterns.get(value or "")
                if pattern and re.search(pattern, lowered):
                    score += 0.35
        ranked.append(
            (
                score,
                index,
                {
                    "heading": heading,
                    # The lines of this section that answer the question for
                    # this student — the row for their situation, the sentence
                    # with the amount — ahead of the clipped full text.
                    "highlight": _highlight(body, question_tokens, keywords, facets, viewer_terms),
                    "text": _clip(_scope_rows(body, facets), SECTION_CHARACTERS),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [section for _, _, section in ranked]


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z•(])|\n+")


# Table rows written for a residency the student does not have: an
# international student's tuition question must not show the in-state row.
_RESIDENCY_ROW_EXCLUSIONS: Mapping[str, str] = {
    "international": r"\bin-state\b|\bout-of-state\b|\bdomestic\b",
    "domestic": r"\binternational\b",
}


def _scope_rows(body: str, facets: StudentFacets | None) -> str:
    """Drop linearised table rows that belong to another residency."""

    residency = facets.residency if facets is not None else None
    if residency is None or residency not in _RESIDENCY_ROW_EXCLUSIONS:
        return body
    exclusion = re.compile(_RESIDENCY_ROW_EXCLUSIONS[residency], re.IGNORECASE)
    kept: list[str] = []
    for line in _linearise_tables(body).splitlines():
        if (
            line.startswith("•")
            and exclusion.search(line)
            and not re.search(residency, line, re.IGNORECASE)
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


def _token_weight(token: str) -> float:
    """Longer, rarer words decide a highlight; 'aid' and 'term' do not."""

    return min(1.0, 0.25 + 0.06 * len(token))


def _highlight(
    body: str,
    question_tokens: set[str],
    keywords: Sequence[str],
    facets: StudentFacets | None,
    viewer_terms: Sequence[str] = (),
) -> str | None:
    """Up to two lines of a section that carry the question's or the
    student's terms: a table row for their residency, the sentence with the
    fee, the clause with the deadline."""

    facet_patterns = []
    if facets is not None:
        for facet, patterns in _FACET_SECTION_TERMS.items():
            pattern = patterns.get(facets.value(facet) or "")
            if pattern:
                facet_patterns.append(re.compile(pattern, re.IGNORECASE))
    viewer = [term.lower() for term in viewer_terms if term]
    scored: list[tuple[float, int, str]] = []
    for index, raw in enumerate(
        _SENTENCE_SPLIT.split(_linearise_tables(_scope_rows(body, facets)))
    ):
        line = " ".join(raw.split())
        if len(line) < 12:
            continue
        lowered = line.lower()
        score = 0.0
        score += 1.5 * sum(1 for pattern in facet_patterns if pattern.search(lowered))
        score += 1.0 * sum(1 for term in viewer if term in lowered)
        score += 1.0 * len(_keyword_hits(keywords, lowered)[:2])
        score += sum(
            sorted(
                (_token_weight(token) for token in question_tokens if token in lowered),
                reverse=True,
            )[:4]
        )
        if score > 0:
            scored.append((score, index, line))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen = sorted(scored[:2], key=lambda item: item[1])
    return _clip(" ".join(line for _, _, line in chosen), 320)


def _keyword_hits(keywords: Sequence[str], question: str) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        cleaned = keyword.lower().strip()
        if not cleaned:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(cleaned) + r"(?![a-z0-9])", question):
            hits.append(cleaned)
    return hits[:6]


def _applicability(applies_to: Mapping[str, Any], facets: StudentFacets | None) -> JsonDict:
    """Does this document apply to the student, by its declared facets?

    verdict: "applies" (every declared facet matches, or nothing is declared),
    "does_not_apply" (a declared facet is known and outside the list),
    "unknown" (a declared facet is not known for this student).
    """

    declared = {
        str(key): [str(value) for value in values]
        for key, values in applies_to.items()
        if isinstance(values, list) and values
    }
    if not declared:
        return {"verdict": "applies", "facets": [], "basis": "applies to every student"}
    if facets is None:
        return {
            "verdict": "unknown",
            "facets": [{"facet": key, "requires": values} for key, values in declared.items()],
            "basis": "no student record was available to check the conditions",
        }
    checks: list[JsonDict] = []
    verdict = "applies"
    for key, values in declared.items():
        actual = facets.value(key)
        if actual is None:
            checks.append({"facet": key, "requires": values, "student": None, "matches": None})
            if verdict == "applies":
                verdict = "unknown"
            continue
        matches = actual in values
        checks.append({"facet": key, "requires": values, "student": actual, "matches": matches})
        if not matches:
            verdict = "does_not_apply"
    basis = "; ".join(
        f"{check['facet']}: the student is {check['student'] or 'unknown'}, "
        f"the document is for {', '.join(check['requires'])}"
        for check in checks
    )
    return {"verdict": verdict, "facets": checks, "basis": basis}


def _office_brief(office: Mapping[str, Any] | None, code: str) -> JsonDict:
    if office is None:
        return {"code": code, "name": code, "known": False}
    return {
        "code": code,
        "name": str(office.get("name") or code),
        "shortName": str(office.get("short_name") or code),
        "component": office.get("component"),
        "location": office.get("location"),
        "hours": office.get("hours"),
        "email": office.get("email"),
        "headTitle": office.get("head_title"),
        "serviceLevelBusinessDays": office.get("sla_business_days"),
        "staffRecordsInPlatform": office.get("component") is not None,
        "known": True,
    }


# Words every office matches: they must not decide which office is named.
_OFFICE_STOP = frozenset({"office", "offices", "contact", "which", "where", "email", "services"})


def _office_matches(offices: Mapping[str, JsonDict], terms: _Terms) -> list[JsonDict]:
    """Offices whose name or services the question names, for 'who handles'."""

    if not terms.tokens:
        return []
    question = terms.text
    matched: list[tuple[int, str]] = []
    for code, office in offices.items():
        haystacks = [
            str(office.get("name") or "").lower(),
            str(office.get("short_name") or "").lower(),
            " ".join(str(item) for item in (office.get("services") or [])).lower(),
        ]
        score = 0
        for token in terms.tokens:
            if token in _GENERIC or token in _OFFICE_STOP or len(token) < 4:
                continue
            if any(re.search(r"\b" + re.escape(token), haystack) for haystack in haystacks):
                score += 1
        name_words = _TOKEN.findall(str(office.get("short_name") or "").lower())
        if name_words and all(word in question for word in name_words if len(word) > 3):
            score += 2
        if score >= 2:
            matched.append((score, code))
    matched.sort(key=lambda item: (-item[0], offices[item[1]]["display_order"]))
    return [
        {
            **_office_brief(offices[code], code),
            "services": list(offices[code].get("services") or [])[:8],
        }
        for _, code in matched[:3]
    ]


def _calendar_row(row: Mapping[str, Any], today: date) -> JsonDict:
    starts_on: date = row["starts_on"]
    ends_on: date | None = row["ends_on"]
    days = (starts_on - today).days
    if ends_on is not None and starts_on <= today <= ends_on:
        relative = "in progress today"
    elif days == 0:
        relative = "today"
    elif days > 0:
        relative = f"in {days} day(s)"
    else:
        relative = f"{-days} day(s) ago — this date has passed"
    return {
        "code": str(row["code"]),
        "term": row["term"],
        "label": str(row["label"]),
        "category": str(row["category"]),
        "audience": str(row["audience"]),
        "startsOn": starts_on.isoformat(),
        "endsOn": ends_on.isoformat() if ends_on else None,
        "startsAt": row["starts_at"].strftime("%H:%M") if row["starts_at"] else None,
        "endsAt": row["ends_at"].strftime("%H:%M") if row["ends_at"] else None,
        "relativeToToday": relative,
        "ownerOffice": row["owner_office_code"],
        "document": row["document_code"],
        "description": row["description"],
    }


def _facet_payload(facets: StudentFacets | None) -> JsonDict | None:
    if facets is None:
        return None
    payload: JsonDict = {
        key: facets.value(key) for key in _FACET_KEYS if facets.value(key) is not None
    }
    if facets.description:
        payload["basis"] = dict(facets.description)
    return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _linearise_tables(value: str) -> str:
    """Markdown table rows become 'header: cell; header: cell' lines.

    Composers read the section as plain text; a pipe-delimited row with a
    header three lines up is where a status and its meaning come apart.
    """

    lines: list[str] = []
    header: list[str] | None = None
    for raw in value.splitlines():
        stripped = raw.strip()
        if stripped.startswith("|"):
            if _TABLE_SEPARATOR.match(stripped):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if header is None:
                header = cells
                continue
            paired = [
                f"{header[i]}: {cell}" if i < len(header) and header[i] else cell
                for i, cell in enumerate(cells)
                if cell
            ]
            lines.append("• " + "; ".join(paired))
            continue
        header = None
        lines.append(raw)
    return "\n".join(lines)


def _answer_guidance(facets: StudentFacets | None) -> str | None:
    """One sentence telling a composer which of a document's sections govern."""

    if facets is None:
        return None
    notes: list[str] = []
    if facets.residency == "international":
        notes.append(
            "This student IS an INTERNATIONAL (F-1/J-1) student — never say 'if you are an "
            "international student'. Where a document has an international-students rule, "
            "that rule governs, including where it says something is not available to them; "
            "quote the international row of any rate table, never the in-state row."
        )
    if facets.class_standing == "transfer":
        notes.append("This student is a TRANSFER student, not a first-year student.")
    elif facets.class_standing == "first_year":
        notes.append("This student is a FIRST-YEAR student.")
    if facets.admit_term:
        notes.append(
            f"This student's admit term is {facets.admit_term}: quote that term's dates, "
            "not another term's."
        )
    return " ".join(notes) or None


def _clip(value: str, limit: int) -> str:
    cleaned = " ".join(_linearise_tables(value).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Facets from a student record
# ---------------------------------------------------------------------------


def student_facets(
    *,
    residency_status: str | None,
    citizenship_status: str | None,
    class_year: int | None,
    admit_term_name: str | None,
    admit_term_starts_on: date | None,
    housing_preference: str | None,
    program_code: str | None,
    program_department: str | None,
) -> StudentFacets:
    """Reduce raw record values to the `applies_to` vocabulary.

    Class standing is inferred from the admit term and class year: a student
    whose class year is four years after the admit year entered as a
    first-year student; a nearer class year means credits came with them (a
    transfer). Unknown inputs stay unknown — never guessed.
    """

    residency = None
    if residency_status in ("domestic", "international"):
        residency = residency_status
    citizenship = None
    if citizenship_status in ("us_citizen", "permanent_resident", "international"):
        citizenship = citizenship_status
    standing = None
    if class_year is not None and admit_term_starts_on is not None:
        expected_first_year = admit_term_starts_on.year + (
            4 if admit_term_starts_on.month >= 6 else 3
        )
        standing = "first_year" if class_year >= expected_first_year else "transfer"
    housing = None
    if housing_preference in ("on_campus", "off_campus", "commuting", "family", "undecided"):
        housing = housing_preference
    description: dict[str, str] = {}
    if residency:
        description["residency"] = residency
    if standing:
        description["class_standing"] = (
            "first-year student" if standing == "first_year" else "transfer student"
        ) + (f" (class of {class_year})" if class_year else "")
    if admit_term_name:
        description["admit_term"] = admit_term_name
    if housing:
        description["housing_plan"] = housing.replace("_", " ")
    if program_code:
        description["program"] = program_code + (
            f" ({program_department})" if program_department else ""
        )
    return StudentFacets(
        residency=residency,
        citizenship=citizenship,
        class_standing=standing,
        admit_term=admit_term_name,
        housing_plan=housing,
        program=program_code,
        department=program_department,
        description=description,
    )
