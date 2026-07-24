const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
const DEFAULT_MODEL = "openai/gpt-4o-mini";

const documentSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    documentType: {
      type: "string",
      enum: [
        "transcript",
        "identity",
        "financial_aid",
        "ferpa",
        "immunization",
        "residency",
        "other",
      ],
    },
    summary: { type: "string" },
    studentName: { type: ["string", "null"] },
    institutionName: { type: ["string", "null"] },
    issueDate: { type: ["string", "null"] },
    academicTerm: { type: ["string", "null"] },
    fields: {
      type: "array",
      maxItems: 24,
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          key: { type: "string" },
          label: { type: "string" },
          value: { type: "string" },
          confidence: { type: "number", minimum: 0, maximum: 1 },
        },
        required: ["key", "label", "value", "confidence"],
      },
    },
    warnings: {
      type: "array",
      maxItems: 12,
      items: { type: "string" },
    },
  },
  required: [
    "documentType",
    "summary",
    "studentName",
    "institutionName",
    "issueDate",
    "academicTerm",
    "fields",
    "warnings",
  ],
};

export class OpenRouterGateway {
  constructor(options = {}) {
    this.apiKey = options.apiKey?.trim() || "";
    this.model = options.model?.trim() || DEFAULT_MODEL;
    this.appUrl = options.appUrl?.trim() || "http://localhost:3000";
    this.appName = options.appName?.trim() || "Aster Student Portal";
    this.fetch = options.fetch ?? globalThis.fetch;
  }

  get configured() {
    return this.apiKey.length > 0;
  }

  async askEdward({ message, pageContext, history, studentContext }) {
    if (!this.configured) {
      return guidedEdwardResponse(message);
    }

    const boundedHistory = Array.isArray(history)
      ? history.slice(-6).map((item) => ({
          role: item.role === "assistant" ? "assistant" : "user",
          content: String(item.content ?? "").slice(0, 1_200),
        }))
      : [];
    const context = {
      preferredName: studentContext.preferredName,
      programName: studentContext.programName,
      termName: studentContext.termName,
      onboardingStatus: studentContext.onboardingStatus,
      enrollmentCompletion: studentContext.enrollmentCompletion,
      nextAction: studentContext.nextAction,
      unreadMessages: studentContext.unreadMessages,
      documentStatuses: studentContext.documentStatuses,
      pageContext: String(pageContext || "unknown").slice(0, 120),
    };

    const payload = await this.#complete({
      model: this.model,
      temperature: 0.2,
      max_tokens: 420,
      messages: [
        {
          role: "system",
          content:
            "You are Edward, Aster University's student portal guide. Answer in plain language using only the provided portal context. Never claim to submit, approve, pay, or change a record. Do not request passwords, full government IDs, bank or card details, medical details, or other secrets. If the student needs an official decision, direct them to the correct office. Keep answers under 140 words and prefer one clear next step.",
        },
        {
          role: "system",
          content: `Current portal context: ${JSON.stringify(context)}`,
        },
        ...boundedHistory,
        {
          role: "user",
          content: String(message).slice(0, 2_000),
        },
      ],
    });

    const content = readMessageContent(payload);
    return {
      message: content.slice(0, 2_500),
      provider: "openrouter",
      model: payload.model ?? this.model,
      usage: normalizeUsage(payload.usage),
      suggestedActions: suggestedActionsFor(message),
    };
  }

  async extractStudentDocument({ fileName, mimeType, bytes }) {
    if (!this.configured) {
      return pendingExtraction(fileName);
    }

    const dataUrl = `data:${mimeType};base64,${bytes.toString("base64")}`;
    const filePart =
      mimeType === "application/pdf"
        ? {
            type: "file",
            file: {
              filename: fileName,
              file_data: dataUrl,
            },
          }
        : {
            type: "image_url",
            image_url: { url: dataUrl },
          };
    const payload = await this.#complete({
      model: this.model,
      temperature: 0,
      max_tokens: 1_200,
      messages: [
        {
          role: "system",
          content:
            "Extract structured facts from the student's uploaded document. Copy only values visible in the document. Never infer or invent missing values. Omit secrets such as full SSNs, taxpayer IDs, passport numbers, account numbers, card details, signatures, and medical diagnoses. Use warnings for unreadable, ambiguous, or sensitive sections. Confidence is 0 to 1. Return JSON matching the provided schema.",
        },
        {
          role: "user",
          content: [
            {
              type: "text",
              text: `Parse ${fileName} into safe student-record metadata. Do not include full government or financial identifiers.`,
            },
            filePart,
          ],
        },
      ],
      ...(mimeType === "application/pdf"
        ? {
            plugins: [
              {
                id: "file-parser",
                pdf: { engine: "cloudflare-ai" },
              },
            ],
          }
        : {}),
      response_format: {
        type: "json_schema",
        json_schema: {
          name: "student_document_extraction",
          strict: true,
          schema: documentSchema,
        },
      },
      provider: {
        require_parameters: true,
      },
    });

    const parsed = JSON.parse(readMessageContent(payload));
    return normalizeExtraction(parsed, {
      model: payload.model ?? this.model,
      processedAt: new Date().toISOString(),
    });
  }

  async #complete(body) {
    const response = await this.fetch(OPENROUTER_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
        "HTTP-Referer": this.appUrl,
        "X-Title": this.appName,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(45_000),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const reason =
        payload?.error?.message ||
        `OpenRouter returned HTTP ${response.status}`;
      throw new Error(reason);
    }
    if (!payload?.choices?.[0]?.message) {
      throw new Error("OpenRouter returned an empty completion");
    }
    return payload;
  }
}

