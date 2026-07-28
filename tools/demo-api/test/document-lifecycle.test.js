import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  expireStaleDocumentExtractions,
  queueDocumentExtraction,
  reserveDocumentUpload,
} from "../src/domain.js";
import { createRequirements, createSeedState, ids } from "../src/seed.js";

const startedAt = new Date("2026-07-28T10:00:00.000Z");

function reservationInput(id, uploadBundleId) {
  return {
    id,
    fileName: `${id}.pdf`,
    mimeType: "application/pdf",
    sizeBytes: 128,
    category: "transcript",
    requirementId: ids.transcriptRequirement,
    uploadBundleId,
    sha256: "a".repeat(64),
    storageKey: `${id}.pdf`,
  };
}

describe("document processing lifecycle", () => {
  it("locks competing uploads, permits the current bundle, and expires stale work", () => {
    const draft = createSeedState();
    draft.requirements = createRequirements(startedAt.toISOString());
    const firstDocumentId = "00000000-0000-7000-8000-000000000901";
    const firstBundleId = "00000000-0000-7000-8000-000000000902";
    const competingBundleId = "00000000-0000-7000-8000-000000000903";

    reserveDocumentUpload(
      draft,
      reservationInput(firstDocumentId, firstBundleId),
      startedAt,
    );
    const processing = queueDocumentExtraction(
      draft,
      firstDocumentId,
      startedAt,
    );

    assert.equal(processing.extraction.status, "processing");
    assert.equal(
      processing.extraction.processingDeadlineAt,
      "2026-07-28T10:01:30.000Z",
    );
    assert.equal("uploadBundleId" in processing, false);

    assert.throws(
      () =>
        reserveDocumentUpload(
          draft,
          reservationInput(
            "00000000-0000-7000-8000-000000000904",
            competingBundleId,
          ),
          startedAt,
        ),
      (error) => error?.code === "DOCUMENT_EXTRACTION_IN_PROGRESS",
    );

    const sameBundle = reserveDocumentUpload(
      draft,
      reservationInput(
        "00000000-0000-7000-8000-000000000905",
        firstBundleId,
      ),
      startedAt,
    );
    assert.equal(sameBundle.status, "placeholder");

    const expired = expireStaleDocumentExtractions(
      draft,
      new Date("2026-07-28T10:01:31.000Z"),
    );
    assert.equal(expired, 1);
    const failed = draft.documents.find(
      (document) => document.id === firstDocumentId,
    );
    assert.equal(failed.extraction.status, "failed");
    assert.equal(failed.extraction.failureCode, "timeout");
    assert.equal(failed.extraction.retryable, true);

    const replacement = reserveDocumentUpload(
      draft,
      reservationInput(
        "00000000-0000-7000-8000-000000000906",
        competingBundleId,
      ),
      new Date("2026-07-28T10:01:31.000Z"),
    );
    assert.equal(replacement.status, "placeholder");
  });
});
