/**
 * Document lifecycle coverage: missing → uploaded → under review → returned,
 * plus claims that disagree with the record and follow-up references.
 *
 * Persona truths:
 *  - new_admit                     nothing uploaded; identity, transcript, immunization open
 *  - transcript_under_review       transcript submitted, document under review;
 *                                  identity + immunization still the student's move
 *  - document_needs_resubmission   transcript upload returned (extraction failed);
 *                                  requirement rejected — student must act again
 */

export const DOCUMENT_CASES = [
  {
    id: "doc-001",
    category: "documents",
    capability: "missing_documents",
    persona: "new_admit",
    question: "Which documents do I still owe you?",
    expect: {
      requestTypes: ["missing_documents", "document_status"],
      requiredTools: ["getOnboardingChecklist", "getDocumentStatuses"],
    },
    checks: [
      { kind: "mentions", any: ["identity"] },
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["immuniz", "immunis"] },
    ],
    expectedBehavior:
      "All three: identity document, official transcript, immunization record. Complete list, actionable.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-002",
    category: "documents",
    capability: "missing_documents",
    persona: "transcript_under_review",
    question: "What documents are still missing from my file?",
    expect: {
      requestTypes: ["missing_documents"],
      requiredTools: ["getOnboardingChecklist", "getDocumentStatuses"],
    },
    checks: [
      { kind: "mentions", any: ["identity"] },
      { kind: "mentions", any: ["immuniz", "immunis"] },
      {
        kind: "not_mentions_pattern",
        pattern: "transcript[^.]{0,50}(?:missing|not (?:been )?(?:submitted|uploaded|received)|still need)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Identity and immunization are missing; the transcript is submitted and under review — it must not be listed as missing.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-003",
    category: "documents",
    capability: "document_status",
    persona: "transcript_under_review",
    question: "Did my transcript go through?",
    expect: {
      requestTypes: ["document_status"],
      requiredTools: ["getOnboardingChecklist", "getDocumentStatuses"],
    },
    checks: [
      { kind: "mentions", any: ["under review", "review", "received", "uploaded"] },
    ],
    expectedBehavior:
      "Yes — received and under review; no student action needed on it right now.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-004",
    category: "documents",
    capability: "document_status",
    persona: "document_needs_resubmission",
    question: "Why was my transcript rejected?",
    expect: { requestTypes: ["document_status", "missing_documents"] },
    checks: [
      { kind: "mentions", any: ["resubmit", "again", "returned", "rejected", "new copy", "re-upload", "reupload"] },
    ],
    expectedBehavior:
      "The upload was returned (it could not be processed) and a new copy is needed; explain grounded in the record, no invented rejection reasons beyond what it shows.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-005",
    category: "documents",
    capability: "document_status",
    persona: "new_admit",
    question: "Has my immunization record been received?",
    expect: { requestTypes: ["document_status", "missing_documents"] },
    checks: [
      { kind: "mentions", any: ["not", "no ", "haven't", "hasn't", "has not", "missing", "still need"] },
    ],
    expectedBehavior:
      "No — nothing is on file; it is still on the student to submit, due about 25 days out.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-006",
    category: "documents",
    capability: "document_status",
    persona: "transcript_under_review",
    question: "How long will the transcript review take?",
    expect: { requestTypes: ["document_status", "general_question", "policy_lookup"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:within|takes?|took|about|typically|usually)\\s+\\d+\\s*(?:hour|day|week|business)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No review SLA exists in the record — the honest answer says no timeline is published and routes to support, without inventing '3–5 business days'.",
    judgeFacts: ["documents", "institutional_gaps"],
  },
  {
    id: "doc-007",
    category: "documents",
    capability: "document_status",
    persona: "document_needs_resubmission",
    question: "Do I need to do anything about my documents?",
    expect: { requestTypes: ["document_status", "missing_documents", "next_action"] },
    checks: [
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["resubmit", "again", "returned", "upload", "attention"] },
    ],
    expectedBehavior:
      "Yes — resubmit the transcript (it came back), and the identity document and immunization record are still open.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-008",
    category: "documents",
    capability: "conflict_with_record",
    persona: "new_admit",
    question: "I mailed my transcript last week, so that one's handled, right?",
    tags: ["conflict"],
    expect: { requestTypes: ["document_status", "missing_documents"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|right|correct)[^.]{0,40}(?:handled|all set|received|done)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
      { kind: "mentions", any: ["not", "no ", "doesn't show", "does not show", "haven't received", "nothing on file", "no record"] },
    ],
    expectedBehavior:
      "Politely distinguish: the student says it was mailed, but the record shows nothing received; suggest checking back or uploading directly. Never confirm the claim.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-009",
    category: "documents",
    capability: "conflict_with_record",
    persona: "transcript_under_review",
    question: "My transcript was already approved, right? Someone told me it cleared.",
    tags: ["conflict"],
    expect: { requestTypes: ["document_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|correct)[^.]{0,32}(?:approved|cleared|accepted)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
      { kind: "mentions", any: ["under review", "review", "not yet", "hasn't", "has not"] },
    ],
    expectedBehavior:
      "The record says under review, not approved — state what the system shows versus what the student heard.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-010",
    category: "documents",
    capability: "document_status",
    persona: "transcript_under_review",
    question: "What about my immunization record?",
    history: [
      { role: "user", content: "Did my transcript go through?" },
      {
        role: "assistant",
        content: "Yes — your transcript was received and is under review.",
      },
    ],
    tags: ["follow_up"],
    expect: { requestTypes: ["document_status", "missing_documents"] },
    checks: [
      { kind: "mentions", any: ["not", "haven't", "has not", "missing", "still need", "no immunization"] },
    ],
    expectedBehavior:
      "Switch subject cleanly to the immunization record: it is not on file and still the student's move — without re-answering the transcript question.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-011",
    category: "documents",
    capability: "document_status",
    persona: "new_admit",
    question: "What kinds of documents will you need from me overall?",
    expect: { requestTypes: ["missing_documents", "document_status", "remaining_steps"] },
    checks: [
      { kind: "mentions", any: ["identity"] },
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["immuniz", "immunis"] },
    ],
    expectedBehavior:
      "The three document requirements on the checklist; a strong answer also mentions the aid-side documents (verification worksheet).",
    judgeFacts: ["documents", "aid"],
  },
  {
    id: "doc-012",
    category: "documents",
    capability: "document_status",
    persona: "document_needs_resubmission",
    question: "Is my transcript under review?",
    expect: { requestTypes: ["document_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|currently)[^.]{0,24}under review",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
      { kind: "mentions", any: ["resubmit", "returned", "again", "no longer", "was returned", "sent back", "attention"] },
    ],
    expectedBehavior:
      "No — it was under review but came back needing resubmission. The distinction between 'under review' and 'returned' is the whole answer.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-013",
    category: "documents",
    capability: "document_status",
    persona: "nearly_complete",
    question: "Are all my documents in?",
    expect: { requestTypes: ["document_status", "missing_documents", "onboarding_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:still (?:need|missing|waiting on))[^.]{0,32}(?:identity|transcript|immuni)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Yes — identity, transcript, and immunization are all completed; nothing document-shaped is outstanding on the enrollment checklist.",
    judgeFacts: ["documents", "checklist"],
  },
  {
    id: "doc-014",
    category: "documents",
    capability: "missing_documents",
    persona: "fafsa_missing",
    question: "Do you have all my paperwork, including for financial aid?",
    tags: ["cross_domain"],
    expect: { requestTypes: ["missing_documents", "document_status", "aid_missing_documents"] },
    checks: [{ kind: "mentions", any: ["fafsa", "identity", "transcript"] }],
    expectedBehavior:
      "No: the three enrollment documents are open, and on the aid side no FAFSA is on file at all — a complete answer covers both.",
    judgeFacts: ["documents", "aid"],
  },
  {
    id: "doc-015",
    category: "documents",
    capability: "document_status",
    persona: "transcript_under_review",
    question: "Should I send my transcript again just to be safe?",
    expect: { requestTypes: ["document_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|please)[^.]{0,24}(?:send|upload|submit) (?:it|your transcript) again",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "No — it is already received and under review; resubmitting is unnecessary unless it is returned.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-016",
    category: "documents",
    capability: "document_status",
    persona: "new_admit",
    question: "Where do I upload my identity document?",
    expect: { requestTypes: ["document_status", "missing_documents", "general_question", "next_action"] },
    checks: [{ kind: "mentions", any: ["upload", "documents", "enrollment", "checklist", "requirement"] }],
    expectedBehavior:
      "Point at the identity-document requirement/Documents page as the upload path; short, navigational, no invented file rules.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-017",
    category: "documents",
    capability: "document_status",
    persona: "document_needs_resubmission",
    question: "This is so frustrating. I already uploaded my transcript once. Why do I have to do it again?",
    tags: ["conflict"],
    expect: { requestTypes: ["document_status", "missing_documents", "general_question"] },
    checks: [
      { kind: "mentions", any: ["returned", "rejected", "could not be processed", "couldn't be processed", "resubmit", "problem", "issue", "failed"] },
    ],
    expectedBehavior:
      "Acknowledge the earlier upload (true), explain it was returned because it could not be processed, and give the single next step. Tone matters; the record still wins.",
    judgeFacts: ["documents"],
  },
  {
    id: "doc-018",
    category: "documents",
    capability: "document_status",
    persona: "new_admit",
    question: "Does my FERPA form count as one of my required documents?",
    expect: { requestTypes: ["document_status", "missing_documents", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "ferpa[^.]{0,40}(?:is required|is on your checklist|due)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No FERPA requirement exists on this checklist — say what actually is required instead of inventing one.",
    judgeFacts: ["documents"],
  },
];
