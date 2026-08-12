/**
 * One authoritative answer to "what is the state of this student's document?".
 *
 * The staleness bug this module exists to kill came from having two records of
 * the same fact with two different owners: the document row, written by the
 * upload endpoint, and the onboarding requirement, written only as a side
 * effect of the AI extraction pipeline finishing. Between those two events --
 * and forever, if extraction failed or no provider was configured -- the
 * checklist kept asking for a document the student had already provided, and
 * Edward answered from the checklist.
 *
 * The fix is to stop treating the requirement as an independently-owned fact.
 * The document record is the source of truth; the requirement is a projection
 * of it, recomputed by `reconcileDocumentRequirements` after every mutation.
 * The projection is pure and idempotent, so running it more often is only ever
 * a waste of microseconds, never a correctness risk.
 */

import {
  documentContentUsable,
  documentSubmissionState,
  documentSubmissionStates,
  requirementStatusBySubmissionState,
  strongestSubmissionState,
} from "@vv/student-assistant-core";

export {
  documentSubmissionState,
  strongestSubmissionState,
} from "@vv/student-assistant-core";

/**
 * The student-facing lifecycle, defined once in the shared core so the write
 * path here and the assistant's read path cannot disagree about what
 * "submitted" means.
 */
export const DOCUMENT_SUBMISSION_STATES = documentSubmissionStates;

/** Review outcomes an admissions/registrar reviewer can record. */
export const DOCUMENT_REVIEW_DECISIONS = Object.freeze([
  "accepted",
  "rejected",
  "needs_resubmission",
  "waived",
  "under_review",
]);

/**
 * Which onboarding requirement each document category satisfies. A category
 * absent here (consent signatures, residency proofs, loose "other" uploads)
 * satisfies no checklist item and is left alone.
 */
export const REQUIREMENT_CODE_BY_DOCUMENT_CATEGORY = Object.freeze({
  identity: "identity_document",
  transcript: "official_transcript",
  financial_aid: "financial_aid_verification",
  health: "immunization_record",
});

const PROGRESS_BY_STATE = Object.freeze({
  UPLOADED: 60,
  UNDER_REVIEW: 80,
  ACCEPTED: 100,
  REJECTED: 40,
  NEEDS_RESUBMISSION: 40,
  WAIVED: 100,
});

/** Documents that count towards one requirement, by id or by category. */
export function documentsForRequirement(state, requirement) {
  const category = documentCategoryForRequirementCode(requirement.code);
  return (state.documents ?? []).filter(
    (document) =>
      document.requirementId === requirement.id ||
      (!document.requirementId && category && document.category === category),
  );
}

export function documentCategoryForRequirementCode(code) {
  return (
    Object.entries(REQUIREMENT_CODE_BY_DOCUMENT_CATEGORY).find(
      ([, requirementCode]) => requirementCode === code,
    )?.[0] ?? null
  );
}

/**
 * Every document-backed requirement with its authoritative state. This is the
 * shape both the checklist projection and the assistant read from, so the two
 * cannot drift apart again.
 */
export function documentRequirementStates(state) {
  if (!Array.isArray(state?.requirements)) return [];
  return state.requirements
    .filter((requirement) => requirement.submissionType === "document")
    .map((requirement) => {
      const documents = documentsForRequirement(state, requirement);
      const submissionState = strongestSubmissionState(documents);
      const leading = documents.find(
        (document) => documentSubmissionState(document) === submissionState,
      );
      return {
        requirementId: requirement.id,
        requirementCode: requirement.code,
        title: requirement.title,
        category: documentCategoryForRequirementCode(requirement.code),
        submissionState,
        requirementStatus: requirement.status,
        dueAt: requirement.dueAt ?? null,
        documentId: leading?.id ?? null,
        fileName: leading?.fileName ?? null,
        submittedAt: leading?.createdAt ?? null,
        reviewedAt: leading?.review?.decidedAt ?? null,
        reviewerNote: leading?.review?.note ?? null,
        responsibleOffice: requirement.responsibleOffice ?? null,
      };
    });
}

