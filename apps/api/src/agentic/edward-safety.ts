import type {
  AskEdwardResponse,
  EdwardActionWidget,
  StudentAppointmentType,
  StudentDocumentCategory,
} from "@vv/contracts";

export interface EdwardActionAuthority {
  offerId: string;
  depositAmountCents: number;
  depositPaid: boolean;
  allowDepositPayment: boolean;
  documentUploadCategory: StudentDocumentCategory | null;
  appointmentType: StudentAppointmentType | null;
}

const unsafeExecutionTarget =
  /\b(?:python|shell|bash|zsh|powershell|terminal|command|script|node(?:\.js)?|curl|wget|reverse shell|remote code)\b/i;
const unsafeExecutionVerb =
  /\b(?:run|execute|launch|invoke|spawn|eval|install|upload and run|write and run)\b/i;
const sensitiveOrDestructiveTarget =
  /(?:\.env\b|environment variables?|\bapi[-_ ]?keys?\b|\bsecrets?\b|\bpasswords?\b|\bcredentials?\b|\bauth tokens?\b|169\.254\.169\.254|metadata service|instance metadata|read (?:a )?file|filesystem|exfiltrat|\bdelete\b|\bdrop (?:the )?(?:database|table)\b|\bransomware\b|\bmalware\b)/i;
const authorityBypass =
  /(?:ignore|override|bypass|disregard|reveal).{0,48}(?:system prompt|developer message|safety (?:rule|policy)|access control|authorization|hidden prompt)|(?:jailbreak|prompt injection)/i;
const maliciousCodeRequest =
  /(?:write|create|generate|provide|give me).{0,48}(?:python|shell|bash|powershell|code|script).{0,80}(?:hack|attack|exploit|steal|exfiltrat|bypass|reverse shell|malware|ransomware)|(?:hack|attack|exploit|steal|exfiltrat|bypass|reverse shell|malware|ransomware).{0,80}(?:python|shell|bash|powershell|code|script)/i;
const forgedRecordMutation =
  /(?:mark|set|change|make|pretend|forge).{0,48}(?:deposit|payment|requirement|application|record|course|grade|aid).{0,48}(?:paid|complete|completed|approved|verified|accepted|waived)|(?:paid|complete|completed|approved|verified|accepted|waived).{0,48}(?:without paying|without approval|without authorization)/i;
const crossStudentAccess =
  /(?:another|other|different|all).{0,24}students?.{0,48}(?:record|profile|document|payment|grade|email|phone|data)|(?:record|profile|document|payment|grade|email|phone|data).{0,48}(?:another|other|different|all).{0,24}students?/i;

/**
 * Edward receives the current page only as a small routing hint. It must never
 * become free-form model context, so collapse a known portal route to a
 * canonical value and use a benign default for anything else.
 */
const portalPageContexts = new Set([
  "/dashboard",
  "/enrollment",
  "/enrollment/requirements",
  "/financials",
  "/classrooms",
  "/campus-life",
  "/edward",
  "/profile",
  "/documents",
  "/payments",
  "/appointments",
  "/help",
]);

const requirementPath = /^\/enrollment\/requirements\/[a-z0-9][a-z0-9-]{0,80}$/i;

/**
 * These are navigation destinations Edward is allowed to offer. Keep this
 * independent from the model response: the model supplies prose only.
 */
const portalActionHrefs = new Set([
  "/dashboard",
  "/enrollment",
  "/financials",
  "/classrooms",
  "/campus-life",
  "/edward",
  "/profile",
  "/documents",
  "/payments",
  "/appointments",
  "/help",
]);

const fallbackPageContext = "/dashboard";

export function normalizeEdwardPageContext(value: unknown): string {
  if (typeof value !== "string") return fallbackPageContext;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 120 || /[\u0000-\u001f\u007f]/.test(trimmed)) {
    return fallbackPageContext;
  }
  const candidate = trimmed.length > 1 ? trimmed.replace(/\/+$/, "") : trimmed;
  if (portalPageContexts.has(candidate)) return candidate;
  if (requirementPath.test(candidate)) return "/enrollment/requirements";
  return fallbackPageContext;
}

export function normalizeEdwardActionHref(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 160 || /[\u0000-\u001f\u007f]/.test(trimmed)) {
    return null;
  }
  const candidate = trimmed.length > 1 ? trimmed.replace(/\/+$/, "") : trimmed;
  if (portalActionHrefs.has(candidate)) return candidate;
  return requirementPath.test(candidate) ? candidate : null;
}

/**
 * High-risk requests never reach a model. This is deliberately a narrow,
 * deterministic policy boundary for capabilities Edward does not possess:
 * code execution, host/secret access, authorization bypasses, forged record
 * changes, and access to another student's record.
 */