export function createOpenRouterGatewayFromEnv(options = {}) {
  return new OpenRouterGateway({
    apiKey: options.apiKey ?? process.env.OPENROUTER_API_KEY,
    model: options.model ?? process.env.OPENROUTER_MODEL,
    appUrl: options.appUrl ?? process.env.OPENROUTER_APP_URL,
    appName: options.appName ?? process.env.OPENROUTER_APP_NAME,
    fetch: options.fetch,
  });
}

function readMessageContent(payload) {
  const content = payload?.choices?.[0]?.message?.content;
  if (typeof content === "string" && content.trim()) return content.trim();
  if (Array.isArray(content)) {
    const text = content
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("\n")
      .trim();
    if (text) return text;
  }
  throw new Error("OpenRouter returned no readable content");
}

function normalizeUsage(usage) {
  if (!usage || typeof usage !== "object") return null;
  return {
    promptTokens: Number(usage.prompt_tokens ?? 0),
    completionTokens: Number(usage.completion_tokens ?? 0),
    totalTokens: Number(usage.total_tokens ?? 0),
  };
}

function normalizeExtraction(value, metadata) {
  const documentTypes = new Set(documentSchema.properties.documentType.enum);
  const fields = Array.isArray(value?.fields)
    ? value.fields.slice(0, 24).map((field, index) => ({
        key: safeText(field?.key, `field_${index + 1}`, 80),
        label: safeText(field?.label, `Field ${index + 1}`, 120),
        value: redactSensitiveValue(safeText(field?.value, "", 500)),
        confidence: Math.max(0, Math.min(1, Number(field?.confidence ?? 0))),
      }))
    : [];
  return {
    status: "completed",
    documentType: documentTypes.has(value?.documentType)
      ? value.documentType
      : "other",
    summary: safeText(
      value?.summary,
      "The document was parsed and is ready for review.",
      800,
    ),
    studentName: nullableText(value?.studentName, 160),
    institutionName: nullableText(value?.institutionName, 200),
    issueDate: nullableText(value?.issueDate, 80),
    academicTerm: nullableText(value?.academicTerm, 120),
    fields,
    warnings: Array.isArray(value?.warnings)
      ? value.warnings.slice(0, 12).map((warning) => safeText(warning, "", 400))
      : [],
    model: metadata.model,
    provider: "openrouter",
    processedAt: metadata.processedAt,
    verifiedAt: null,
  };
}

