import { S, f, soft, forbid, one, NONE_PATTERN, ASKS_WHICH, NO_UUID } from "./common.mjs";

const KWAME = S("kwame");
const BRUNO = S("bruno");
const LUCIA = S("lucia");
const PETRA = S("petra");
const ADRIA = S("adria");
const CAMILA = S("camila");

export const DOCUMENTS_CASES = [
  one("rg-doc-001", "documents", "student", "kwame",
    "what have i uploaded so far",
    "Lists the seven uploaded documents (or their count) with statuses; the immunization record is rejected.",
    {
      requiredTools: ["getDocumentStatuses"],
      facts: [
        f("immunization rejected", "immuni[sz]ation[^.]{0,80}reject|reject[^.]{0,80}immuni[sz]ation"),
        soft("count", `{{num:${KWAME}.documents.total}}`),
        soft("transcript accepted", "transcript[^.]{0,60}accept"),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-doc-002", "documents", "student", "bruno",
    "did my transcript go through ok",
    "Transcript is accepted.",
    {
      requiredTools: ["getDocumentStatuses"],
      facts: [f("accepted", "accept|approved|went through|received and (?:reviewed|processed)|complete")],
      forbidden: [forbid("says pending or rejected", "still (?:under|in) review|rejected|not (?:yet )?(?:been )?(?:reviewed|accepted)|pending review")],
    }),
  one("rg-doc-003", "documents", "student", "kwame",
    "which of my docs got rejected and why",
    "The immunization record was rejected; the recorded reason is the staff review note ('Prior staff review' / 'earlier submission needs changes'). Must not invent a specific medical reason.",
    {
      requiredTools: ["getDocumentStatuses"],
      facts: [
        f("immunization", "immuni[sz]ation"),
        f("reason as recorded or honest that no detail is recorded", `{{gt:${KWAME}.documents.rejected.0.reasonLabel}}|needs changes|earlier submission|prior (?:staff )?review|no (?:specific |detailed )?reason (?:was |is )?(?:recorded|given|provided)|reason (?:isn'?t|wasn'?t|is not|was not) (?:recorded|given|provided|available)|contact enrollment services|resubmit`),
      ],
      forbidden: [forbid("invents a reason", "(?:expired|illegible|blurry|missing (?:a )?signature|wrong (?:form|format)|out of date|incomplete (?:dose|vaccin))")],
    }),
  one("rg-doc-004", "documents", "student", "lucia",
    "is my transcript still being reviewed?",
    "Yes — the transcript document is under review.",
    {
      requiredTools: ["getDocumentStatuses"],
      facts: [f("under review", "under review|being reviewed|in review|still (?:in|under) review|\\byes\\b")],
      forbidden: [forbid("says accepted", "(?:has been|was|is) accepted|approved")],
    }),
  one("rg-doc-005", "documents", "student", "petra",
    "did u get my tax return transcript",
    "Yes: tax_return_transcript-oakenshaw.pdf is on file and under review (verification worksheet uploaded, still pending).",
    {
      requiredTools: ["getDocumentStatuses"],
      facts: [
        f("received", "received|on file|uploaded|got it|\\byes\\b|have it"),
        f("under review", "under review|being reviewed|pending|in review"),
      ],
      forbidden: [forbid("denies receipt", "(?:haven'?t|have not|did not|didn'?t) (?:received|got|seen)|no (?:tax return|such) (?:document|file)")],
    }),
  one("rg-doc-006", "documents", "staff", "marcus",
    "which documents from Adria Kettleby are still under review",
    "Two: the transcript and the I-20 support document.",
    {
      resolvedStudentId: `gt:${ADRIA}.id`,
      requiredTools: ["getStudentDocuments"],
      facts: [f("transcript", "transcript"), f("i20 support", "i-?20|support")],
      forbidden: [forbid("says none pending", "no documents (?:are )?(?:under|in) review|nothing (?:under|in) review")],
    }),
  one("rg-doc-007", "documents", "staff", "registrar",
    "Has Omar Vellacourt's transcript been accepted?",
    "Four students share this name; must ask which one instead of picking.",
    {
      resolvedStudentId: null,
      facts: [ASKS_WHICH],
      forbidden: [forbid("answers as if resolved", "(?:his|the) transcript (?:has been|was|is) (?:accepted|rejected|under review)")],
    }),
  one("rg-doc-008", "documents", "staff", "zelda",
    "what did Camila Calderwood submit and where does each one stand",
    "Four documents: transcript, photo ID and residency affidavit accepted; immunization record rejected.",
    {
      resolvedStudentId: `gt:${CAMILA}.id`,
      requiredTools: ["getStudentDocuments"],
      facts: [
        f("immunization rejected", "immuni[sz]ation[^.]{0,80}reject|reject[^.]{0,80}immuni[sz]ation"),
        f("transcript accepted", "transcript[^.]{0,80}accept|accept[^.]{0,80}transcript"),
        soft("residency", "residency"),
      ],
    }),
];
