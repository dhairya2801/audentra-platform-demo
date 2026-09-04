"""Import a tenant's institutional knowledge corpus into PostgreSQL.

The corpus is a directory of seed/import input, exactly like the managed
configuration YAML next to it:

    knowledge/
      offices.yaml            the office directory
      calendar.yaml           dated calendar entries
      documents/*.md          Markdown documents with YAML front matter

`load_corpus` parses and validates it into plain dataclasses (no database);
`import_corpus` upserts those into the institution_* tables, idempotently:
an unchanged document is left alone, a changed one is rewritten with its
sections regenerated, and a document that disappeared from the directory is
retired rather than deleted (the trace of an answer that cited it stays
resolvable).

Validation is deliberately strict — every cross-reference must resolve —
because a document that points a student at an office or a policy that does
not exist is the one failure the corpus exists to prevent.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

DOCUMENT_KINDS = frozenset(
    {"policy", "procedure", "handbook", "program", "service", "directory", "internal"}
)
AUDIENCES = frozenset({"student", "internal", "all"})
STATUSES = frozenset({"published", "draft", "retired"})
CALENDAR_CATEGORIES = frozenset(
    {
        "admissions",
        "registration",
        "billing",
        "financial_aid",
        "housing",
        "orientation",
        "international",
        "health",
        "advising",
        "academic",
        "holiday",
        "campus_life",
    }
)
CALENDAR_AUDIENCES = frozenset(
    {"all", "new_students", "transfer", "international", "on_campus", "residential"}
)
# The facet vocabulary the retrieval layer knows how to evaluate against a
# student. Anything else in `applies_to` is a typo, not a new facet.
APPLIES_TO_FACETS: Mapping[str, frozenset[str] | None] = {
    "residency": frozenset({"domestic", "international"}),
    "citizenship": frozenset({"us_citizen", "permanent_resident", "international"}),
    "class_standing": frozenset({"first_year", "transfer"}),
    "admit_term": None,
    "housing_plan": frozenset({"on_campus", "off_campus", "commuting", "family", "undecided"}),
    "program": None,
    "department": None,
}

_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
_OFFICE_CODE = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_NAMESPACE = UUID("6f0a5d0e-9d3c-4a4e-9b1c-3c2f0b6e8a11")


class KnowledgeCorpusError(ValueError):
    """The corpus is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Office:
    code: str
    name: str
    short_name: str
    component: str | None
    location: str
    hours: str | None
    email: str | None
    head_title: str | None
    sla_business_days: int | None
    description: str
    services: tuple[str, ...]
    display_order: int


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    code: str
    term: str | None
    label: str
    category: str
    audience: str
    starts_on: date
    ends_on: date | None
    starts_at: time | None
    ends_at: time | None
    owner_office: str | None
    document: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class Section:
    ordinal: int
    heading: str
    body: str


@dataclass(frozen=True, slots=True)
class Document:
    code: str
    kind: str
    title: str
    summary: str
    body: str
    owner_office: str
    audience: str
    status: str
    version: str
    effective_from: date
    effective_until: date | None
    supersedes: str | None
    applies_to: dict[str, list[str]]
    related: tuple[str, ...]
    keywords: tuple[str, ...]
    source_path: str
    sections: tuple[Section, ...]

    @property
    def content_sha256(self) -> str:
        digest = hashlib.sha256()
        for part in (
            self.code,
            self.kind,
            self.title,
            self.summary,
            self.body,
            self.owner_office,
            self.audience,
            self.status,
            self.version,
            self.effective_from.isoformat(),
            self.effective_until.isoformat() if self.effective_until else "",
            self.supersedes or "",
            repr(sorted((key, sorted(values)) for key, values in self.applies_to.items())),
            "|".join(self.related),
            "|".join(self.keywords),
        ):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ProgramRequirement:
    course_code: str
    category: str
    term: int


@dataclass(frozen=True, slots=True)
class ProgramPlan:
    code: str
    department: str
    requirements: tuple[ProgramRequirement, ...]


@dataclass(frozen=True, slots=True)
class HelpArticle:
    category: str
    question: str
    answer: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class Corpus:
    tenant_slug: str
    offices: tuple[Office, ...]
    calendar: tuple[CalendarEntry, ...]
    documents: tuple[Document, ...]
    # Optional companions: program plans (department + core requirements
    # against the course catalog) and the Help page's short guides.
    programs: tuple[ProgramPlan, ...] = ()
    help_articles: tuple[HelpArticle, ...] = ()


