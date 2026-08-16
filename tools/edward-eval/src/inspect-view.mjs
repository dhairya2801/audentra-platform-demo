/**
 * Single-case inspection view: everything needed to debug one failure in one
 * screenful — question, student state, conversation, expectations,
 * classification, tools (both rounds), tool results, answer, deterministic
 * verdicts, judge scores, taxonomy, and the trace id to dig deeper.
 */

const line = (label, value) =>
  console.log(`${label.padEnd(24)} ${value ?? "—"}`);

function compactFacts(facts) {
  if (!facts) return null;
  return [
    `deposit=${facts.depositState}`,
    `open=[${facts.openRequirementTitles?.slice(0, 6).join(" | ") ?? ""}]`,
    `missingDocs=[${facts.missingDocumentTitles?.join(" | ") ?? ""}]`,
    `gates=[${facts.registrationGateCodes?.join(", ") ?? ""}]`,
    `aidOpen=[${facts.openAidDocumentTitles?.join(" | ") ?? ""}]`,
    `housing=${facts.housingEligibility}`,
    `balance=${facts.remainingBalanceUsd ?? "-"}`,
  ].join("\n                         ");
}

export function printInspection(record, facts) {
  console.log(`\n${"=".repeat(78)}`);
  line("CASE", `${record.id}  [${record.category} / ${record.capability}]`);
  line("PERSONA", record.persona + (record.faults ? `  faults=${JSON.stringify(record.faults)}` : ""));
  line("TAGS", record.tags.join(", ") || "—");
  if (record.critical) line("CRITICAL", "yes — failing this fails the run");
  line("STUDENT STATE", compactFacts(facts));
  if (record.expectedBehavior) line("EXPECTED BEHAVIOR", record.expectedBehavior);
  if (record.note) line("NOTE", record.note);
  if (record.history.length > 0) {
    console.log("SEEDED CONVERSATION:");
    for (const entry of record.history) {
      console.log(`   ${entry.role === "user" ? "student" : "edward "}> ${entry.content}`);
    }
  }

  record.turns.forEach((turn, index) => {
    console.log(`\n--- turn ${index + 1}/${record.turns.length} ${"-".repeat(50)}`);
    line("QUESTION", turn.question);
    if (turn.error) {
      line("ERROR", turn.error);
      return;
    }
    const trace = turn.response?.trace;
    line(
      "CLASSIFICATION",
      trace?.classification
        ? `${trace.classification.requestType} (${trace.classification.source}, conf ${trace.classification.confidence})` +
            (trace.classification.additionalRequestTypes?.length
              ? ` +[${trace.classification.additionalRequestTypes.join(", ")}]`
              : "")
        : null,
    );
    if (turn.expect) {
      line("EXPECTATIONS", JSON.stringify(turn.expect));
    }
    const initial = (trace?.toolCalls ?? []).filter((call) => call.round !== "dependency");
    const dependency = (trace?.toolCalls ?? []).filter((call) => call.round === "dependency");
    line(
      "SELECTED TOOLS",
      initial.map((call) => `${call.tool}(${call.status})`).join(", ") || "none",
    );
    if (dependency.length > 0) {
      line(
        "DEPENDENCY TOOLS",
        dependency.map((call) => `${call.tool}(${call.status})`).join(", ") +
          (trace?.secondRead?.triggeredBy
            ? `  triggered by ${trace.secondRead.triggeredBy.map((reason) => reason.gate).join(", ")}`
            : ""),
      );
    }
    for (const call of trace?.toolCalls ?? []) {
      if (call.result !== undefined && call.result !== null) {
        const rendered = JSON.stringify(call.result);
        console.log(
          `   ${call.tool} result: ${rendered.slice(0, 400)}${rendered.length > 400 ? "…" : ""}`,
        );
      }
    }
    line("FINAL ANSWER", turn.answer);
    const deterministic = [
      ...(turn.contractFailures ?? []).map((failure) => `contract: ${failure}`),
      ...(turn.checkFailures ?? []).map((entry) => `${entry.check.kind}: ${entry.failure}`),
      ...(turn.toolCodes ?? []).map((code) => `${code.code}: ${code.detail}`),
    ];
    line(
      "DETERMINISTIC",
      deterministic.length === 0 ? "PASS" : `FAIL\n   - ${deterministic.join("\n   - ")}`,
    );
    if (turn.judgement) {
      line(
        "JUDGE SCORES",
        `${turn.judgement.total}/${turn.judgement.maximum}  ` +
          Object.entries(turn.judgement.scores)
            .map(([key, score]) => `${key}=${score}`)
            .join(" "),
      );
      if (turn.judgement.worstProblem) line("JUDGE WORST PROBLEM", turn.judgement.worstProblem);
    } else if (turn.judgementError) {
      line("JUDGE ERROR", turn.judgementError);
    }
    if ((turn.taxonomy ?? []).length > 0) {
      line(
        "FAILURE CATEGORIES",
        [...new Set(turn.taxonomy.map((entry) => entry.code))].join(", "),
      );
    }
    line("TRACE ID", turn.requestId);
    line(
      "LATENCY",
      `${turn.latencyMs ?? "-"}ms client / ${trace?.durationMs ?? "-"}ms server`,
    );
  });
  console.log("=".repeat(78));
}
