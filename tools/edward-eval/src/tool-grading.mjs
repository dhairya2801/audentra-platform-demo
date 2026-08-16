/**
 * Tool-selection grading, independent of answer quality.
 *
 * A case's `expect` block states what a correct plan looks like; this module
 * compares it with what actually executed (both rounds) and emits typed
 * failure codes. Several valid paths are respected: only tools listed as
 * `requiredTools` (or one-per-group `anyOfTools`) are demanded, universal
 * context reads are always acceptable, and `acceptableTools` widens the set
 * further without demanding anything.
 *
 * Codes:
 *   INTENT_FAILURE          classification landed outside the expected set
 *   TOOL_NOT_CALLED         a required read (or every member of a group) is absent
 *   WRONG_TOOL              a required read is missing while an out-of-scope read ran
 *   UNNECESSARY_TOOL        a forbidden read ran, or the ceiling was exceeded
 *   DEPENDENCY_READ_MISSING an expected dependency-round read never happened
 *   BAD_TOOL_ARGUMENT       reserved: platform tools take no caller arguments,
 *                           so this cannot occur today; kept for taxonomy stability
 */

import { UNIVERSAL_CONTEXT_TOOLS } from "./case-schema.mjs";

export function gradeToolSelection(expect, response) {
  if (!expect || !response) return [];
  const codes = [];
  const executed = response.graphExecution?.executedTools ?? [];
  const executedSet = new Set(executed);

  const requestTypes = response.requestTypes ?? [];
  if (
    Array.isArray(expect.requestTypes) &&
    expect.requestTypes.length > 0 &&
    !requestTypes.some((type) => expect.requestTypes.includes(type))
  ) {
    codes.push({
      code: "INTENT_FAILURE",
      detail: `expected requestType in [${expect.requestTypes.join(", ")}], got [${requestTypes.join(", ") || "none"}]`,
    });
  }

  const required = expect.requiredTools ?? [];
  const missingRequired = required.filter((tool) => !executedSet.has(tool));
  for (const tool of missingRequired) {
    codes.push({ code: "TOOL_NOT_CALLED", detail: `${tool} never executed` });
  }
  for (const group of expect.anyOfTools ?? []) {
    if (!group.some((tool) => executedSet.has(tool))) {
      codes.push({
        code: "TOOL_NOT_CALLED",
        detail: `none of [${group.join(", ")}] executed`,
      });
    }
  }

  for (const tool of expect.forbiddenTools ?? []) {
    if (executedSet.has(tool)) {
      codes.push({ code: "UNNECESSARY_TOOL", detail: `${tool} should not run` });
    }
  }
  if (
    typeof expect.maxTools === "number" &&
    executed.length > expect.maxTools
  ) {
    codes.push({
      code: "UNNECESSARY_TOOL",
      detail: `${executed.length} reads executed, ceiling ${expect.maxTools}: ${executed.join(", ")}`,
    });
  }

  if (missingRequired.length > 0) {
    const acceptable = new Set([
      ...required,
      ...(expect.acceptableTools ?? []),
      ...(expect.anyOfTools ?? []).flat(),
      ...(expect.dependencyTools ?? []),
      ...UNIVERSAL_CONTEXT_TOOLS,
    ]);
    const strays = executed.filter((tool) => !acceptable.has(tool));
    if (strays.length > 0) {
      codes.push({
        code: "WRONG_TOOL",
        detail: `read [${strays.join(", ")}] instead of [${missingRequired.join(", ")}]`,
      });
    }
  }

  for (const tool of expect.dependencyTools ?? []) {
    if (!executedSet.has(tool)) {
      codes.push({
        code: "DEPENDENCY_READ_MISSING",
        detail: `${tool} was expected as a dependency (or initial) read`,
      });
    }
  }

  return codes;
}

/** Executed-read stats used by the summary (unnecessary-tool rate, averages). */
export function toolStats(response) {
  const calls = response?.trace?.toolCalls ?? [];
  return {
    executed: calls.length,
    dependencyRound: calls.filter((call) => call.round === "dependency").length,
    unavailable: calls.filter((call) => call.status !== "available").length,
    selectionSource: response?.graphExecution?.toolSelectionSource ?? null,
  };
}