@dataclass(slots=True)
class ImportReport:
    offices: int = 0
    calendar_entries: int = 0
    documents_inserted: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    documents_retired: int = 0
    sections: int = 0
    programs: int = 0
    program_requirements: int = 0
    help_articles: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def default_corpus_root(tenant_slug: str) -> Path:
    """`apps/api/assets/config/tenants/<slug>/knowledge` from this module."""

    package_root = Path(__file__).resolve().parents[3]  # .../src
    return package_root.parent / "assets" / "config" / "tenants" / tenant_slug / "knowledge"


def load_corpus(root: Path) -> Corpus:
    if not root.is_dir():
        raise KnowledgeCorpusError(f"knowledge directory not found: {root}")
    offices_raw = _load_yaml(root / "offices.yaml")
    calendar_raw = _load_yaml(root / "calendar.yaml")
    tenant_slug = str(offices_raw.get("tenant") or "")
    if not tenant_slug:
        raise KnowledgeCorpusError("offices.yaml must name its tenant")
    if str(calendar_raw.get("tenant") or "") != tenant_slug:
        raise KnowledgeCorpusError("calendar.yaml names a different tenant than offices.yaml")

    offices = tuple(
        _office(item, index) for index, item in enumerate(_items(offices_raw, "offices"))
    )
    office_codes = {office.code for office in offices}
    if len(office_codes) != len(offices):
        raise KnowledgeCorpusError("office codes must be unique")

    documents_dir = root / "documents"
    if not documents_dir.is_dir():
        raise KnowledgeCorpusError(f"documents directory not found: {documents_dir}")
    documents = tuple(_document(path, root) for path in sorted(documents_dir.glob("*.md")))
    document_codes = {document.code for document in documents}
    if len(document_codes) != len(documents):
        duplicates = sorted(
            code for code in document_codes if sum(d.code == code for d in documents) > 1
        )
        raise KnowledgeCorpusError(f"duplicate document codes: {', '.join(duplicates)}")

    calendar = tuple(
        _calendar_entry(item, index) for index, item in enumerate(_items(calendar_raw, "entries"))
    )
    calendar_codes = {entry.code for entry in calendar}
    if len(calendar_codes) != len(calendar):
        raise KnowledgeCorpusError("calendar entry codes must be unique")

    problems: list[str] = []
    for document in documents:
        if document.owner_office not in office_codes:
            problems.append(
                f"{document.code}: owner_office {document.owner_office!r} is not an office"
            )
        for related in document.related:
            if related not in document_codes:
                problems.append(f"{document.code}: related {related!r} is not a document")
        if document.supersedes and document.supersedes not in document_codes:
            problems.append(
                f"{document.code}: supersedes {document.supersedes!r} is not a document"
            )
        if not document.sections:
            problems.append(f"{document.code}: has no '##' sections")
    for entry in calendar:
        if entry.owner_office and entry.owner_office not in office_codes:
            problems.append(
                f"calendar {entry.code}: owner_office {entry.owner_office!r} is not an office"
            )
        if entry.document and entry.document not in document_codes:
            problems.append(f"calendar {entry.code}: document {entry.document!r} is not a document")
    programs = _load_programs(root / "programs.yaml", tenant_slug)
    help_articles = _load_help_articles(root / "help-articles.yaml", tenant_slug)
    if problems:
        raise KnowledgeCorpusError("corpus is inconsistent:\n  " + "\n  ".join(problems))
    return Corpus(
        tenant_slug=tenant_slug,
        offices=offices,
        calendar=calendar,
        documents=documents,
        programs=programs,
        help_articles=help_articles,
    )


_HELP_CATEGORIES = frozenset({"getting_started", "documents", "payments", "support"})
# The program_requirement.category vocabulary the schema enforces.
_REQUIREMENT_CATEGORIES = frozenset({"major_core", "math_science", "general_education", "elective"})


