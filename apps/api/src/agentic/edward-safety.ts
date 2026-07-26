import type {
  AskEdwardResponse,
  EdwardActionWidget,
} from "@vv/contracts";

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
 * A gateway (including a future provider implementation) is not trusted to
 * choose navigable destinations. Strip unsafe suggestions before returning the
 * response from the application boundary. Deposit widgets intentionally pass
 * through untouched because their action is a separate, server-authorized POST
 * and has no navigable href.
 */
export function normalizeEdwardResponse(
  response: AskEdwardResponse,
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
    suggestedActions: suggestedActions.slice(0, 4),
    widgets: normalizeWidgets(response.widgets),
  };
}

function normalizeWidgets(widgets: EdwardActionWidget[]): EdwardActionWidget[] {
  if (!Array.isArray(widgets)) return [];
  const normalized: EdwardActionWidget[] = [];
  for (const widget of widgets) {
    if (!widget || typeof widget !== "object") continue;
    if (widget.type === "deposit_payment") {
      normalized.push(widget);
      continue;
    }
    if (widget.type !== "document_upload" && widget.type !== "appointment") {
      continue;
    }
    const href = normalizeEdwardActionHref(widget.href);
    if (href) normalized.push({ ...widget, href });
  }
  return normalized.slice(0, 2);
}
