import type {
  AskEdwardInput,
  AskEdwardResponse,
  StudentDocumentExtraction,
} from "@vv/contracts";
import type { AppConfig } from "../config/app-config";

const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";

export const STUDENT_AI_GATEWAY = Symbol("STUDENT_AI_GATEWAY");

export interface EdwardStudentContext {
  preferredName: string;
  programName: string;
  termName: string;
  onboardingStatus: string;
  enrollmentCompletion: number;
  nextAction: unknown;
  unreadMessages: number;
  documentStatuses: Array<{ category: string; status: string }>;
}

export interface StudentAiGateway {
  askEdward(
    input: AskEdwardInput & { studentContext: EdwardStudentContext },
  ): Promise<AskEdwardResponse>;
  extractStudentDocument(input: {
    fileName: string;
    mimeType: "application/pdf" | "image/jpeg" | "image/png";
    bytes: Buffer;
  }): Promise<StudentDocumentExtraction>;
}

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
} as const;

interface CompletionPayload {
  model?: string;
  choices?: Array<{
    message?: {
      content?: string | Array<{ type?: string; text?: string }>;
    };
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
  error?: { message?: string };
}

export class OpenRouterStudentAiGateway implements StudentAiGateway {
  private readonly apiKey: string;
  private readonly model: string;
  private readonly appUrl: string;
  private readonly appName: string;

  constructor(
    config: AppConfig,
    private readonly fetchImpl: typeof fetch = globalThis.fetch,
  ) {
    this.apiKey = config.openRouter?.apiKey.trim() ?? "";
    this.model = config.openRouter?.model.trim() || "openai/gpt-4o-mini";
    this.appUrl =
      config.openRouter?.appUrl.trim() || "http://localhost:3000";
    this.appName =
      config.openRouter?.appName.trim() || "Aster Student Portal";
  }