def _load_programs(path: Path, tenant_slug: str) -> tuple[ProgramPlan, ...]:
    if not path.exists():
        return ()
    raw = _load_yaml(path)
    if str(raw.get("tenant") or "") != tenant_slug:
        raise KnowledgeCorpusError("programs.yaml names a different tenant")
    plans: list[ProgramPlan] = []
    seen: set[str] = set()
    for index, item in enumerate(_items(raw, "programs")):
        where = f"programs[{index}]"
        code = _required(item, "code", where, maximum=32)
        if code in seen:
            raise KnowledgeCorpusError(f"{where}: program {code!r} is listed twice")
        seen.add(code)
        requirements: list[ProgramRequirement] = []
        courses: set[str] = set()
        for r_index, req in enumerate(item.get("requirements") or []):
            r_where = f"{where}.requirements[{r_index}]"
            if not isinstance(req, dict):
                raise KnowledgeCorpusError(f"{r_where}: expected a mapping")
            course = _required(req, "course", r_where, maximum=32).upper()
            if course in courses:
                raise KnowledgeCorpusError(f"{r_where}: {course} is listed twice for {code}")
            courses.add(course)
            category = _required(req, "category", r_where, maximum=40)
            if category not in _REQUIREMENT_CATEGORIES:
                raise KnowledgeCorpusError(
                    f"{r_where}: category {category!r} is not in {sorted(_REQUIREMENT_CATEGORIES)}"
                )
            term = req.get("term")
            if isinstance(term, bool) or not isinstance(term, int) or not 1 <= term <= 8:
                raise KnowledgeCorpusError(f"{r_where}: term must be an integer 1-8")
            requirements.append(
                ProgramRequirement(course_code=course, category=category, term=term)
            )
        plans.append(
            ProgramPlan(
                code=code,
                department=_required(item, "department", where, maximum=80),
                requirements=tuple(requirements),
            )
        )
    return tuple(plans)


def _load_help_articles(path: Path, tenant_slug: str) -> tuple[HelpArticle, ...]:
    if not path.exists():
        return ()
    raw = _load_yaml(path)
    if str(raw.get("tenant") or "") != tenant_slug:
        raise KnowledgeCorpusError("help-articles.yaml names a different tenant")
    articles: list[HelpArticle] = []
    for index, item in enumerate(_items(raw, "articles")):
        where = f"articles[{index}]"
        category = _required(item, "category", where, maximum=40)
        if category not in _HELP_CATEGORIES:
            raise KnowledgeCorpusError(
                f"{where}: category {category!r} is not in {sorted(_HELP_CATEGORIES)}"
            )
        articles.append(
            HelpArticle(
                category=category,
                question=_required(item, "question", where, maximum=240),
                answer=_required(item, "answer", where),
                sort_order=index * 10,
            )
        )
    return tuple(articles)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise KnowledgeCorpusError(f"{path.name}: {error}") from error
    if not isinstance(loaded, dict):
        raise KnowledgeCorpusError(f"{path.name}: expected a mapping at the top level")
    return loaded