/**
 * Project document state onto the requirements and the financial-aid document
 * list. Returns the number of fields changed so a caller can skip a write.
 *
 * Deliberately a no-op when the state does not look like a student portal
 * state: the same store class also holds tenant-level records.
 */
export function reconcileDocumentRequirements(state) {
  if (!Array.isArray(state?.requirements) || !Array.isArray(state?.documents)) {
    return 0;
  }
  let changes = 0;
  for (const requirement of state.requirements) {
    if (requirement.submissionType !== "document") continue;
    // A file the parser could not read, or read as a different kind of
    // document, is stored but does not satisfy anything. Excluding it here is
    // what keeps a restaurant menu filed as a transcript from advancing the
    // transcript requirement. The document itself is still reported to the
    // student as needing resubmission -- it is the checklist that must not move.
    const documents = documentsForRequirement(state, requirement).filter(
      documentContentUsable,
    );
    const submissionState = strongestSubmissionState(documents);

    if (submissionState === "NOT_SUBMITTED") {
      // Nothing usable is on file. If an earlier run of this projection had
      // advanced the requirement -- an upload that was later found unreadable,
      // say -- put it back. A projection that could only ever move forward
      // would leave the checklist claiming a submission that no longer exists.
      changes += restoreProjectedRequirement(requirement);
      continue;
    }

    const status = requirementStatusBySubmissionState[submissionState];
    const progressPercent = PROGRESS_BY_STATE[submissionState];
    if (status && requirement.status !== status) {
      rememberPreProjectionStatus(requirement);
      requirement.status = status;
      changes += 1;
    }
    if (
      typeof progressPercent === "number" &&
      requirement.progressPercent !== progressPercent
    ) {
      rememberPreProjectionStatus(requirement);
      requirement.progressPercent = progressPercent;
      changes += 1;
    }
    if (requirement.code === "financial_aid_verification") {
      changes += reconcileFinancialAidDocument(state, submissionState);
    }
  }
  return changes;
}

/**
 * The requirement's own status, before any document projection touched it.
 * Recorded once so the projection is reversible; a requirement blocked by a
 * dependency must return to "blocked", not to a guess.
 */
function rememberPreProjectionStatus(requirement) {
  requirement.documentProjection ??= {
    priorStatus: requirement.status,
    priorProgressPercent: requirement.progressPercent,
  };
}

function restoreProjectedRequirement(requirement) {
  const prior = requirement.documentProjection;
  if (!prior) return 0;
  requirement.status = prior.priorStatus;
  requirement.progressPercent = prior.priorProgressPercent;
  delete requirement.documentProjection;
  return 1;
}

/**
 * The financial-aid worksheet appears twice: as an onboarding requirement and
 * as a row in the aid document list the financials page renders. Both are
 * projections of the same upload.
 */
function reconcileFinancialAidDocument(state, submissionState) {
  const target = (state.financials?.requiredDocuments ?? []).find(
    (item) => item.code === "verification_worksheet",
  );
  if (!target) return 0;
  // "Rejected" rather than "action required": both need the student to act, but
  // only one of them means the worksheet was received and sent back. Collapsing
  // them made Edward say exactly the same sentence before an upload and after a
  // reviewer returned it.
  const status = {
    UPLOADED: "submitted",
    UNDER_REVIEW: "under_review",
    ACCEPTED: "verified",
    REJECTED: "rejected",
    NEEDS_RESUBMISSION: "rejected",
    WAIVED: "waived",
  }[submissionState];
  if (!status || target.status === status) return 0;
  target.status = status;
  target.version = (target.version ?? 0) + 1;
  return 1;
}

/**
 * Record a reviewer's decision on a stored document. Kept here rather than in
 * the upload code so that every write which can change a document's state goes
 * through a place that knows about the projection.
 */
export function applyDocumentReviewDecision(document, decision, now, note = null) {
  const status = {
    accepted: "accepted",
    rejected: "rejected",
    needs_resubmission: "needs_resubmission",
    waived: "waived",
    under_review: "under_review",
  }[decision];
  if (!status) throw new Error(`Unknown document review decision: ${decision}`);
  document.status = status;
  document.review = {
    decision,
    decidedAt: now.toISOString(),
    note: note ?? null,
    synthetic: true,
  };
  return document;
}