  async askEdward(
    input: AskEdwardInput & { studentContext: EdwardStudentContext },
  ): Promise<AskEdwardResponse> {
    if (!this.apiKey) return guidedEdwardResponse(input.message);
    const history = (input.history ?? []).slice(-6).map((message) => ({
      role: message.role,
      content: message.content.slice(0, 1_200),
    }));
    const studentContext = {
      ...input.studentContext,
      pageContext: input.pageContext.slice(0, 120),
    };
    const payload = await this.complete({
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
          content: `Current portal context: ${JSON.stringify(studentContext)}`,
        },
        ...history,
        { role: "user", content: input.message.slice(0, 2_000) },
      ],
    });
    return {
      message: readMessageContent(payload).slice(0, 2_500),
      provider: "openrouter",
      model: payload.model ?? this.model,
      usage: normalizeUsage(payload.usage),
      suggestedActions: suggestedActionsFor(input.message),
    };
  }

  async extractStudentDocument(input: {
    fileName: string;
    mimeType: "application/pdf" | "image/jpeg" | "image/png";
    bytes: Buffer;
  }): Promise<StudentDocumentExtraction> {
    if (!this.apiKey) return pendingExtraction(input.fileName);
    const dataUrl = `data:${input.mimeType};base64,${input.bytes.toString("base64")}`;
    const filePart =
      input.mimeType === "application/pdf"
        ? {
            type: "file",
            file: { filename: input.fileName, file_data: dataUrl },
          }
        : {
            type: "image_url",
            image_url: { url: dataUrl },
          };
    const payload = await this.complete({
      model: this.model,
      temperature: 0,
      max_tokens: 1_200,
      messages: [
        {
          role: "system",
          content:
            "Extract structured facts from the student's uploaded document. Copy only values visible in the document. Never infer missing values. Omit secrets such as full SSNs, taxpayer IDs, passport numbers, account numbers, card details, signatures, and medical diagnoses. Use warnings for unreadable, ambiguous, or sensitive sections. Return JSON matching the provided schema.",
        },
        {
          role: "user",
          content: [
            {
              type: "text",
              text: `Parse ${input.fileName} into safe student-record metadata.`,
            },
            filePart,
          ],
        },
      ],
      ...(input.mimeType === "application/pdf"
        ? {
            plugins: [
              { id: "file-parser", pdf: { engine: "cloudflare-ai" } },
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
      provider: { require_parameters: true },
    });
    const parsed = JSON.parse(readMessageContent(payload)) as Record<
      string,
      unknown
    >;
    return normalizeExtraction(parsed, payload.model ?? this.model);
  }

  private async complete(body: Record<string, unknown>) {
    const response = await this.fetchImpl(OPENROUTER_URL, {
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
    const payload = (await response.json().catch(() => null)) as
      | CompletionPayload
      | null;
    if (!response.ok) {
      throw new Error(
        payload?.error?.message || `OpenRouter returned HTTP ${response.status}`,
      );
    }
    if (!payload?.choices?.[0]?.message) {
      throw new Error("OpenRouter returned an empty completion");
    }
    return payload;
  }
}

function readMessageContent(payload: CompletionPayload): string {
  const content = payload.choices?.[0]?.message?.content;
  if (typeof content === "string" && content.trim()) return content.trim();
  if (Array.isArray(content)) {
    const text = content
      .filter((part) => part.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("\n")
      .trim();
    if (text) return text;
  }
  throw new Error("OpenRouter returned no readable content");
}

function normalizeUsage(usage: CompletionPayload["usage"]) {
  if (!usage) return null;
  return {
    promptTokens: Number(usage.prompt_tokens ?? 0),
    completionTokens: Number(usage.completion_tokens ?? 0),
    totalTokens: Number(usage.total_tokens ?? 0),
  };
}

function normalizeExtraction(
  value: Record<string, unknown>,
  model: string,
): StudentDocumentExtraction {
  const documentTypes = new Set(documentSchema.properties.documentType.enum);
  const rawFields = Array.isArray(value.fields) ? value.fields : [];
  const fields = rawFields.slice(0, 24).map((candidate, index) => {
    const field =
      candidate && typeof candidate === "object"
        ? (candidate as Record<string, unknown>)
        : {};
    return {
      key: safeText(field.key, `field_${index + 1}`, 80),
      label: safeText(field.label, `Field ${index + 1}`, 120),
      value: redactSensitiveValue(safeText(field.value, "", 500)),
      confidence: Math.max(
        0,
        Math.min(1, Number(field.confidence ?? 0)),
      ),
    };
  });
  return {
    status: "completed",
    documentType: documentTypes.has(
      value.documentType as (typeof documentSchema.properties.documentType.enum)[number],
    )
      ? (value.documentType as StudentDocumentExtraction["documentType"])
      : "other",
    summary: safeText(
      value.summary,
      "The document was parsed and is ready for review.",
      800,
    ),
    studentName: nullableText(value.studentName, 160),
    institutionName: nullableText(value.institutionName, 200),
    issueDate: nullableText(value.issueDate, 80),
    academicTerm: nullableText(value.academicTerm, 120),
    fields,
    warnings: (Array.isArray(value.warnings) ? value.warnings : [])
      .slice(0, 12)
      .map((warning) => safeText(warning, "", 400))
      .filter(Boolean),
    model,
    provider: "openrouter",
    processedAt: new Date().toISOString(),
    verifiedAt: null,
  };
}

function pendingExtraction(fileName: string): StudentDocumentExtraction {
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

function guidedEdwardResponse(message: string): AskEdwardResponse {
  const text = message.toLowerCase();
  const response = /document|upload|transcript|fafsa|ferpa/.test(text)
    ? "Open Documents to upload a PDF, JPEG, or PNG. Aster stores the original file and prepares structured fields for your review. Nothing extracted is treated as verified until you approve it."
    : /deadline|due|when/.test(text)
      ? "Your dashboard shows the nearest enrollment deadlines. Open Enrollment for the complete checklist and the status of each requirement."
      : /payment|deposit|pay/.test(text)
        ? "Open Payments to review the enrollment deposit. Payment details should only be entered in the secure processor—not in this chat."
        : /profile|phone|name|contact/.test(text)
          ? "You can update changeable contact preferences from Profile. Legal identity changes may require supporting documentation and staff review."
          : /appointment|advisor|person|human/.test(text)
            ? "Open Appointments to schedule enrollment, admissions, or financial-aid support with a staff member."
            : "I can help you find enrollment steps, documents, deadlines, payments, appointments, and profile settings.";
  return {
    message: response,
    provider: "guided",
    model: null,
    usage: null,
    suggestedActions: suggestedActionsFor(message),
  };
}

function suggestedActionsFor(message: string) {
  const text = message.toLowerCase();
  if (/document|upload|transcript|fafsa|ferpa/.test(text)) {
    return [{ label: "Open documents", href: "/documents" }];
  }
  if (/payment|deposit|pay/.test(text)) {
    return [{ label: "Open payments", href: "/payments" }];
  }
  if (/appointment|advisor|human/.test(text)) {
    return [{ label: "Book an appointment", href: "/appointments" }];
  }
  if (/profile|phone|name|contact/.test(text)) {
    return [{ label: "Open profile", href: "/profile" }];
  }
  return [
    { label: "View enrollment", href: "/enrollment" },
    { label: "Get support", href: "/help" },
  ];
}

function inferDocumentType(
  fileName: string,
): StudentDocumentExtraction["documentType"] {
  const name = fileName.toLowerCase();
  if (name.includes("transcript")) return "transcript";
  if (name.includes("fafsa") || name.includes("financial")) {
    return "financial_aid";
  }
  if (name.includes("ferpa") || name.includes("release")) return "ferpa";
  if (name.includes("immun") || name.includes("vaccine")) return "immunization";
  if (name.includes("passport") || name.includes("license")) return "identity";
  if (name.includes("residen")) return "residency";
  return "other";
}

function safeText(value: unknown, fallback: string, maximum: number): string {
  if (typeof value !== "string") return fallback;
  const text = value.trim().slice(0, maximum);
  return text || fallback;
}

function nullableText(value: unknown, maximum: number): string | null {
  if (value === null || value === undefined) return null;
  return safeText(value, "", maximum) || null;
}

function redactSensitiveValue(value: string): string {
  if (
    /\b\d{3}-?\d{2}-?\d{4}\b/.test(value) ||
    /\b(?:\d[ -]*?){13,19}\b/.test(value)
  ) {
    return "[sensitive value redacted]";
  }
  return value;
}
