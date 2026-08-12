/**
 * Guaranteed coverage of every interesting student state.
 *
 * Purely probabilistic generation is a bad basis for evaluation: at 3% odds a
 * "rejected document" student is present on most runs and absent on some, and
 * a regression suite that silently loses its only rejected-document case is
 * worse than no suite at all. So the generator hands the first block of
 * students to this matrix, which forces each state into existence, and only
 * then randomises the rest of the population.
 *
 * Each entry mutates `student.plan` — the internal trait bag the generator
 * reads when it builds the student's documents, holds, aid, ledger, and
 * housing. `generate.js` reconciles contradictions afterwards (for example,
 * housing is dropped for a student whose deposit never posted), so an entry
 * only has to state the traits it actually cares about.
 *
 * Adding a state: append an entry here and, if it deserves a narrative, add a
 * persona in `personas.js` that references the new key.
 *
 * @typedef {object} StateMatrixEntry
 * @property {string} key kebab-case, stable; students record it in `stateKeys`
 * @property {string} label human-readable summary
 * @property {(student: any, universe: any, rng: () => number) => void} assign
 */

/** @type {readonly StateMatrixEntry[]} */
export const STATE_MATRIX = Object.freeze([
  {
    key: "clean-complete",
    label: "Nothing outstanding: documents accepted, aid finalised, housing assigned",
    assign(student) {
      const plan = student.plan;
      plan.decision = "admitted";
      plan.documentProfile = "complete";
      plan.holdCount = 0;
      plan.depositState = "posted";
      plan.fafsaState = "verification_complete";
      plan.aidState = "finalized";
      plan.coverage = "full";
      plan.balanceState = "settled";
      plan.housingState = "residential";
      plan.housingAssigned = true;
      plan.advisingComplete = true;
      plan.orientationStatus = "attended";
    },
  },
  {
    key: "no-holds",
    label: "No holds of any kind on the account",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.holdCount = 0;
    },
  },
  {
    key: "missing-documents",
    label: "Required documents never submitted",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.documentProfile = "missing";
    },
  },
  {
    key: "document-under-review",
    label: "Document uploaded and sitting in review",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.documentProfile = "under_review";
    },
  },
  {
    key: "document-rejected",
    label: "Document reviewed and rejected outright",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.documentProfile = "rejected";
    },
  },
  {
    key: "document-needs-resubmission",
    label: "Document returned to the student for resubmission",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.documentProfile = "needs_resubmission";
    },
  },
  {
    key: "multiple-holds",
    label: "Three simultaneous holds from different offices",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.holdCount = 3;
    },
  },
  {
    key: "single-hold",
    label: "Exactly one open hold",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.holdCount = 1;
    },
  },
  {
    key: "deposit-pending",
    label: "Enrolment deposit initiated but not yet cleared",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.depositState = "pending";
      student.plan.housingState = "none";
    },
  },
  {
    key: "deposit-posted",
    label: "Enrolment deposit cleared and posted to the ledger",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.depositState = "posted";
    },
  },
  {
    key: "fafsa-missing",
    label: "Aid year opened but no FAFSA has arrived",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.fafsaState = "not_received";
      student.plan.aidState = "none";
    },
  },
  {
    key: "fafsa-received",
    label: "FAFSA received, not selected for verification",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.fafsaState = "received";
      student.plan.aidState = "estimated";
    },
  },
  {
    key: "selected-for-verification",
    label: "Selected for federal verification with outstanding requirements",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.fafsaState = "selected_for_verification";
      student.plan.aidState = "estimated";
    },
  },
  {
    key: "aid-finalized",
    label: "Verification complete and the award package is final",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.fafsaState = "verification_complete";
      student.plan.aidState = "finalized";
      student.plan.coverage = "full";
    },
  },
  {
    key: "aid-estimated",
    label: "Award package still marked estimated pending verification",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.fafsaState = "received";
      student.plan.aidState = "estimated";
    },
  },
  {
    key: "aid-disbursed",
    label: "Accepted awards disbursed to the student account",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.depositState = "posted";
      student.plan.fafsaState = "verification_complete";
      student.plan.aidState = "disbursed";
      student.plan.coverage = "full";
    },
  },
  {
    key: "partial-tuition-coverage",
    label: "Aid covers only part of tuition, leaving a real balance",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.fafsaState = "verification_complete";
      student.plan.aidState = "finalized";
      student.plan.coverage = "partial";
      student.plan.balanceState = "owed";
    },
  },
  {
    key: "balance-owed",
    label: "Unpaid balance on the student account",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.balanceState = "owed";
      student.plan.coverage = "partial";
    },
  },
  {
    key: "refund-due",
    label: "Aid exceeded charges, so a credit balance is waiting to be refunded",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.depositState = "posted";
      student.plan.fafsaState = "verification_complete";
      student.plan.aidState = "disbursed";
      student.plan.coverage = "over";
      student.plan.balanceState = "refund";
    },
  },
  {
    key: "no-aid",
    label: "No FAFSA and no awards: paying out of pocket",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.fafsaState = "none";
      student.plan.aidState = "none";
    },
  },
  {
    key: "deadline-passed",
    label: "Deposit deadline came and went with nothing paid",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.deadlinePassed = true;
      student.plan.depositState = "none";
      student.plan.housingState = "none";
    },
  },
  {
    key: "international-student",
    label: "International admit with immigration requirements outstanding",
    assign(student) {
      student.plan.decision = "admitted";
      student.admitType = "international";
      student.residency = "international";
    },
  },
  {
    key: "transfer-student",
    label: "Transfer admit whose prior credit is still being evaluated",
    assign(student) {
      student.plan.decision = "admitted";
      student.admitType = "transfer";
    },
  },
  {
    key: "commuter",
    label: "Deposited student living off campus",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.depositState = "posted";
      student.plan.housingState = "commuter";
    },
  },
  {
    key: "residential",
    label: "Deposited student assigned to a residence hall",
    assign(student) {
      student.plan.decision = "admitted";
      student.plan.depositState = "posted";
      student.plan.housingState = "residential";
      student.plan.housingAssigned = true;
    },
  },
]);

/** @type {ReadonlyMap<string, StateMatrixEntry>} */
export const STATE_MATRIX_BY_KEY = new Map(
  STATE_MATRIX.map((entry) => [entry.key, entry]),
);

/**
 * Applies one named state to a student draft. Throws on an unknown key so a
 * typo in `personas.js` fails loudly at generation time rather than producing
 * a persona whose scenario text does not match its data.
 *
 * @param {any} student
 * @param {string} key
 * @param {any} universe
 * @param {() => number} rng
 */
export function applyStateKey(student, key, universe, rng) {
  const entry = STATE_MATRIX_BY_KEY.get(key);
  if (!entry) {
    throw new Error(
      `Unknown synthetic state key "${key}"; known keys: ${[...STATE_MATRIX_BY_KEY.keys()].join(", ")}`,
    );
  }
  entry.assign(student, universe, rng);
}
