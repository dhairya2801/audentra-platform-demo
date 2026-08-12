/**
 * Public entry point for the synthetic university data layer.
 *
 * Import from here rather than from the individual modules: the split between
 * `generate`, `invariants`, `state-matrix`, and `personas` is an internal
 * organisation of the same data set.
 */

export {
  AID_YEAR,
  BALANCE_HOLD_THRESHOLD_USD,
  DEFAULT_NOW,
  DEFAULT_SEED,
  DEFAULT_STUDENT_COUNT,
  DOCUMENT_STATUSES,
  ENROLLMENT_DEPOSIT_USD,
  MAX_STUDENT_COUNT,
  MIN_STUDENT_COUNT,
  UNIVERSE_VERSION,
  generateUniverse,
  studentRecord,
  studentsByStateKey,
} from "./generate.js";

export { validateUniverse } from "./invariants.js";

export {
  STATE_MATRIX,
  STATE_MATRIX_BY_KEY,
  applyStateKey,
} from "./state-matrix.js";

export {
  SYNTHETIC_PERSONAS,
  SYNTHETIC_PERSONAS_BY_KEY,
  personaStudent,
} from "./personas.js";

export {
  addDays,
  addHours,
  createRng,
  intBetween,
  pick,
  shuffle,
  uuidFrom,
  uuidFromString,
  weighted,
} from "./random.js";

import { DEFAULT_SEED, generateUniverse } from "./generate.js";

/** @type {object | null} */
let memoised = null;

/**
 * The default universe, generated once per process.
 *
 * The result is shared, not cloned: callers must treat it as read-only. Anyone
 * who needs to mutate should call `generateUniverse()` directly, which always
 * returns fresh objects.
 *
 * @returns {object}
 */
export function syntheticUniverse() {
  memoised ??= generateUniverse({ seed: DEFAULT_SEED });
  return memoised;
}

/** Drops the memoised universe. Exists for tests that measure generation. */
export function resetSyntheticUniverse() {
  memoised = null;
}