def _items(document: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw = document.get(key)
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise KnowledgeCorpusError(f"{key}: expected a list of mappings")
    return raw


def _required(item: Mapping[str, Any], key: str, where: str, *, maximum: int | None = None) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeCorpusError(f"{where}: {key} is required")
    value = value.strip()
    if maximum is not None and len(value) > maximum:
        raise KnowledgeCorpusError(f"{where}: {key} is longer than {maximum} characters")
    return value


def _optional(
    item: Mapping[str, Any], key: str, where: str, *, maximum: int | None = None
) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise KnowledgeCorpusError(f"{where}: {key} must be a string")
    value = value.strip()
    if not value:
        return None
    if maximum is not None and len(value) > maximum:
        raise KnowledgeCorpusError(f"{where}: {key} is longer than {maximum} characters")
    return value


def _string_list(item: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = item.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise KnowledgeCorpusError(f"{where}: {key} must be a list of strings")
    cleaned = tuple(entry.strip() for entry in value if entry.strip())
    return cleaned


def _date(value: Any, where: str, key: str, *, required: bool) -> date | None:
    if value is None:
        if required:
            raise KnowledgeCorpusError(f"{where}: {key} is required")
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as error:
            raise KnowledgeCorpusError(f"{where}: {key} is not an ISO date") from error
    raise KnowledgeCorpusError(f"{where}: {key} is not a date")


def _time(value: Any, where: str, key: str) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value.strip())
        except ValueError as error:
            raise KnowledgeCorpusError(f"{where}: {key} is not a HH:MM time") from error
    raise KnowledgeCorpusError(f"{where}: {key} is not a time")


def _office(item: Mapping[str, Any], index: int) -> Office:
    where = f"offices[{index}]"
    code = _required(item, "code", where, maximum=16)
    if not _OFFICE_CODE.match(code):
        raise KnowledgeCorpusError(f"{where}: code {code!r} must be upper-case letters/digits")
    sla = item.get("sla_business_days")
    if sla is not None and (isinstance(sla, bool) or not isinstance(sla, int) or sla < 0):
        raise KnowledgeCorpusError(f"{where}: sla_business_days must be a non-negative integer")
    order = item.get("display_order", index)
    if isinstance(order, bool) or not isinstance(order, int):
        raise KnowledgeCorpusError(f"{where}: display_order must be an integer")
    return Office(
        code=code,
        name=_required(item, "name", where, maximum=160),
        short_name=_required(item, "short_name", where, maximum=80),
        component=_optional(item, "component", where, maximum=80),
        location=_required(item, "location", where, maximum=160),
        hours=_optional(item, "hours", where, maximum=240),
        email=_optional(item, "email", where, maximum=160),
        head_title=_optional(item, "head_title", where, maximum=160),
        sla_business_days=sla,
        description=_required(item, "description", where),
        services=_string_list(item, "services", where),
        display_order=order,
    )


def _calendar_entry(item: Mapping[str, Any], index: int) -> CalendarEntry:
    where = f"calendar[{index}]"
    code = _required(item, "code", where, maximum=80)
    if not _CODE.match(code):
        raise KnowledgeCorpusError(f"{where}: code {code!r} must be kebab-case")
    category = _required(item, "category", where, maximum=40)
    if category not in CALENDAR_CATEGORIES:
        raise KnowledgeCorpusError(
            f"{where}: category {category!r} is not in {sorted(CALENDAR_CATEGORIES)}"
        )
    audience = _optional(item, "audience", where, maximum=40) or "all"
    if audience not in CALENDAR_AUDIENCES:
        raise KnowledgeCorpusError(
            f"{where}: audience {audience!r} is not in {sorted(CALENDAR_AUDIENCES)}"
        )
    starts_on = _date(item.get("starts_on"), where, "starts_on", required=True)
    ends_on = _date(item.get("ends_on"), where, "ends_on", required=False)
    assert starts_on is not None
    if ends_on is not None and ends_on < starts_on:
        raise KnowledgeCorpusError(f"{where}: ends_on precedes starts_on")
    return CalendarEntry(
        code=code,
        term=_optional(item, "term", where, maximum=40),
        label=_required(item, "label", where, maximum=240),
        category=category,
        audience=audience,
        starts_on=starts_on,
        ends_on=ends_on,
        starts_at=_time(item.get("starts_at"), where, "starts_at"),
        ends_at=_time(item.get("ends_at"), where, "ends_at"),
        owner_office=_optional(item, "owner_office", where, maximum=16),
        document=_optional(item, "document", where, maximum=80),
        description=_optional(item, "description", where),
    )


def _document(path: Path, root: Path) -> Document:
    where = path.name
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(raw)
    if match is None:
        raise KnowledgeCorpusError(f"{where}: missing YAML front matter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise KnowledgeCorpusError(f"{where}: front matter is not valid YAML: {error}") from error
    if not isinstance(meta, dict):
        raise KnowledgeCorpusError(f"{where}: front matter must be a mapping")
    body = match.group(2).strip()
    if not body:
        raise KnowledgeCorpusError(f"{where}: empty body")

    code = _required(meta, "code", where, maximum=80)
    if not _CODE.match(code):
        raise KnowledgeCorpusError(f"{where}: code {code!r} must be kebab-case")
    if path.stem != code:
        raise KnowledgeCorpusError(f"{where}: file name must equal the code {code!r}")
    kind = _required(meta, "kind", where, maximum=20)
    if kind not in DOCUMENT_KINDS:
        raise KnowledgeCorpusError(f"{where}: kind {kind!r} is not in {sorted(DOCUMENT_KINDS)}")
    audience = _required(meta, "audience", where, maximum=20)
    if audience not in AUDIENCES:
        raise KnowledgeCorpusError(f"{where}: audience {audience!r} is not in {sorted(AUDIENCES)}")
    if kind == "internal" and audience != "internal":
        raise KnowledgeCorpusError(f"{where}: internal documents must have audience internal")
    status = _optional(meta, "status", where, maximum=20) or "published"
    if status not in STATUSES:
        raise KnowledgeCorpusError(f"{where}: status {status!r} is not in {sorted(STATUSES)}")
    version_raw = meta.get("version")
    version = str(version_raw).strip() if version_raw is not None else ""
    if not version or len(version) > 20:
        raise KnowledgeCorpusError(f"{where}: version is required (quote it in YAML)")
    effective_from = _date(meta.get("effective_from"), where, "effective_from", required=True)
    effective_until = _date(meta.get("effective_until"), where, "effective_until", required=False)
    assert effective_from is not None
    if effective_until is not None and effective_until < effective_from:
        raise KnowledgeCorpusError(f"{where}: effective_until precedes effective_from")

    applies_raw = meta.get("applies_to") or {}
    if not isinstance(applies_raw, dict):
        raise KnowledgeCorpusError(f"{where}: applies_to must be a mapping")
    applies_to: dict[str, list[str]] = {}
    for facet, values in applies_raw.items():
        if facet not in APPLIES_TO_FACETS:
            raise KnowledgeCorpusError(
                f"{where}: applies_to facet {facet!r} is not in {sorted(APPLIES_TO_FACETS)}"
            )
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, str) for v in values)
        ):
            raise KnowledgeCorpusError(f"{where}: applies_to.{facet} must be a non-empty list")
        allowed = APPLIES_TO_FACETS[facet]
        cleaned = [str(v).strip() for v in values]
        if allowed is not None:
            unknown = [v for v in cleaned if v not in allowed]
            if unknown:
                raise KnowledgeCorpusError(
                    f"{where}: applies_to.{facet} has unknown values {unknown}; "
                    f"allowed {sorted(allowed)}"
                )
        applies_to[facet] = cleaned

    sections = tuple(_sections(body))
    return Document(
        code=code,
        kind=kind,
        title=_required(meta, "title", where, maximum=200),
        summary=_required(meta, "summary", where),
        body=body,
        owner_office=_required(meta, "owner_office", where, maximum=16),
        audience=audience,
        status=status,
        version=version,
        effective_from=effective_from,
        effective_until=effective_until,
        supersedes=_optional(meta, "supersedes", where, maximum=80),
        applies_to=applies_to,
        related=_string_list(meta, "related", where),
        keywords=tuple(keyword.lower() for keyword in _string_list(meta, "keywords", where)),
        source_path=str(path.relative_to(root.parent)),
        sections=sections,
    )


