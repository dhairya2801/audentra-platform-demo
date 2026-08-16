/**
 * Deterministic grading over raw transcripts: contract invariants, per-case
 * checks (against the FULL student-visible answer — prose plus every block's
 * fallbackText, so a fact satisfied inside a bullet list or table counts),
 * tool-selection codes, and taxonomy. Shared by run.mjs (live) and
 * regrade.mjs (stored batches, free).
 */

import { runContractChecks, runChecks } from "./assertions.mjs";
import { gradeToolSelection, toolStats } from "./tool-grading.mjs";
import { classifyTurn, dedupeCodes } from "./taxonomy.mjs";

/** Prose plus non-duplicate block fallbacks — what the student actually sees. */
export function studentVisibleText(turn) {
  const blocks = turn.response?.blocks ?? [];
  const extras = blocks
    .map((block) => block.fallbackText ?? "")
    .filter((text) => text && text !== turn.answer);
  return [turn.answer, ...extras].join("\n");
}

export function gradeTranscripts(transcripts, factsByPersona) {
  return transcripts.map((record) => {
    const facts = factsByPersona[record.persona] ?? {};
    const turns = record.turns.map((turn) => {
      if (turn.error) {
        return {
          ...turn,
          contractFailures: [],
          checkFailures: [],
          toolCodes: [],
          deterministicPass: false,
        };
      }
      const contractFailures = runContractChecks(turn.response);
      const visible = studentVisibleText(turn);
      const checkFailures = (turn.checks ?? [])
        .map((check) => ({
          check,
          failure: runChecks([check], visible, turn.response, { facts })[0] ?? null,
        }))
        .filter((entry) => entry.failure);
      const toolCodes = gradeToolSelection(turn.expect, turn.response);
      return {
        ...turn,
        contractFailures,
        checkFailures,
        toolCodes,
        toolStats: toolStats(turn.response),
        deterministicPass:
          contractFailures.length === 0 && checkFailures.length === 0 && toolCodes.length === 0,
      };
    });
    return {
      ...record,
      turns,
      deterministicPass: turns.every((turn) => turn.deterministicPass),
    };
  });
}

/** Taxonomy + per-case judgement rollup; call after any judge pass. */
export function finalizeRecords(results) {
  for (const record of results) {
    for (const turn of record.turns) {
      turn.taxonomy = classifyTurn(turn, record);
    }
    record.taxonomyCodes = dedupeCodes(record.turns.flatMap((turn) => turn.taxonomy ?? []));
    const judgedTurns = record.turns.filter((turn) => turn.judgement);
    record.judgement =
      judgedTurns.length > 0
        ? {
            total: judgedTurns.reduce((sum, turn) => sum + turn.judgement.total, 0),
            maximum: judgedTurns.reduce((sum, turn) => sum + turn.judgement.maximum, 0),
          }
        : null;
  }
  return results;
}
