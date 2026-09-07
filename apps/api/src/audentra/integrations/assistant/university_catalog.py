"""Shared v3 evidence tools. Identity stays bound by the existing executors."""

UNIVERSITY_TOOLS = {
    "getUniversityOverview": (
        "overview",
        (
            "Read current admission/lifecycle, actual term credit loads, "
            "posted balances, independent official holds with their owning "
            "offices, and institutional deadlines."
        ),
    ),
    "getUniversityAcademics": (
        "academics",
        (
            "Read actual registrations and historical course attempts/grades, "
            "accepted versus pending transfer evaluations, evidence-based SAP "
            "history, prerequisites, waitlist offers, individual exceptions "
            "and explicitly unresolved degree requirements. Use for academic "
            "progress or course-drop consequences."
        ),
    ),
    "getUniversityAccount": (
        "account",
        (
            "Read the canonical posted ledger in integer cents, exact term "
            "balances including credit owed, pending/failed/reversed payments,"
            " annual accepted awards versus term disbursements, employment "
            "awards, SAP and actual holds. Pending money is never posted "
            "money."
        ),
    ),
    "getUniversityRelationships": (
        "relationships",
        (
            "Read named cross-office advisers and active leave coverage, "
            "appointments/no-shows, physical housing/compatible placement, "
            "effective/revoked FERPA consent and individual exceptions. Staff "
            "additionally see case owners, handoff steps, dependencies and "
            "delivery failures."
        ),
    ),
    "getUniversityDocuments": (
        "documents",
        (
            "Read required documents including not submitted, current review "
            "status, rejection reasons and original revisions with effective "
            "and recorded timestamps."
        ),
    ),
    "getUniversityHistory": (
        "history",
        (
            "Read historical events bounded by BOTH knownAt (recorded time) "
            "and effectiveAt. Optional entityType and entityId focus the event history; "
            "external grade corrections use entityType=transfer_credit, institutional "
            "course grades use enrollment. There is no grade entity type. "
            "Use ISO timestamps with timezone. Defaults to "
            "the fixed university clock. Current dossier fields cannot answer "
            "what the university knew before a late grade correction."
        ),
    ),
}

UNIVERSITY_CONTEXT = (
    "This is a read-only evidence path: never claim a record was changed or an "
    "action succeeded. If asked to perform an unavailable write, explain the "
    "boundary and use records only to explain the current situation. "
    "This tenant uses Synthetic University v3 in PostgreSQL. Prefer getUniversity* "
    "tools for institutional facts. The tool snapshotAt is the university's fixed "
    "clock, not wall-clock today. Retrieve policy passages for rules and combine "
    "them with current student evidence and scoped exceptions. Cite source code, "
    "version and section in policy answers. Never infer citizenship from domestic "
    "residence, count pending transfers or money, equate approval with downstream "
    "completion, or certify graduation from unresolved curriculum distributions. "
    "Individual exception scope comes from its own record and linked "
    "policy, never a similarly named accommodation policy. Course "
    "impacts calculate the approved floor; do not invent a different "
    "minimum. "
    "Use policyReferences and exception policy_id to retrieve the governing document. "
    "Course waitlist seats are not admission offers or housing offers. For a drop with "
    "requiresPriorDSOApproval=true, explicitly say the student must obtain prior "
    "ISS/DSO approval before dropping; academic and aid advice alone is incomplete. "
    "For historical corrections compare the requested knownAt cutoff with a current "
    "history read for the same entityType (e.g. transfer_credit); do not infer missing "
    "events when hasMoreEvents=false and there is no truncatedItems marker. "
    "A FERPA release is optional and never a registration condition. "
    "Historical questions require getUniversityHistory with both cutoffs. "
    "Dates and deadlines use America/New_York, including late-evening deadlines whose "
    "UTC date is the following day. Use the local date, never the UTC calendar date. "
    "A missed optional appointment creates no automatic penalty. Amounts with "
    "negative balance indicate credit owed, not proof of a settled refund."
)


def policy_evidence_blocks(calls: object) -> list[dict[str, object]]:
    """Provenance comes from executed reads even if the model omits citations.

    These are labeled retrieved evidence, not a claim that every passage applies
    or supports every sentence. Lab retains the full passage/ranking trace.
    """
    from audentra.integrations.assistant.read_loop import LoopCall

    if not isinstance(calls, list):
        return []
    citations: dict[str, str] = {}
    for call in calls:
        if not isinstance(call, LoopCall) or call.status != "available":
            continue
        if not isinstance(call.result, dict):
            continue
        for source in call.result.get("sources", []):
            if not isinstance(source, dict) or source.get("applicability") == "does_not_apply":
                continue
            if source.get("citation") and source.get("content_hash"):
                citations[str(source["citation"])] = str(source["citation"])
    if not citations:
        return []
    values = list(citations)[:5]
    content = "Policy passages retrieved:\n" + "\n".join(values)
    return [{"type": "text", "text": content, "fallbackText": content}]