def _sections(body: str) -> Iterable[Section]:
    """Split a Markdown body at its `##` headings.

    Text before the first heading becomes section 0 ("Overview") when present,
    so a document that opens with prose is still retrievable by that prose.
    """

    matches = list(_SECTION.finditer(body))
    ordinal = 0
    if matches and body[: matches[0].start()].strip():
        yield Section(ordinal=ordinal, heading="Overview", body=body[: matches[0].start()].strip())
        ordinal += 1
    elif not matches:
        yield Section(ordinal=0, heading="Overview", body=body.strip())
        return
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section_body = body[match.end() : end].strip()
        if not section_body:
            continue
        yield Section(ordinal=ordinal, heading=match.group(1).strip()[:200], body=section_body)
        ordinal += 1


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def stable_id(tenant_id: str, kind: str, code: str) -> UUID:
    return uuid5(_NAMESPACE, f"{tenant_id}:{kind}:{code}")


async def import_corpus(
    engine: AsyncEngine,
    corpus: Corpus,
    *,
    tenant_id: str,
    now: datetime | None = None,
) -> ImportReport:
    moment = now or datetime.now(UTC)
    report = ImportReport()
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"institution-knowledge:{tenant_id}"},
        )
        await _import_offices(connection, corpus, tenant_id, moment, report)
        await _import_documents(connection, corpus, tenant_id, moment, report)
        await _import_calendar(connection, corpus, tenant_id, moment, report)
        await _import_programs(connection, corpus, tenant_id, moment, report)
        await _import_help_articles(connection, corpus, tenant_id, moment, report)
    return report


