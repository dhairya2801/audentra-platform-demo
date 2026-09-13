"""Portal contract projections over v3 evidence. No independent financial truth."""

from typing import Any

JsonDict = dict[str, Any]
GRADE_POINTS = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}


def financials(account: JsonDict, documents: JsonDict) -> JsonDict:
    term = "2026FA"
    ledger = [r for r in account["ledger"] if r["term_id"] == term]
    charges = sum(r["amount_cents"] for r in ledger if r["kind"] == "charge")
    aid = -sum(r["amount_cents"] for r in ledger if r["kind"] == "aid")
    paid = -sum(r["amount_cents"] for r in ledger if r["kind"] == "payment")
    payment_ids = {r["id"] for r in ledger if r["kind"] == "payment"}
    paid -= sum(
        r["amount_cents"]
        for r in ledger
        if r["kind"] == "reversal" and r.get("reverses_id") in payment_ids
    )
    balance = sum(r["amount_cents"] for r in ledger)
    awards = [
        {
            "id": r["id"],
            "source": "institutional" if r["source"] == "employment" else r["source"],
            "name": r["name"],
            "type": "work_study"
            if not r["posts_to_account"]
            else "loan"
            if "loan" in r["name"].lower()
            else "grant",
            "offeredAmountCents": r["offered_cents"],
            "acceptedAmountCents": r["accepted_cents"],
            "status": r["status"],
            "requiresAction": r["status"] == "offered",
            "updatedAt": account["snapshotAt"],
        }
        for r in account["awards"]
    ]
    latest = account["sap"][0] if account["sap"] else None
    statuses = {
        "ACCEPTED": "verified",
        "WAIVED": "verified",
        "UNDER_REVIEW": "under_review",
        "UPLOADED": "submitted",
        "REJECTED": "action_required",
        "EXPIRED": "action_required",
        "NEEDS_RESUBMISSION": "action_required",
    }
    required = [
        {
            "id": r["id"],
            "code": r["category"],
            "title": r["category"].replace("_", " ").title(),
            "description": "Financial Aid document evidence",
            "status": statuses.get(r["status"], "not_started"),
            "dueAt": None,
            "documentId": None if r["status"] == "NOT_SUBMITTED" else r["id"],
            "href": "/documents",
            "version": r["version"],
            "updatedAt": account["snapshotAt"],
        }
        for r in documents["documents"]
        if r["office_id"] == "FA"
    ]
    return {
        "academicYear": "2026-2027",
        "costOfAttendanceCents": charges,
        "acceptedAidCents": sum(
            r["accepted_cents"] for r in account["awards"] if r["posts_to_account"]
        ),
        "pendingAidCents": sum(
            r["offered_cents"]
            for r in account["awards"]
            if r["posts_to_account"] and r["status"] == "offered"
        ),
        "paymentsCents": paid,
        "remainingBalanceCents": balance,
        "awards": awards,
        "requiredDocuments": required,
        "paymentPlans": [],
        "paymentSchedule": [],
        "accountBasis": "posted_ledger",
        "termLabel": "Fall 2026",
        "postedAidCents": aid,
        "postedChargesCents": charges,
        "accountAdjustmentsCents": sum(
            r["amount_cents"]
            for r in ledger
            if r["kind"] in ("reversal", "refund", "credit_adjustment")
        ),
        "ledger": ledger,
        "disbursements": account["disbursements"],
        "transactions": account["payments"],
        "sap": {
            "status": {"suspension": "not_meeting"}.get(latest["status"], latest["status"])
            if latest
            else "not_evaluated",
            "cumulativeGpa": latest["gpa"] if latest else None,
            "minimumGpa": 2.0,
            "completionRatePercent": round(latest["completion_rate"] * 100, 1)
            if latest and latest["completion_rate"] is not None
            else None,
            "minimumCompletionRatePercent": 67,
            "attemptedCredits": latest["attempted_credits"] if latest else 0,
            "maximumAttemptedCredits": round(account["student"]["degree_credits"] * 1.5),
        },
        "generatedAt": account["snapshotAt"],
    }


