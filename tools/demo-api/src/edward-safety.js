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
 * Actions are server-created UI controls. Filter them at the HTTP boundary so
 * an AI adapter (or a future provider) cannot turn its response into external
 * navigation. Deposit widgets do not have an href, so their payment flow is
 * intentionally left exactly as-is.
 */
export function normalizeEdwardResponse(response) {
  const suggestedActions = Array.isArray(response?.suggestedActions)
    ? response.suggestedActions.flatMap((action) => {
        const href = normalizeEdwardActionHref(action?.href);
        const label =
          typeof action?.label === "string" ? action.label.trim().slice(0, 80) : "";
        return href && label ? [{ label, href }] : [];
      })
    : [];

  return {
    ...response,
    suggestedActions: suggestedActions.slice(0, 4),
    widgets: normalizeWidgets(response?.widgets),
  };
}

function normalizeWidgets(widgets) {
  if (!Array.isArray(widgets)) return [];
  return widgets
    .flatMap((widget) => {
      if (!widget || typeof widget !== "object") return [];
      if (widget.type === "deposit_payment") return [widget];
      if (widget.type !== "document_upload" && widget.type !== "appointment") {
        return [];
      }
      const href = normalizeEdwardActionHref(widget.href);
      return href ? [{ ...widget, href }] : [];
    })
    .slice(0, 2);
}
