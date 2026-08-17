"""Presentation-policy tests for Edward's composed answers.

The response contract: prose answers and synthesizes; structured blocks carry
collections exactly once. These tests pin the policy deterministically —
no detail duplicated between prose and blocks, links point only at real
portal routes, single items and empty results stay prose, and the rewrite
model is told what blocks will render under its reply.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from audentra.integrations.assistant.blocks import (
    describe_blocks_for_prompt,
    table_block,
)
from audentra.integrations.assistant.classify import Classification, classify
from audentra.integrations.assistant.compose import compose_deterministic
from audentra.integrations.assistant.derive import derive_student_state
from audentra.integrations.assistant.normalize import normalize_request
from audentra.integrations.assistant.pipeline import AssistantPipeline
from audentra.integrations.assistant.tools import AssistantToolHost, execute_tool_reads
from audentra.integrations.staff_assistant.classify import StaffClassification
from audentra.integrations.staff_assistant.compose import compose_staff_deterministic
from audentra.integrations.staff_assistant.derive import StaffDerivedState
from audentra.integrations.staff_assistant.pipeline import StaffAssistantPipeline
from audentra.integrations.staff_assistant.tools import StaffAssistantToolHost

# Every route the portals app router actually serves (static pages plus the
# documented dynamic families). A produced href that matches nothing here is
# an invented route — the exact failure this pattern exists to catch.
_REAL_ROUTE = re.compile(
    r"^/(?:dashboard|enrollment|documents|financials|payments|appointments|"
    r"messages|help|onboarding|profile|classrooms|campus-life)"
    r"(?:/requirements/[a-z0-9-]+|/clubs/[A-Za-z0-9%-]+)?"
    r"(?:\?[\w=%&-]+)?$"
    r"|^/staff#(?:overview|tasks|students|outreach|messages|knowledge|core_plays)$"
)


def _all_hrefs(blocks: list[dict[str, Any]]) -> list[str]:
    hrefs: list[str] = []
    for block in blocks:
        for item in block.get("items", []):
            if item.get("href"):
                hrefs.append(str(item["href"]))
        for href in block.get("rowHrefs", []) or []:
            if href:
                hrefs.append(str(href))
    return hrefs


def _classify(text: str) -> Classification:
    result = classify(normalize_request(text))
    assert result is not None, f"deterministic classification missing for {text!r}"
    return result


def _fixture_host(primitives: dict[str, dict[str, Any]]) -> AssistantToolHost:
    def reader(value: dict[str, Any]) -> Any:
        async def read() -> dict[str, Any]:
            return value

        return read

    return AssistantToolHost({name: reader(value) for name, value in primitives.items()})


def _student_state(primitives: dict[str, dict[str, Any]], tools: list[str]) -> Any:
    async def build() -> Any:
        from datetime import UTC, datetime

        execution = await execute_tool_reads(
            tools, _fixture_host(primitives), now=datetime(2026, 8, 16, tzinfo=UTC)
        )
        return derive_student_state(execution)

    return asyncio.run(build())


_CLUBS = [
    {
        "id": f"club-{index}",
        "name": name,
        "category": category,
        "description": description,
        "nextActivity": None,
    }
    for index, (name, category, description) in enumerate(
        [
            ("Robotics Club", "engineering", "Build competition robots together."),
            ("Jazz Ensemble", "music", "Weekly jam sessions and gigs."),
            ("Debate Society", "academic", "Competitive parliamentary debate."),
            ("Trail Runners", "sports", "Saturday trail runs, all paces."),
        ]
    )
]


def test_four_clubs_prose_synthesizes_and_rows_link_to_real_club_pages() -> None:
    state = _student_state({"campus_life": {"events": [], "clubs": _CLUBS}}, ["getCampusLife"])
    answer = compose_deterministic(_classify("What clubs can I join?"), state)

    # The count and the spread of interests belong in prose; descriptions
    # appear once, in the list.
    assert "4 clubs" in answer.message
    for club in _CLUBS:
        assert str(club["description"]) not in answer.message
    club_block = next(block for block in answer.blocks if block.get("items"))
    assert len(club_block["items"]) == 4
    hrefs = _all_hrefs(answer.blocks)
    assert hrefs == [f"/campus-life/clubs/club-{index}" for index in range(4)]
    for href in hrefs:
        assert _REAL_ROUTE.match(href), href


_CHECKLIST = {
    "items": [
        {
            "id": f"req-{index}",
            "code": code,
            "slug": slug,
            "title": title,
            "status": "ready",
            "blocking": True,
            "documentCategory": category,
        }
        for index, (code, slug, title, category) in enumerate(
            [
                (
                    "official_transcript",
                    "transcript-upload",
                    "Submit your official transcript",
                    "transcript",
                ),
                (
                    "identity_document",
                    "identity-document-upload",
                    "Upload an identity document",
                    "identity",
                ),
                (
                    "immunization_record",
                    "immunization-upload",
                    "Submit your immunization record",
                    "health",
                ),
            ]
        )
    ]
}


def test_multiple_checklist_items_count_in_prose_list_in_block() -> None:
    state = _student_state(
        {"requirements": _CHECKLIST, "documents": {"items": []}},
        ["getOnboardingChecklist"],
    )
    answer = compose_deterministic(_classify("What's left on my checklist?"), state)

    assert "3 checklist steps" in answer.message
    # No step title is walked through in prose — the block carries them.
    for item in _CHECKLIST["items"]:
        assert str(item["title"]) not in answer.message
    steps = next(block for block in answer.blocks if block["type"] == "next_steps")
    assert len(steps["items"]) == 3


def test_returned_checklist_step_is_the_named_exception() -> None:
    checklist = {
        "items": [
            dict(item, **({"status": "rejected"} if index == 0 else {}))
            for index, item in enumerate(_CHECKLIST["items"])
        ]
    }
    state = _student_state(
        {"requirements": checklist, "documents": {"items": []}},
        ["getOnboardingChecklist"],
    )
    answer = compose_deterministic(_classify("What's left on my checklist?"), state)

    assert "Submit your official transcript was returned" in answer.message
    assert "needs your attention again" in answer.message


def test_multiple_documents_prose_plus_table_without_row_duplication() -> None:
    state = _student_state(
        {"requirements": _CHECKLIST, "documents": {"items": []}},
        ["getOnboardingChecklist", "getDocumentStatuses"],
    )
    answer = compose_deterministic(_classify("Where do my documents stand?"), state)

    table = next(block for block in answer.blocks if block["type"] == "table")
    assert len(table["rows"]) == 3
    # Explanatory prose + table: the sentence synthesizes states, the table
    # holds the rows, and the rows deep-link to their requirement pages.
    assert "3 required documents" in answer.message
    assert "3 still to submit" in answer.message
    for row in table["rows"]:
        assert str(row["document"]) not in answer.message
    assert table["rowHrefs"] == [
        "/enrollment/requirements/transcript-upload",
        "/enrollment/requirements/identity-document-upload",
        "/enrollment/requirements/immunization-upload",
    ]
    for href in _all_hrefs(answer.blocks):
        assert _REAL_ROUTE.match(href), href


def test_missing_documents_list_carries_actionable_deep_links() -> None:
    state = _student_state(
        {"requirements": _CHECKLIST, "documents": {"items": []}},
        ["getOnboardingChecklist", "getDocumentStatuses"],
    )
    answer = compose_deterministic(_classify("What documents do I still need to send?"), state)

    assert "3 documents" in answer.message
    steps = next(block for block in answer.blocks if block["type"] == "next_steps")
    hrefs = [item["href"] for item in steps["items"]]
    assert hrefs == [
        "/enrollment/requirements/transcript-upload",
        "/enrollment/requirements/identity-document-upload",
        "/enrollment/requirements/immunization-upload",
    ]
    # Imperative checklist titles become noun phrases inside the action label.
    assert steps["items"][0]["text"] == "Upload your official transcript"


_AID = {
    "academicYear": "2026-2027",
    "awards": [
        {
            "name": "Aster Grant",
            "type": "grant",
            "status": "accepted",
            "offeredAmountCents": 1_000_000,
            "acceptedAmountCents": 1_000_000,
        },
    ],
    "requiredDocuments": [
        {"code": "fafsa", "title": "FAFSA", "status": "pending", "href": "/financials"},
        {
            "code": "verification_worksheet",
            "title": "Verification worksheet",
            "status": "pending",
            "href": "/financials",
        },
        {
            "code": "award_acceptance",
            "title": "Award acceptance",
            "status": "not_started",
            "href": "/financials",
        },
    ],
    "paymentSchedule": [],
}


def test_financial_aid_requirements_not_enumerated_next_to_their_block() -> None:
    state = _student_state({"financials": _AID}, ["getFinancialAidStatus"])
    answer = compose_deterministic(_classify("What's my financial aid status?"), state)

    assert "3 requirements" in answer.message
    assert "Verification worksheet" not in answer.message
    steps = next(block for block in answer.blocks if block["type"] == "next_steps")
    assert len(steps["items"]) == 3


_DEADLINE_REQUIREMENTS = {
    "items": [
        {
            "id": f"req-{index}",
            "code": code,
            "slug": slug,
            "title": title,
            "status": "ready",
            "blocking": True,
            "dueAt": due,
        }
        for index, (code, slug, title, due) in enumerate(
            [
                (
                    "official_transcript",
                    "transcript-upload",
                    "Submit your official transcript",
                    "2026-08-10T00:00:00Z",
                ),
                (
                    "immunization_record",
                    "immunization-upload",
                    "Submit your immunization record",
                    "2026-08-20T00:00:00Z",
                ),
                (
                    "housing_preference",
                    "housing-preference",
                    "Select a housing preference",
                    "2026-09-10T00:00:00Z",
                ),
            ]
        )
    ]
}


def test_deadlines_table_synthesized_prose_and_linked_rows() -> None:
    state = _student_state(
        {
            "requirements": _DEADLINE_REQUIREMENTS,
            "payments": {"items": []},
            "dashboard": {"offer": {}},
        },
        ["getStudentDeadlines"],
    )
    answer = compose_deterministic(_classify("What deadlines do I have?"), state)

    table = next(block for block in answer.blocks if block["type"] == "table")
    assert len(table["rows"]) == 3
    # The one overdue item is the exception prose must surface.
    assert "past due" in answer.message
    assert "Submit your official transcript" in answer.message
    assert "Submit your immunization record" not in answer.message
    assert table["rowHrefs"][0] == "/enrollment/requirements/transcript-upload"
    for href in _all_hrefs(answer.blocks):
        assert _REAL_ROUTE.match(href), href


def test_single_deadline_is_a_sentence_not_a_table() -> None:
    single = {"items": _DEADLINE_REQUIREMENTS["items"][1:2]}
    state = _student_state(
        {"requirements": single, "payments": {"items": []}, "dashboard": {"offer": {}}},
        ["getStudentDeadlines"],
    )
    answer = compose_deterministic(_classify("What deadlines do I have?"), state)

    assert not any(block["type"] == "table" for block in answer.blocks)
    assert "2026-08-20" in answer.message


def test_single_document_requirement_is_prose_with_one_action() -> None:
    single = {"items": _CHECKLIST["items"][:1]}
    state = _student_state(
        {"requirements": single, "documents": {"items": []}},
        ["getOnboardingChecklist", "getDocumentStatuses"],
    )
    answer = compose_deterministic(_classify("Where do my documents stand?"), state)

    assert not any(block["type"] == "table" for block in answer.blocks)
    assert "official transcript" in answer.message
    assert "is not submitted yet" in answer.message


def test_housing_options_render_once_as_a_list() -> None:
    state = _student_state(
        {
            "housing_plan": {
                "preference": None,
                "residences": [
                    {"name": "North Hall", "description": "Suite-style rooms."},
                    {"name": "Cedar House", "description": "Quiet residential college."},
                ],
            }
        },
        ["getHousingOptions"],
    )
    answer = compose_deterministic(_classify("What housing options are there?"), state)

    assert "2 housing options are" in answer.message
    assert "North Hall" not in answer.message
    options = next(block for block in answer.blocks if block.get("items"))
    assert len(options["items"]) == 2


def test_empty_result_is_prose_only() -> None:
    state = _student_state({"campus_life": {"events": [], "clubs": []}}, ["getCampusLife"])
    answer = compose_deterministic(_classify("What clubs can I join?"), state)

    assert [block["type"] for block in answer.blocks] == ["text"]
    assert "No campus events or clubs" in answer.message


def test_cross_domain_answer_merges_without_duplicating_blocks() -> None:
    state = _student_state(
        {
            "requirements": _CHECKLIST,
            "documents": {"items": []},
            "campus_life": {"events": [], "clubs": _CLUBS},
        },
        ["getOnboardingChecklist", "getDocumentStatuses", "getCampusLife"],
    )
    classification = Classification(
        "remaining_steps", 0.9, additional_request_types=("campus_life",)
    )
    answer = compose_deterministic(classification, state)

    assert "3 checklist steps" in answer.message
    assert "4 clubs" in answer.message
    kinds = [block["type"] for block in answer.blocks]
    assert kinds.count("next_steps") == 1
    assert kinds.count("bullet_list") == 1


def test_unavailable_read_answer_stays_honest_and_linkless() -> None:
    async def failing_read() -> dict[str, Any]:
        raise RuntimeError("backend down")

    async def build() -> Any:
        from datetime import UTC, datetime

        host = AssistantToolHost({"requirements": failing_read})
        execution = await execute_tool_reads(
            ["getOnboardingChecklist"], host, now=datetime(2026, 8, 16, tzinfo=UTC)
        )
        return derive_student_state(execution)

    state = asyncio.run(build())
    answer = compose_deterministic(_classify("What's left on my checklist?"), state)

    assert "couldn't check" in answer.message
    assert _all_hrefs(answer.blocks) == []


def test_unsupported_request_is_prose_only() -> None:
    state = _student_state({}, [])
    classification = Classification("unsupported_or_out_of_scope", 0.9)
    answer = compose_deterministic(classification, state)

    assert [block["type"] for block in answer.blocks] == ["text"]
    assert "can't check that" in answer.message


# ---------------------------------------------------------------------------
# Rewrite integration: the model is told what renders under its prose.
# ---------------------------------------------------------------------------


def test_pipeline_passes_block_descriptions_to_the_rewrite_model() -> None:
    seen: dict[str, Any] = {}

    async def composer(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"answer": "You have three documents to send; the table below lists them."}

    primitives: dict[str, dict[str, Any]] = {
        "requirements": _CHECKLIST,
        "documents": {"items": []},
    }

    async def build() -> None:
        pipeline = AssistantPipeline(_fixture_host(primitives), model_composer=composer)
        await pipeline.execute(message="Where do my documents stand?")

    asyncio.run(build())
    described = seen.get("presented_blocks")
    assert described and any("table" in line for line in described)
    assert all("transcript" not in line.lower() for line in described)


def test_describe_blocks_reports_shape_not_rows() -> None:
    block = table_block(
        [{"key": "a", "label": "Document"}, {"key": "b", "label": "Status"}],
        [{"a": "Transcript", "b": "Accepted"}],
        caption="Document status",
    )
    lines = describe_blocks_for_prompt([block])
    assert lines == ["A table “Document status” with 1 row(s) (columns: Document, Status)."]


def test_table_row_hrefs_reject_external_urls() -> None:
    block = table_block(
        [{"key": "a", "label": "A"}],
        [{"a": "1"}, {"a": "2"}],
        row_hrefs=["https://evil.example", "/documents"],
    )
    assert block["rowHrefs"] == [None, "/documents"]


# ---------------------------------------------------------------------------
# Staff Edward
# ---------------------------------------------------------------------------


def test_staff_cohort_list_is_a_table_with_a_real_directory_link() -> None:
    async def find_students(*, cohort: Any, limit: int) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": f"80000000-0000-7000-8000-00000000010{index}",
                    "name": name,
                    "programName": "Computer Science",
                    "classYear": 2027,
                    "offerStatus": "accepted",
                    "depositState": "unpaid",
                    "depositPaid": False,
                    "requirements": {"total": 8, "completed": 3, "openBlocking": 2},
                }
                for index, name in enumerate(["Jordan Ellis", "Sam Reyes", "Ana Cruz"])
            ],
            "returned": 3,
            "total": 3,
            "truncated": False,
            "filter": ["admission offer status = accepted", "deposit state = unpaid"],
        }

    pipeline = StaffAssistantPipeline(StaffAssistantToolHost({"find_students": find_students}))
    result = asyncio.run(
        pipeline.execute(message="Which admitted students haven't paid their deposit?")
    )

    assert "3 students match" in result.message
    assert "Jordan Ellis" not in result.message
    table = next(block for block in result.blocks if block["type"] == "table")
    assert [row["name"] for row in table["rows"]] == ["Jordan Ellis", "Sam Reyes", "Ana Cruz"]
    steps = next(block for block in result.blocks if block["type"] == "next_steps")
    assert steps["items"][0]["href"] == "/staff#students"
    for href in _all_hrefs(result.blocks):
        assert _REAL_ROUTE.match(href), href


def test_staff_blockers_prose_splits_ownership_without_reciting_titles() -> None:
    state = StaffDerivedState(
        student={"name": "Maya Chen", "preferredName": "Maya"},
        blockers=[
            {
                "title": "Submit your official transcript",
                "owner": "university",
                "clearingAction": "Waiting on review.",
            },
            {
                "title": "Pay the enrollment deposit",
                "owner": "student",
                "clearingAction": "Pay from the portal.",
            },
            {
                "title": "Submit your immunization record",
                "owner": "student",
                "clearingAction": "Upload the record.",
            },
        ],
    )
    answer = compose_staff_deterministic(StaffClassification("student_blockers", 0.9), state)

    assert "3 items" in answer.message
    assert "2 need action from the student" in answer.message
    assert "Pay the enrollment deposit" not in answer.message
    steps = next(block for block in answer.blocks if block["type"] == "next_steps")
    assert len(steps["items"]) == 3


def test_staff_work_queue_prose_does_not_recite_the_first_row() -> None:
    state = StaffDerivedState(
        queue={
            "items": [
                {
                    "id": "w1",
                    "key": "ENR-104",
                    "title": "Chase missing transcript",
                    "status": "todo",
                    "priority": "urgent",
                    "dueAt": "2026-08-18T00:00:00Z",
                    "student": {"name": "Maya Chen"},
                },
                {
                    "id": "w2",
                    "key": "ENR-105",
                    "title": "Verify aid worksheet",
                    "status": "todo",
                    "priority": "high",
                    "dueAt": "2026-08-19T00:00:00Z",
                    "student": {"name": "Sam Reyes"},
                },
            ],
            "counts": {"urgent": 1, "escalated": 0},
        },
    )
    answer = compose_staff_deterministic(StaffClassification("work_queue", 0.9), state)

    assert "2 open items" in answer.message
    assert "ENR-104" in answer.message  # the head of the queue is the answer
    assert "Chase missing transcript" not in answer.message  # its row is not
    steps = next(block for block in answer.blocks if block["type"] == "next_steps")
    assert steps["items"][0]["href"] == "/staff#tasks"


def test_staff_cohort_aggregate_buckets_render_as_a_table() -> None:
    state = StaffDerivedState(
        cohort_summary={
            "filter": ["admission offer status = accepted"],
            "matchingStudents": 12,
            "groupBy": "program",
            "countsRepresent": "students",
            "buckets": [
                {"value": "Computer Science", "count": 7},
                {"value": "Biology", "count": 5},
            ],
        },
    )
    answer = compose_staff_deterministic(StaffClassification("cohort_aggregate", 0.9), state)

    assert "12 students match" in answer.message
    assert "Computer Science" not in answer.message
    table = next(block for block in answer.blocks if block["type"] == "table")
    assert len(table["rows"]) == 2


def test_guard_catches_stops_you_from_housing_phrasing() -> None:
    from audentra.integrations.assistant.guard import (
        build_causal_guards,
        guard_grounded_answer,
    )

    guards = build_causal_guards(
        housing_gates=[
            {"code": "enrollment_deposit_posted", "satisfied": False},
            {"code": "official_transcript", "satisfied": False},
        ]
    )
    evidence = ["Housing eligibility: blocked by earlier checklist items."]
    invented = guard_grounded_answer(
        answer=(
            "Your unfinished aid verification does stop you from applying for housing right now."
        ),
        evidence_texts=evidence,
        causal_guards=guards,
    )
    assert not invented.accepted
    assert invented.reason_code == "invented_causation"
    # The truthful negation of the same claim must stay acceptable.
    negated = guard_grounded_answer(
        answer=(
            "Your aid verification does not stop you from applying for "
            "housing; the deposit and transcript on your checklist come first."
        ),
        evidence_texts=evidence,
        causal_guards=guards,
    )
    assert negated.accepted


def test_rewrite_cannot_drop_the_hold_system_caveat() -> None:
    from audentra.integrations.staff_assistant.pipeline import _restore_dropped_caveats

    draft = (
        "Maya is blocked by 3 items — listed below. (No registrar hold system "
        "exists — these derived blockers are the complete list.)"
    )
    softened = "Maya is blocked by three open requirements, shown in the table below."
    restored = _restore_dropped_caveats(softened, draft)
    assert "no registrar hold system" in restored.lower()
    # An answer that kept the caveat is left alone.
    assert _restore_dropped_caveats(restored, draft) == restored


@pytest.mark.parametrize(
    "href",
    [
        "/enrollment/requirements/transcript-upload",
        "/documents?document=abc-123",
        "/campus-life/clubs/club-7",
        "/staff#tasks",
    ],
)
def test_route_pattern_accepts_documented_routes(href: str) -> None:
    assert _REAL_ROUTE.match(href)


@pytest.mark.parametrize(
    "href",
    ["/registrar", "/staff#billing", "https://example.com", "/housing", "/tasks"],
)
def test_route_pattern_rejects_unknown_routes(href: str) -> None:
    assert not _REAL_ROUTE.match(href)