async def _import_programs(
    connection: AsyncConnection,
    corpus: Corpus,
    tenant_id: str,
    moment: datetime,
    report: ImportReport,
) -> None:
    """Department per program and the core requirements against the active catalog.

    Requirements are replaced per program (delete + insert) rather than
    merged: the plan is the source of truth for which courses are required,
    and a course dropped from the plan must disappear from the audit.
    """

    if not corpus.programs:
        return
    catalog = (
        await connection.execute(
            text(
                "SELECT id FROM course_catalog_version WHERE tenant_id = :tenant_id "
                "AND status = 'active' ORDER BY effective_from DESC LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
    ).first()
    if catalog is None:
        report.warnings.append("no active course catalog version; program plans skipped")
        return
    catalog_id = str(catalog[0])
    courses = {
        str(row["code"]).upper(): str(row["id"])
        for row in (
            await connection.execute(
                text(
                    "SELECT id, code FROM catalog_course WHERE tenant_id = :tenant_id "
                    "AND catalog_version_id = :catalog_id AND active = true"
                ),
                {"tenant_id": tenant_id, "catalog_id": catalog_id},
            )
        ).mappings()
    }
    programs = {
        str(row["code"]): str(row["id"])
        for row in (
            await connection.execute(
                text("SELECT id, code FROM program WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        ).mappings()
    }
    for plan in corpus.programs:
        program_id = programs.get(plan.code)
        if program_id is None:
            report.warnings.append(f"program {plan.code} is not provisioned; plan skipped")
            continue
        missing = [r.course_code for r in plan.requirements if r.course_code not in courses]
        if missing:
            raise KnowledgeCorpusError(
                f"program {plan.code}: courses not in the active catalog: {', '.join(missing)}"
            )
        await connection.execute(
            text(
                "UPDATE program SET department = :department, updated_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {
                "department": plan.department,
                "now": moment,
                "id": program_id,
                "tenant_id": tenant_id,
            },
        )
        await connection.execute(
            text(
                "DELETE FROM program_requirement WHERE tenant_id = :tenant_id "
                "AND program_id = :program_id AND catalog_version_id = :catalog_id"
            ),
            {"tenant_id": tenant_id, "program_id": program_id, "catalog_id": catalog_id},
        )
        for requirement in plan.requirements:
            await connection.execute(
                text(
                    """
                    INSERT INTO program_requirement (
                      id, tenant_id, program_id, catalog_version_id, course_id, category,
                      recommended_term, required, created_at
                    ) VALUES (
                      :id, :tenant_id, :program_id, :catalog_id, :course_id, :category,
                      :term, true, :now
                    )
                    """
                ),
                {
                    "id": stable_id(
                        tenant_id, "program_requirement", f"{plan.code}:{requirement.course_code}"
                    ),
                    "tenant_id": tenant_id,
                    "program_id": program_id,
                    "catalog_id": catalog_id,
                    "course_id": courses[requirement.course_code],
                    "category": requirement.category,
                    "term": requirement.term,
                    "now": moment,
                },
            )
            report.program_requirements += 1
        report.programs += 1


async def _import_help_articles(
    connection: AsyncConnection,
    corpus: Corpus,
    tenant_id: str,
    moment: datetime,
    report: ImportReport,
) -> None:
    if not corpus.help_articles:
        return
    ids: list[str] = []
    for article in corpus.help_articles:
        article_id = stable_id(tenant_id, "help_article", f"{article.category}:{article.question}")
        ids.append(str(article_id))
        await connection.execute(
            text(
                """
                INSERT INTO help_article (
                  id, tenant_id, category, question, answer, sort_order, active,
                  created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :category, :question, :answer, :sort_order, true, :now, :now
                )
                ON CONFLICT (id) DO UPDATE SET
                  category = EXCLUDED.category, question = EXCLUDED.question,
                  answer = EXCLUDED.answer, sort_order = EXCLUDED.sort_order,
                  active = true, updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": article_id,
                "tenant_id": tenant_id,
                "category": article.category,
                "question": article.question,
                "answer": article.answer,
                "sort_order": article.sort_order,
                "now": moment,
            },
        )
        report.help_articles += 1
    await connection.execute(
        text(
            """
            UPDATE help_article SET active = false, updated_at = :now
            WHERE tenant_id = :tenant_id AND active = true
              AND NOT (CAST(id AS text) = ANY(CAST(:ids AS text[])))
            """
        ),
        {"tenant_id": tenant_id, "ids": ids, "now": moment},
    )


async def _import_offices(
    connection: AsyncConnection,
    corpus: Corpus,
    tenant_id: str,
    moment: datetime,
    report: ImportReport,
) -> None:
    codes = [office.code for office in corpus.offices]
    for office in corpus.offices:
        await connection.execute(
            text(
                """
                INSERT INTO institution_office (
                  id, tenant_id, code, name, short_name, component, location, hours, email,
                  head_title, sla_business_days, description, services, display_order,
                  active, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :code, :name, :short_name, :component, :location, :hours,
                  :email, :head_title, :sla, :description, :services, :display_order,
                  true, :now, :now
                )
                ON CONFLICT (tenant_id, code) DO UPDATE SET
                  name = EXCLUDED.name, short_name = EXCLUDED.short_name,
                  component = EXCLUDED.component, location = EXCLUDED.location,
                  hours = EXCLUDED.hours, email = EXCLUDED.email,
                  head_title = EXCLUDED.head_title,
                  sla_business_days = EXCLUDED.sla_business_days,
                  description = EXCLUDED.description, services = EXCLUDED.services,
                  display_order = EXCLUDED.display_order, active = true,
                  updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": stable_id(tenant_id, "office", office.code),
                "tenant_id": tenant_id,
                "code": office.code,
                "name": office.name,
                "short_name": office.short_name,
                "component": office.component,
                "location": office.location,
                "hours": office.hours,
                "email": office.email,
                "head_title": office.head_title,
                "sla": office.sla_business_days,
                "description": office.description,
                "services": list(office.services),
                "display_order": office.display_order,
                "now": moment,
            },
        )
        report.offices += 1
    await connection.execute(
        text(
            """
            UPDATE institution_office SET active = false, updated_at = :now
            WHERE tenant_id = :tenant_id AND active = true
              AND NOT (code = ANY(CAST(:codes AS text[])))
            """
        ),
        {"tenant_id": tenant_id, "codes": codes, "now": moment},
    )


async def _import_documents(
    connection: AsyncConnection,
    corpus: Corpus,
    tenant_id: str,
    moment: datetime,
    report: ImportReport,
) -> None:
    existing = {
        str(row["code"]): (str(row["id"]), str(row["content_sha256"]))
        for row in (
            await connection.execute(
                text(
                    "SELECT id, code, content_sha256 FROM institution_knowledge_document "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings()
    }
    for document in corpus.documents:
        document_id = stable_id(tenant_id, "document", document.code)
        digest = document.content_sha256
        previous = existing.get(document.code)
        if previous is not None and previous[1] == digest:
            report.documents_unchanged += 1
            continue
        await connection.execute(
            text(
                """
                INSERT INTO institution_knowledge_document (
                  id, tenant_id, code, kind, title, summary, body, owner_office_code, audience,
                  status, version, effective_from, effective_until, supersedes_code, applies_to,
                  related_codes, keywords, keywords_text, source_path, content_sha256,
                  created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :code, :kind, :title, :summary, :body, :owner, :audience,
                  :status, :version, :effective_from, :effective_until, :supersedes,
                  CAST(:applies_to AS jsonb), :related, :keywords, :keywords_text, :source_path,
                  :sha, :now, :now
                )
                ON CONFLICT (tenant_id, code) DO UPDATE SET
                  kind = EXCLUDED.kind, title = EXCLUDED.title, summary = EXCLUDED.summary,
                  body = EXCLUDED.body, owner_office_code = EXCLUDED.owner_office_code,
                  audience = EXCLUDED.audience, status = EXCLUDED.status,
                  version = EXCLUDED.version, effective_from = EXCLUDED.effective_from,
                  effective_until = EXCLUDED.effective_until,
                  supersedes_code = EXCLUDED.supersedes_code, applies_to = EXCLUDED.applies_to,
                  related_codes = EXCLUDED.related_codes, keywords = EXCLUDED.keywords,
                  keywords_text = EXCLUDED.keywords_text,
                  source_path = EXCLUDED.source_path,
                  content_sha256 = EXCLUDED.content_sha256,
                  updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": document_id,
                "tenant_id": tenant_id,
                "code": document.code,
                "kind": document.kind,
                "title": document.title,
                "summary": document.summary,
                "body": document.body,
                "owner": document.owner_office,
                "audience": document.audience,
                "status": document.status,
                "version": document.version,
                "effective_from": document.effective_from,
                "effective_until": document.effective_until,
                "supersedes": document.supersedes,
                "applies_to": _json(document.applies_to),
                "related": list(document.related),
                "keywords": list(document.keywords),
                "keywords_text": " ".join(document.keywords),
                "source_path": document.source_path,
                "sha": digest,
                "now": moment,
            },
        )
        await connection.execute(
            text("DELETE FROM institution_knowledge_section WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        for section in document.sections:
            await connection.execute(
                text(
                    """
                    INSERT INTO institution_knowledge_section (
                      id, tenant_id, document_id, ordinal, heading, body, created_at
                    ) VALUES (:id, :tenant_id, :document_id, :ordinal, :heading, :body, :now)
                    """
                ),
                {
                    "id": stable_id(tenant_id, "section", f"{document.code}#{section.ordinal}"),
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "ordinal": section.ordinal,
                    "heading": section.heading,
                    "body": section.body,
                    "now": moment,
                },
            )
            report.sections += 1
        if previous is None:
            report.documents_inserted += 1
        else:
            report.documents_updated += 1
    missing = sorted(set(existing) - {document.code for document in corpus.documents})
    if missing:
        result = await connection.execute(
            text(
                """
                UPDATE institution_knowledge_document
                SET status = 'retired', updated_at = :now
                WHERE tenant_id = :tenant_id AND status <> 'retired'
                  AND code = ANY(CAST(:codes AS text[]))
                """
            ),
            {"tenant_id": tenant_id, "codes": missing, "now": moment},
        )
        report.documents_retired = int(result.rowcount or 0)
        if report.documents_retired:
            report.warnings.append(
                f"retired documents no longer in the corpus: {', '.join(missing)}"
            )


async def _import_calendar(
    connection: AsyncConnection,
    corpus: Corpus,
    tenant_id: str,
    moment: datetime,
    report: ImportReport,
) -> None:
    for entry in corpus.calendar:
        await connection.execute(
            text(
                """
                INSERT INTO academic_calendar_entry (
                  id, tenant_id, code, term, label, category, audience, starts_on, ends_on,
                  starts_at, ends_at, owner_office_code, document_code, description,
                  created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :code, :term, :label, :category, :audience, :starts_on,
                  :ends_on, :starts_at, :ends_at, :owner, :document, :description, :now, :now
                )
                ON CONFLICT (tenant_id, code) DO UPDATE SET
                  term = EXCLUDED.term, label = EXCLUDED.label, category = EXCLUDED.category,
                  audience = EXCLUDED.audience, starts_on = EXCLUDED.starts_on,
                  ends_on = EXCLUDED.ends_on, starts_at = EXCLUDED.starts_at,
                  ends_at = EXCLUDED.ends_at, owner_office_code = EXCLUDED.owner_office_code,
                  document_code = EXCLUDED.document_code, description = EXCLUDED.description,
                  updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": stable_id(tenant_id, "calendar", entry.code),
                "tenant_id": tenant_id,
                "code": entry.code,
                "term": entry.term,
                "label": entry.label,
                "category": entry.category,
                "audience": entry.audience,
                "starts_on": entry.starts_on,
                "ends_on": entry.ends_on,
                "starts_at": entry.starts_at,
                "ends_at": entry.ends_at,
                "owner": entry.owner_office,
                "document": entry.document,
                "description": entry.description,
                "now": moment,
            },
        )
        report.calendar_entries += 1
    await connection.execute(
        text(
            """
            DELETE FROM academic_calendar_entry
            WHERE tenant_id = :tenant_id AND NOT (code = ANY(CAST(:codes AS text[])))
            """
        ),
        {"tenant_id": tenant_id, "codes": [entry.code for entry in corpus.calendar]},
    )


def _json(value: Mapping[str, Sequence[str]]) -> str:
    import json

    return json.dumps(dict(value), sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _resolve_tenant_id(engine: AsyncEngine, slug: str) -> str:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT id FROM tenant WHERE slug = :slug"), {"slug": slug}
            )
        ).first()
    if row is None:
        raise KnowledgeCorpusError(f"tenant {slug!r} is not provisioned in this database")
    return str(row[0])


def main(argv: Sequence[str] | None = None) -> None:
    import argparse
    import asyncio
    import os

    from audentra.bootstrap.settings import RuntimeSettings
    from audentra.infrastructure.db.engine import create_database_engine

    parser = argparse.ArgumentParser(
        description=(
            "Import a tenant's institutional knowledge corpus (offices, calendar, documents)"
        )
    )
    parser.add_argument("--tenant", required=True, help="tenant slug, e.g. aster-demo")
    parser.add_argument("--root", type=Path, help="override the knowledge directory")
    parser.add_argument("--check", action="store_true", help="validate only; touch no database")
    arguments = parser.parse_args(argv)

    root = arguments.root or default_corpus_root(arguments.tenant)
    try:
        corpus = load_corpus(root)
    except KnowledgeCorpusError as error:
        raise SystemExit(f"audentra-seed-knowledge: {error}") from error
    if corpus.tenant_slug != arguments.tenant:
        raise SystemExit(
            "audentra-seed-knowledge: corpus names tenant "
            f"{corpus.tenant_slug!r}, not {arguments.tenant!r}"
        )
    print(
        f"Loaded {len(corpus.documents)} documents "
        f"({sum(len(d.sections) for d in corpus.documents)} sections), "
        f"{len(corpus.offices)} offices, {len(corpus.calendar)} calendar entries, "
        f"{len(corpus.programs)} program plans, {len(corpus.help_articles)} help articles "
        f"from {root}"
    )
    if arguments.check:
        return

    async def run() -> ImportReport:
        settings = RuntimeSettings.from_environment(os.environ)
        engine = create_database_engine(settings.database_url, settings.database)
        try:
            tenant_id = await _resolve_tenant_id(engine, arguments.tenant)
            return await import_corpus(engine, corpus, tenant_id=tenant_id)
        finally:
            await engine.dispose()

    try:
        report = asyncio.run(run())
    except KnowledgeCorpusError as error:
        raise SystemExit(f"audentra-seed-knowledge: {error}") from error
    print(
        f"Imported: {report.documents_inserted} new, {report.documents_updated} updated, "
        f"{report.documents_unchanged} unchanged, {report.documents_retired} retired documents; "
        f"{report.sections} sections; {report.offices} offices; "
        f"{report.calendar_entries} calendar entries; {report.programs} program plans "
        f"({report.program_requirements} requirements); {report.help_articles} help articles"
    )
    for warning in report.warnings:
        print(f"  warning: {warning}")


if __name__ == "__main__":
    main()