export function guardedEdwardResponse(
  message: unknown,
): AskEdwardResponse | null {
  if (typeof message !== "string") return null;
  const requestsCodeExecution =
    unsafeExecutionVerb.test(message) &&
    (unsafeExecutionTarget.test(message) ||
      sensitiveOrDestructiveTarget.test(message));
  const requestsSensitiveOperation =
    sensitiveOrDestructiveTarget.test(message) &&
    /\b(?:get|read|show|print|dump|steal|send|post|copy|expose|reveal|access|extract)\b/i.test(
      message,
    );

  let reason: string | null = null;
  if (crossStudentAccess.test(message)) {
    reason =
      "I can only use the signed-in student’s permission-scoped Aster record. I can’t access or reveal another student’s information.";
  } else if (forgedRecordMutation.test(message)) {
    reason =
      "I can’t forge, approve, or mark payments and student records complete from chat. Use the authorized portal workflow so validation, idempotency, and the audit trail are preserved.";
  } else if (
    requestsCodeExecution ||
    requestsSensitiveOperation ||
    authorityBypass.test(message) ||
    maliciousCodeRequest.test(message)
  ) {
    reason =
      "I can’t run code or commands, access server files or secrets, bypass safeguards, or attack systems. Edward has no shell, Python, filesystem, or arbitrary network tools.";
  }
  if (!reason) return null;

  return {
    message: reason,
    provider: "guided",
    model: null,
    usage: null,
    suggestedActions: [],
    contextReceipts: [],
    widgets: [],
  };
}

/**
 * Prose is rendered as text by the React client, but it is also normalized at
 * the server boundary so a future renderer cannot accidentally activate
 * model-invented markup or URI schemes.
 */
export function sanitizeEdwardProse(value: unknown): string {
  const text = typeof value === "string" ? value : "";
  const withoutMarkup = text
    .replace(/<\s*\/?\s*(?:script|style|iframe|object|embed|link|meta)\b[^>]*>/gi, "")
    .replace(
      /\[([^\]\r\n]{1,240})\]\(\s*(?:(?:[a-z][a-z0-9+.-]*:)|\/\/|\/)[^\s)]*\s*\)/gi,
      "$1",
    )
    .replace(
      /(?:https?:\/\/|www\.|\/\/|(?:javascript|vbscript|data|mailto|tel|file|blob):)[^\s<>()\]]+/gi,
      "",
    )
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s+([,.;:!?])/g, "$1")
    .trim();
  return (
    textSlice(withoutMarkup, 2_500) ||
    "I prepared the relevant Aster portal action below."
  );
}

/**
 * A gateway (including a future provider implementation) is not trusted to
 * choose navigable destinations. Strip unsafe suggestions before returning the
 * response from the application boundary. State-changing widgets are rebuilt
 * from authoritative student data rather than trusting provider-supplied IDs,
 * amounts, status, or labels.
 */
export function normalizeEdwardResponse(
  response: AskEdwardResponse,
  authority?: EdwardActionAuthority,
): AskEdwardResponse {
  const suggestedActions = Array.isArray(response.suggestedActions)
    ? response.suggestedActions.flatMap((action) => {
        if (!action || typeof action !== "object") return [];
        const href = normalizeEdwardActionHref(action.href);
        const label =
          typeof action.label === "string" ? action.label.trim().slice(0, 80) : "";
        return href && label ? [{ label, href }] : [];
      })
    : [];

  return {
    ...response,
    message: sanitizeEdwardProse(response.message),
    suggestedActions: suggestedActions.slice(0, 4),
    widgets: normalizeWidgets(response.widgets, authority),
  };
}

function normalizeWidgets(
  widgets: EdwardActionWidget[],
  authority?: EdwardActionAuthority,
): EdwardActionWidget[] {
  if (!Array.isArray(widgets)) return [];
  const normalized: EdwardActionWidget[] = [];
  for (const widget of widgets) {
    if (!widget || typeof widget !== "object") continue;
    if (widget.type === "deposit_payment") {
      if (!authority?.allowDepositPayment) continue;
      normalized.push({
        type: "deposit_payment",
        id: "edward-deposit-payment",
        title: "Enrollment deposit",
        description: authority.depositPaid
          ? "Your enrollment deposit is recorded as paid."
          : "Complete the simulated enrollment deposit securely here.",
        offerId: authority.offerId,
        amountCents: authority.depositAmountCents,
        status: authority.depositPaid ? "completed" : "ready",
      });
      continue;
    }
    if (widget.type === "document_upload") {
      if (!authority?.documentUploadCategory) continue;
      normalized.push({
        type: "document_upload",
        id: "edward-document-upload",
        title: "Upload a document",
        description:
          "Add a PDF, JPEG, or PNG through the protected document workflow.",
        category: authority.documentUploadCategory,
        href: "/documents",
      });
      continue;
    }
    if (widget.type === "appointment") {
      if (!authority?.appointmentType) continue;
      normalized.push({
        type: "appointment",
        id: "edward-advisor-appointment",
        title: "Meet with a student advisor",
        description: "Choose a time with the team best suited to your question.",
        appointmentType: authority.appointmentType,
        href: "/appointments",
      });
    }
  }
  return normalized.slice(0, 2);
}

function textSlice(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : value.slice(0, maxLength);
}
