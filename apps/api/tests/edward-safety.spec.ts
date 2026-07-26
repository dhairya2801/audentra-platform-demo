import { describe, expect, it } from "vitest";
import {
  normalizeEdwardActionHref,
  normalizeEdwardPageContext,
  normalizeEdwardResponse,
} from "../src/agentic/edward-safety";

describe("Edward request and action safety", () => {
  it("reduces page context to known portal routes", () => {
    expect(normalizeEdwardPageContext(" /financials/ ")).toBe("/financials");
    expect(
      normalizeEdwardPageContext(
        "/enrollment/requirements/transcript-upload",
      ),
    ).toBe("/enrollment/requirements");
    expect(normalizeEdwardPageContext("https://unsafe.example/prompt")).toBe(
      "/dashboard",
    );
    expect(normalizeEdwardPageContext("javascript:ignore")).toBe(
      "/dashboard",
    );
  });

  it("keeps only internal, allowlisted navigation destinations", () => {
    expect(normalizeEdwardActionHref("/documents")).toBe("/documents");
    expect(normalizeEdwardActionHref("/enrollment/requirements/transcript-upload"))
      .toBe("/enrollment/requirements/transcript-upload");
    expect(normalizeEdwardActionHref("//unsafe.example")).toBeNull();
    expect(normalizeEdwardActionHref("https://unsafe.example")).toBeNull();
    expect(normalizeEdwardActionHref("javascript:alert(1)")).toBeNull();
  });

  it("filters unsafe links while preserving the separate deposit action", () => {
    const response = normalizeEdwardResponse({
      message: "Your next step is ready.",
      provider: "guided",
      model: null,
      usage: null,
      suggestedActions: [
        { label: "Open documents", href: "/documents" },
        { label: "Unsafe", href: "javascript:alert(1)" },
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
        {
          type: "appointment",
          id: "appointment",
          title: "Meet an advisor",
          description: "Choose a time.",
          appointmentType: "enrollment_support",
          href: "/appointments",
        },
      ],
    });

    expect(response.suggestedActions).toEqual([
      { label: "Open documents", href: "/documents" },
    ]);
    expect(response.widgets).toEqual([
      expect.objectContaining({ type: "deposit_payment", id: "deposit" }),
      expect.objectContaining({ type: "appointment", href: "/appointments" }),
    ]);
  });
});