function pendingExtraction(fileName) {
  return {
    status: "pending_configuration",
    documentType: inferDocumentType(fileName),
    summary:
      "File stored securely. Add OPENROUTER_API_KEY to run structured extraction.",
    studentName: null,
    institutionName: null,
    issueDate: null,
    academicTerm: null,
    fields: [],
    warnings: [
      "Agentic parsing is waiting for an OpenRouter API key.",
      "No extracted value will update the student profile without review.",
    ],
    model: null,
    provider: "local",
    processedAt: null,
    verifiedAt: null,
  };
}

function inferDocumentType(fileName) {
  const name = String(fileName).toLowerCase();
  if (name.includes("transcript")) return "transcript";
  if (name.includes("fafsa") || name.includes("financial")) return "financial_aid";
  if (name.includes("ferpa") || name.includes("release")) return "ferpa";
  if (name.includes("immun") || name.includes("vaccine")) return "immunization";
  if (name.includes("passport") || name.includes("license")) return "identity";
  if (name.includes("residen")) return "residency";
  return "other";
}

function guidedEdwardResponse(message) {
  const text = String(message).toLowerCase();
  const response = text.match(/document|upload|transcript|fafsa|ferpa/)
    ? "Open Documents to upload a PDF, JPEG, or PNG. Aster stores the original file and prepares structured fields for your review. Nothing extracted is treated as verified until you approve it."
    : text.match(/deadline|due|when/)
      ? "Your dashboard shows the nearest enrollment deadlines. Open Enrollment for the complete checklist and the status of each requirement."
      : text.match(/payment|deposit|pay/)
        ? "Open Payments to review the enrollment deposit. Payment details should only be entered in the secure processor—not in this chat."
        : text.match(/profile|phone|name|contact/)
          ? "You can update changeable contact preferences from Profile. Legal identity changes may require supporting documentation and staff review."
          : text.match(/appointment|advisor|person|human/)
            ? "Open Appointments to schedule enrollment, admissions, or financial-aid support with a staff member."
            : "I can help you find enrollment steps, documents, deadlines, payments, appointments, and profile settings. Ask one specific question and I’ll point you to the right place.";
  return {
    message: response,
    provider: "guided",
    model: null,
    usage: null,
    suggestedActions: suggestedActionsFor(message),
  };
}

function suggestedActionsFor(message) {
  const text = String(message).toLowerCase();
  if (text.match(/document|upload|transcript|fafsa|ferpa/)) {
    return [{ label: "Open documents", href: "/documents" }];
  }
  if (text.match(/payment|deposit|pay/)) {
    return [{ label: "Open payments", href: "/payments" }];
  }
  if (text.match(/appointment|advisor|human/)) {
    return [{ label: "Book an appointment", href: "/appointments" }];
  }
  if (text.match(/profile|phone|name|contact/)) {
    return [{ label: "Open profile", href: "/profile" }];
  }
  return [
    { label: "View enrollment", href: "/enrollment" },
    { label: "Get support", href: "/help" },
  ];
}

function safeText(value, fallback, maximum) {
  if (typeof value !== "string") return fallback;
  const text = value.trim().slice(0, maximum);
  return text || fallback;
}

function nullableText(value, maximum) {
  if (value === null || value === undefined) return null;
  const text = safeText(value, "", maximum);
  return text || null;
}

function redactSensitiveValue(value) {
  if (
    /\b\d{3}-?\d{2}-?\d{4}\b/.test(value) ||
    /\b(?:\d[ -]*?){13,19}\b/.test(value)
  ) {
    return "[sensitive value redacted]";
  }
  return value;
}
