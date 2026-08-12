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

const requirementPath = /^\/enrollment\/requirements\/[a-z0-9][a-z0-9-]{0,80}$/i;
const fallbackPageContext = "/dashboard";
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
 * The page is a routing hint for the prompt, never arbitrary model context.
 * Normalize known portal locations and collapse anything else to a safe,
 * low-information value.
 */
export function normalizeEdwardPageContext(value) {
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

export function normalizeEdwardActionHref(value) {
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
 * Reject capability-escalation requests before spending tokens. Edward has no
 * executor, host access, arbitrary network tool, or cross-student data tool.
 */
export function guardedEdwardResponse(message, studentContext = {}) {
  if (typeof message !== "string") return null;
  const universityName =
    typeof studentContext.universityName === "string" &&
    studentContext.universityName.trim()
      ? studentContext.universityName.trim().slice(0, 120)
      : "the university";
  const requestsCodeExecution =
    unsafeExecutionVerb.test(message) &&
    (unsafeExecutionTarget.test(message) ||
      sensitiveOrDestructiveTarget.test(message));
  const requestsSensitiveOperation =
    sensitiveOrDestructiveTarget.test(message) &&
    /\b(?:get|read|show|print|dump|steal|send|post|copy|expose|reveal|access|extract)\b/i.test(
      message,
    );

  let reason = null;
  if (crossStudentAccess.test(message)) {
    reason =
      `I can only use the signed-in student’s permission-scoped ${universityName} record. I can’t access or reveal another student’s information.`;
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

export function sanitizeEdwardProse(value) {
  const text = typeof value === "string" ? value : "";
  const normalized = text
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
    normalized.slice(0, 2_500) ||
    "I prepared the relevant student portal action below."
  );
}

/**
 * Actions are server-created UI controls. Filter them at the HTTP boundary so
 * an AI adapter (or a future provider) cannot turn its response into external
 * navigation. State-changing widgets are rebuilt from authoritative record
 * data instead of trusting provider-supplied IDs, amounts, status, or labels.
 */
export function normalizeEdwardResponse(response, authority) {
  const suggestedActions = Array.isArray(response?.suggestedActions)
    ? response.suggestedActions.flatMap((action) => {
        const href = normalizeEdwardActionHref(action?.href);
        const label =
          typeof action?.label === "string" ? action.label.trim().slice(0, 80) : "";
        return href && label ? [{ label, href }] : [];
      })
    : [];

  const blocks = normalizeResponseBlocks(response?.blocks);
  return {
    ...response,
    message: sanitizeEdwardProse(response?.message),
    ...(blocks.length > 0 ? { blocks } : {}),
    suggestedActions: suggestedActions.slice(0, 4),
    widgets: normalizeWidgets(response?.widgets, authority),
  };
}

const blockTypes = new Set([
  "text",
  "bullet_list",
  "numbered_list",
  "table",
  "next_steps",
]);

/**
 * Blocks are built from grounded state rather than written by a model, so this
 * is a bound rather than a trust boundary: it caps size and re-runs the same
 * prose sanitiser the message goes through, so no route can grow a way to put
 * unchecked text in front of a student.
 */
function normalizeResponseBlocks(blocks) {
  if (!Array.isArray(blocks)) return [];
  return blocks
    .flatMap((block) => {
      if (!block || typeof block !== "object" || !blockTypes.has(block.type)) {
        return [];
      }
      const fallbackText = sanitizeEdwardProse(block.fallbackText);
      if (!fallbackText) return [];
      const base = { type: block.type, fallbackText };
      if (block.type === "text") {
        return [{ ...base, text: sanitizeEdwardProse(block.text) }];
      }
      if (block.type === "table") {
        const columns = (Array.isArray(block.columns) ? block.columns : [])
          .slice(0, 6)
          .flatMap((column) =>
            column?.key && column?.label
              ? [
                  {
                    key: String(column.key).slice(0, 40),
                    label: sanitizeEdwardProse(column.label).slice(0, 60),
                    ...(column.align === "right" ? { align: "right" } : {}),
                  },
                ]
              : [],
          );
        if (columns.length === 0) return [];
        const rows = (Array.isArray(block.rows) ? block.rows : [])
          .slice(0, 25)
          .map((row) =>
            Object.fromEntries(
              columns.map((column) => [
                column.key,
                sanitizeEdwardProse(row?.[column.key] ?? "").slice(0, 160),
              ]),
            ),
          );
        return [
          {
            ...base,
            ...(block.caption ? { caption: sanitizeEdwardProse(block.caption) } : {}),
            columns,
            rows,
          },
        ];
      }
      const items = (Array.isArray(block.items) ? block.items : [])
        .slice(0, 12)
        .flatMap((item) => {
          const text = sanitizeEdwardProse(item?.text).slice(0, 300);
          if (!text) return [];
          const href = normalizeEdwardActionHref(item?.href);
          return [
            {
              text,
              ...(href ? { href } : {}),
              ...(item?.owner === "student" || item?.owner === "university"
                ? { owner: item.owner }
                : {}),
            },
          ];
        });
      if (items.length === 0) return [];
      return [
        {
          ...base,
          ...(block.title ? { title: sanitizeEdwardProse(block.title) } : {}),
          items,
        },
      ];
    })
    .slice(0, 8);
}

function normalizeWidgets(widgets, authority) {
  if (!Array.isArray(widgets)) return [];
  return widgets
    .flatMap((widget) => {
      if (!widget || typeof widget !== "object") return [];
      if (widget.type === "deposit_payment") {
        if (!authority?.allowDepositPayment) return [];
        return [
          {
            type: "deposit_payment",
            id: "edward-deposit-payment",
            title: "Enrollment deposit",
            description: authority.depositPaid
              ? "Your enrollment deposit is recorded as paid."
              : "Complete the simulated enrollment deposit securely here.",
            offerId: authority.offerId,
            amountCents: authority.depositAmountCents,
            status: authority.depositPaid ? "completed" : "ready",
          },
        ];
      }
      if (widget.type === "document_upload") {
        if (!authority?.documentUploadCategory) return [];
        return [
          {
            type: "document_upload",
            id: "edward-document-upload",
            title: "Upload a document",
            description:
              "Add a PDF, JPEG, or PNG through the protected document workflow.",
            category: authority.documentUploadCategory,
            href: "/documents",
          },
        ];
      }
      if (widget.type === "appointment") {
        if (!authority?.appointmentType) return [];
        return [
          {
            type: "appointment",
            id: "edward-advisor-appointment",
            title: "Meet with a student advisor",
            description:
              "Choose a time with the team best suited to your question.",
            appointmentType: authority.appointmentType,
            href: "/appointments",
          },
        ];
      }
      return [];
    })
    .slice(0, 2);
}
