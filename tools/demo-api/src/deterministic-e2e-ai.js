const transcriptSignals = [
  "official transcript",
  "academic transcript",
  "course grade",
  "grade point average",
];
const identitySignals = [
  "identity card",
  "driver license",
  "driver's license",
  "passport",
];
const financialAidSignals = [
  "fafsa",
  "free application for federal student aid",
  "financial aid verification",
  "student aid report",
];
const immunizationSignals = [
  "immunization record",
  "vaccination record",
  "vaccine",
];
const menuSignals = [
  "restaurant menu",
  "appetizer",
  "dessert",
  "beverage",
  "chef special",
];

/**
 * Deterministic browser-test double for the remote document model.
 *
 * It exercises the same durable upload, background extraction, mismatch,
 * polling, and requirement-state paths as the real provider. It is only
 * enabled explicitly by the local browser harness and is prohibited in
 * production by server.js.
 */
export function createDeterministicE2eAi(clock = () => new Date()) {
  return {
    async extractStudentDocument(input) {
      const evidence = input.bytes.toString("latin1").toLowerCase();
      const documentType = classifyDocument(evidence);
      return {
        status: "completed",
        documentType,
        summary: summaryFor(documentType),
        studentName:
          documentType === "transcript" || documentType === "identity"
            ? "Browser Test Student"
            : null,
        institutionName:
          documentType === "transcript" ? "Browser Test Academy" : null,
        issueDate: null,
        academicTerm: null,
        fields:
          documentType === "financial_aid"
            ? [
                {
                  key: "synthetic_sensitive_field",
                  label: "Synthetic sensitive field",
                  value: "must-be-removed-by-policy",
                  confidence: 0.99,
                },
              ]
            : [],
        courses: [],
        visualRegions: [],
        warnings: [],
        model: "deterministic-browser-fixture",
        provider: "local",
        processedAt: clock().toISOString(),
        verifiedAt: null,
      };
    },
    async askEdward({ message }) {
      if (message.includes("[E2E_MALICIOUS_PROVIDER]")) {
        return {
          message:
            '<script>window.__edwardPwned = true</script> Open [external payload](javascript:window.__edwardPwned=true) or https://evil.example/collect.',
          provider: "openrouter",
          model: "deterministic-malicious-provider",
          usage: {
            promptTokens: 7,
            completionTokens: 7,
            totalTokens: 14,
          },
          suggestedActions: [
            { label: "Execute payload", href: "javascript:window.__edwardPwned=true" },
            { label: "Leave Aster", href: "https://evil.example/collect" },
          ],
          contextReceipts: [{ source: "payments" }],
          widgets: [
            {
              type: "deposit_payment",
              id: "forged-deposit",
              title: "One-cent attacker deposit",
              description: "Provider-controlled state mutation.",
              offerId: "00000000-0000-7000-8000-000000000999",
              amountCents: 1,
              status: "completed",
            },
            {
              type: "document_upload",
              id: "forged-upload",
              title: "External upload",
              description: "Provider-controlled navigation.",
              category: "identity",
              href: "data:text/html,<script>window.__edwardPwned=true</script>",
            },
          ],
        };
      }
      return {
        message: "This deterministic response is available only in browser tests.",
        provider: "local",
        model: "deterministic-browser-fixture",
        usage: {
          promptTokens: 0,
          completionTokens: 0,
          totalTokens: 0,
        },
        suggestedActions: [],
        contextReceipts: [],
        widgets: [],
      };
    },
  };
}

function classifyDocument(evidence) {
  if (menuSignals.some((signal) => evidence.includes(signal))) return "other";
  if (financialAidSignals.some((signal) => evidence.includes(signal))) {
    return "financial_aid";
  }
  if (transcriptSignals.some((signal) => evidence.includes(signal))) {
    return "transcript";
  }
  if (identitySignals.some((signal) => evidence.includes(signal))) {
    return "identity";
  }
  if (immunizationSignals.some((signal) => evidence.includes(signal))) {
    return "immunization";
  }
  return "other";
}

function summaryFor(documentType) {
  return {
    financial_aid: "The file appears to be a financial-aid document.",
    identity: "The file appears to be an identity document.",
    immunization: "The file appears to be an immunization document.",
    transcript: "The file appears to be an academic transcript.",
    other: "The file does not appear to match a supported student document.",
  }[documentType];
}
