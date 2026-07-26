import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  normalizeEdwardActionHref,
  normalizeEdwardPageContext,
  normalizeEdwardResponse,
} from "../src/edward-safety.js";

describe("Edward request and action safety", () => {
  it("canonicalizes page context and rejects unsafe hrefs", () => {
    assert.equal(normalizeEdwardPageContext(" /financials/ "), "/financials");
    assert.equal(
      normalizeEdwardPageContext("/enrollment/requirements/transcript-upload"),
      "/enrollment/requirements",
    );
    assert.equal(
      normalizeEdwardPageContext("https://unsafe.example/prompt"),
      "/dashboard",
    );
    assert.equal(normalizeEdwardActionHref("/documents"), "/documents");
    assert.equal(normalizeEdwardActionHref("//unsafe.example"), null);
    assert.equal(normalizeEdwardActionHref("javascript:alert(1)"), null);
  });

  it("filters unsafe links and leaves deposit widgets unchanged", () => {
    const response = normalizeEdwardResponse({
      message: "Your next step is ready.",
      provider: "guided",
      model: null,
      usage: null,
      suggestedActions: [
        { label: "Open documents", href: "/documents" },
        { label: "External", href: "https://unsafe.example" },
      ],
      contextReceipts: [],
      widgets: [
        {
          type: "deposit_payment",
          id: "deposit",
          title: "Enrollment deposit",
          description: "Use the authorized payment action.",
          offerId: "offer-1",
          amountCents: 50000,
          status: "ready",
        },
        {
          type: "document_upload",
          id: "unsafe-document",
          title: "Unsafe document route",
          description: "Do not render.",
          category: "identity",
          href: "data:text/html,unsafe",
        },
      ],
    });

    assert.deepEqual(response.suggestedActions, [
      { label: "Open documents", href: "/documents" },
    ]);
    assert.deepEqual(response.widgets, [
      {
        type: "deposit_payment",
        id: "deposit",
        title: "Enrollment deposit",
        description: "Use the authorized payment action.",
        offerId: "offer-1",
        amountCents: 50000,
        status: "ready",
      },
    ]);
  });
});