def academics(record: JsonDict) -> JsonDict:
    student = record["student"]
    program = {
        "id": student["program_id"],
        "code": student["program_id"],
        "name": student["program_name"],
        "degree": student["program_id"].split("-")[0].upper(),
        "totalCredits": student["degree_credits"],
        "description": student["department"],
    }
    attempts = record["attempts"]
    # A passed course earns credit once even if the source later gains repeats.
    passed = {
        r["course_id"]: r
        for r in attempts
        if r["status"] == "completed" and r["grade"] in ("A", "B", "C", "D", "P")
    }
    transfers = {r["course_id"]: r for r in record["transfers"] if r["status"] == "accepted"}
    active = {r["course_id"] for r in attempts if r["status"] == "enrolled"}
    plan = []
    for req in record["requirements"]:
        if not req["course_id"]:
            continue
        dependencies = [p for p in record["prerequisites"] if p["course_id"] == req["course_id"]]
        satisfied: list[str] = []
        missing: list[str] = []
        for dep in dependencies:
            earlier = passed.get(dep["required_course_id"]) or transfers.get(
                dep["required_course_id"]
            )
            qualifies = earlier and GRADE_POINTS.get(earlier["grade"], -1) >= GRADE_POINTS.get(
                dep["minimum_grade"], 2
            )
            (satisfied if qualifies else missing).append(dep["required_code"])
        state = (
            "completed"
            if req["course_id"] in passed
            else "exempted"
            if req["course_id"] in transfers
            else "in_progress"
            if req["course_id"] in active
            else "blocked"
            if missing
            else "eligible"
        )
        course = {
            "id": req["course_id"],
            "code": req["code"],
            "title": req["title"],
            "credits": req["credits"],
            "description": req["course_description"],
            "level": req["level"],
            "availabilityLabel": "See actual term sections",
            "instructorNames": [],
            "meetingPattern": None,
            "resources": [],
            "relatedVideos": [],
            "prerequisites": [
                {"courseCode": p["required_code"], "minimumGrade": p["minimum_grade"]}
                for p in dependencies
            ],
        }
        plan.append(
            {
                "course": course,
                "category": req["category"],
                "recommendedTerm": req["recommended_term"] or 1,
                "status": state,
                "satisfiedPrerequisiteCodes": satisfied,
                "missingPrerequisiteCodes": missing,
            }
        )
    completed = sum(r["credits"] for r in passed.values())
    transferred = sum(r["credits"] for key, r in transfers.items() if key not in passed)
    credits = [
        {
            "id": r["id"],
            "sourceType": "transfer",
            "sourceCode": r["code"],
            "title": r["title"],
            "gradeOrScore": r["grade"],
            "credits": r["credits"],
            "institutionName": r["institution"],
            "sourceDocumentId": None,
            "evaluationStatus": r["status"],
        }
        for r in record["transfers"]
    ]
    return {
        "selectedProgram": program,
        "availablePrograms": [
            {
                "id": p["id"],
                "code": p["id"],
                "name": p["name"],
                "degree": p["id"].split("-")[0].upper(),
                "totalCredits": p["degree_credits"],
                "description": p["department"],
            }
            for p in record["programs"]
        ],
        "transcriptCredits": credits,
        "exemptionRecommendations": [],
        "plan": plan,
        "progress": {
            "completedCredits": completed,
            "exemptedCredits": transferred,
            "requiredCredits": student["degree_credits"],
            "percent": round(min(100, (completed + transferred) / student["degree_credits"] * 100)),
        },
        "attempts": attempts,
        "currentLoads": record["loads"],
        "unresolvedRequirements": [r for r in record["requirements"] if not r["course_id"]],
        "limitations": record["limitations"],
        "catalogVersion": "university-v3",
        "generatedAt": record["snapshotAt"],
    }
