import type { RequirementStatus, StudentDocument } from "@vv/contracts";

/**
 * The document lifecycle, in one place.
 *
 * A student's document exists as three records: the uploaded file, the
 * onboarding requirement it satisfies, and whatever the assistant says about
 * it. When each of those owned its own opinion of "has this been submitted?",
 * they drifted, and Edward told students to upload files they had already
 * uploaded. The mapping below is the single definition all three now share --
 * the preview's write path imports it, and the assistant's read path derives
 * from it -- so a future divergence has to be a deliberate edit here rather
 * than an oversight somewhere else.
 */
export const documentSubmissionStates = [
  "NOT_SUBMITTED",
  "UPLOADED",
  "UNDER_REVIEW",
  "ACCEPTED",
  "REJECTED",
  "NEEDS_RESUBMISSION",
  "WAIVED",
] as const;

export type DocumentSubmissionState = (typeof documentSubmissionStates)[number];

/**
 * Precedence when one requirement has several documents. A newer good
 * submission outranks an older bad one, which is what makes resubmission work:
 * replacing a rejected transcript moves the requirement back to under review
 * instead of leaving it pinned to the rejection.
 */
const submissionStatePrecedence: readonly DocumentSubmissionState[] = [
  "ACCEPTED",
  "WAIVED",
  "UNDER_REVIEW",
  "UPLOADED",
  "NEEDS_RESUBMISSION",
  "REJECTED",
  "NOT_SUBMITTED",
];

export const requirementStatusBySubmissionState: Readonly<
  Record<Exclude<DocumentSubmissionState, "NOT_SUBMITTED">, RequirementStatus>
> = {
  UPLOADED: "submitted",
  UNDER_REVIEW: "under_review",
  ACCEPTED: "completed",
  REJECTED: "rejected",
  NEEDS_RESUBMISSION: "rejected",
  WAIVED: "waived",
};

/** Which document category each extracted document type corresponds to. */
const categoryByExtractedType: Readonly<Record<string, string>> = {
  transcript: "transcript",
  identity: "identity",
  financial_aid: "financial_aid",
  ferpa: "consent",
  immunization: "health",
  residency: "residency",
  // "other" is the classifier saying it did not recognise the document. Filed
  // against a specific requirement, that is a mismatch like any other.
  other: "other",
};

/**
 * Whether a stored file can actually satisfy the requirement it was filed
 * against.
 *
 * Two things make it unusable, and neither is the student having failed to
 * upload: the parser could not read the file at all, or it read it and found a
 * different kind of document -- a menu filed as a transcript. Both leave the
 * requirement unsatisfied, but "you did not upload it" is the wrong thing to
 * tell someone who did. They are reported as needing resubmission instead, with
 * the reason.
 */
export function documentContentUsable(
  document:
    | Pick<StudentDocument, "status" | "category" | "extraction" | "review">
    | null
    | undefined,
): boolean {
  if (!document) return false;
  // A recorded review means a person looked at the file. Their decision
  // outranks the classifier's guess about what kind of document it is.
  if (document.review) return true;
  const extraction = document.extraction;
  if (!extraction) return true;
  if (extraction.status === "failed") return false;
  if (extraction.status !== "completed") return true;
  const impliedCategory = categoryByExtractedType[extraction.documentType ?? ""];
  return !impliedCategory || impliedCategory === document.category;
}

/**
 * Map one stored document onto the lifecycle.
 *
 * `placeholder` is the reserved row created before the original file lands, so
 * it is not yet a submission. `processing` is: the file is stored and the only
 * thing still running is the extractor, which the student neither controls nor
 * needs to wait for.
 */
export function documentSubmissionState(
  document:
    | Pick<StudentDocument, "status" | "category" | "extraction" | "review">
    | Pick<StudentDocument, "status">
    | null
    | undefined,
): DocumentSubmissionState {
  // A reviewer's decision is final and outranks anything the parser thinks --
  // including a decision to keep the document under review, which a person can
  // only have made by looking at it.
  const decided =
    (document && "review" in document && Boolean(document.review)) ||
    document?.status === "accepted" ||
    document?.status === "waived" ||
    document?.status === "rejected" ||
    document?.status === "needs_resubmission";
  if (
    !decided &&
    document &&
    "category" in document &&
    !documentContentUsable(document)
  ) {
    return "NEEDS_RESUBMISSION";
  }
  switch (document?.status) {
    case "accepted":
      return "ACCEPTED";
    case "waived":
      return "WAIVED";
    case "rejected":
      return "REJECTED";
    case "needs_resubmission":
      return "NEEDS_RESUBMISSION";
    case "under_review":
    case "needs_review":
      return "UNDER_REVIEW";
    case "processing":
    case "uploaded":
      return "UPLOADED";
    default:
      return "NOT_SUBMITTED";
  }
}

export function strongestSubmissionState(
  documents: readonly Pick<StudentDocument, "status">[],
): DocumentSubmissionState {
  const states = new Set(documents.map(documentSubmissionState));
  return (
    submissionStatePrecedence.find((candidate) => states.has(candidate)) ??
    "NOT_SUBMITTED"
  );
}

/** True when nothing further is required of the student right now. */
export function isSettledSubmissionState(
  state: DocumentSubmissionState,
): boolean {
  return state === "ACCEPTED" || state === "WAIVED";
}

/** True when the university, not the student, holds the next move. */
export function isAwaitingReviewSubmissionState(
  state: DocumentSubmissionState,
): boolean {
  return state === "UPLOADED" || state === "UNDER_REVIEW";
}

/** True when the student has something to do about it. */
export function isActionRequiredSubmissionState(
  state: DocumentSubmissionState,
): boolean {
  return (
    state === "NOT_SUBMITTED" ||
    state === "REJECTED" ||
    state === "NEEDS_RESUBMISSION"
  );
}

/**
 * Student-facing wording for each state. Kept as data rather than prompt text
 * so the phrasing is testable and cannot be improvised by a model.
 */
export function submissionStateSentence(
  state: DocumentSubmissionState,
  title: string,
): string {
  switch (state) {
    case "ACCEPTED":
      return `${title}: accepted. Nothing further is needed.`;
    case "WAIVED":
      return `${title}: waived, so you do not need to submit it.`;
    case "UNDER_REVIEW":
      return `${title}: uploaded and currently under review. You do not need to upload it again unless the reviewing office asks for a replacement.`;
    case "UPLOADED":
      return `${title}: uploaded and queued for review. You do not need to upload it again.`;
    case "REJECTED":
      return `${title}: rejected on review, so a corrected copy is needed.`;
    case "NEEDS_RESUBMISSION":
      return `${title}: needs to be resubmitted before it can be accepted.`;
    case "NOT_SUBMITTED":
    default:
      return `${title}: not submitted yet.`;
  }
}

/** Short label for tables and status badges. */
export function submissionStateLabel(state: DocumentSubmissionState): string {
  return {
    NOT_SUBMITTED: "Not submitted",
    UPLOADED: "Uploaded",
    UNDER_REVIEW: "Under review",
    ACCEPTED: "Accepted",
    REJECTED: "Rejected",
    NEEDS_RESUBMISSION: "Needs resubmission",
    WAIVED: "Waived",
  }[state];
}
