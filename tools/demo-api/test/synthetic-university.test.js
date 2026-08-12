/**
 * The synthetic university's contract with everything downstream.
 *
 * Two properties are load-bearing and both are easy to break by accident, so
 * they are asserted directly rather than through a proxy: a seed must rebuild
 * byte-identical data, and a generated universe must satisfy every invariant
 * `validateUniverse` knows about. The corruption test at the end exists
 * because a validator that has never rejected anything is not evidence of
 * anything — it checks that the checks actually fire.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  DEFAULT_SEED,
  MAX_STUDENT_COUNT,
  MIN_STUDENT_COUNT,
  STATE_MATRIX,
  SYNTHETIC_PERSONAS,
  generateUniverse,
  studentsByStateKey,
  validateUniverse,
} from "../src/synthetic-university/index.js";

/** The floor of the allowed range: enough for full coverage, cheap to stringify. */
const SMALL = MIN_STUDENT_COUNT;

describe("synthetic university generation", () => {
  it("rebuilds byte-identical JSON from the same seed", () => {
    const first = generateUniverse({ studentCount: SMALL });
    const second = generateUniverse({ studentCount: SMALL });
    assert.equal(JSON.stringify(first), JSON.stringify(second));
  });

  it("produces different data from a different seed", () => {
    const first = generateUniverse({ seed: DEFAULT_SEED, studentCount: SMALL });
    const other = generateUniverse({ seed: DEFAULT_SEED + 1, studentCount: SMALL });
    assert.notEqual(JSON.stringify(first), JSON.stringify(other));
    // Not merely a different `meta.seed`: the population itself must differ.
    assert.notDeepEqual(
      first.students.map((student) => student.id),
      other.students.map((student) => student.id),
    );
  });

  it("generates the requested number of students, within the allowed range", () => {
    const universe = generateUniverse();
    assert.equal(universe.students.length, 3000);
    assert.ok(universe.students.length >= MIN_STUDENT_COUNT);
    assert.ok(universe.students.length <= MAX_STUDENT_COUNT);
    assert.equal(new Set(universe.students.map((s) => s.id)).size, 3000);
    assert.equal(new Set(universe.students.map((s) => s.externalRef)).size, 3000);
    assert.throws(() => generateUniverse({ studentCount: 10 }), RangeError);
    assert.throws(() => generateUniverse({ studentCount: 99_999 }), RangeError);
  });

  it("generates three thousand students well inside the time budget", () => {
    const started = performance.now();
    generateUniverse();
    const elapsed = performance.now() - started;
    // The real target is "well under 2 seconds"; the assertion is loose so a
    // busy machine cannot turn a performance note into a red build.
    assert.ok(elapsed < 5_000, `generation took ${elapsed.toFixed(0)}ms`);
  });

  it("marks everything as synthetic", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    assert.equal(universe.meta.isSynthetic, true);
    for (const student of universe.students) {
      assert.equal(student.isSynthetic, true);
      assert.ok(student.externalRef.startsWith("SYN-"));
      assert.ok(student.email.endsWith("@synthetic.aster.example"));
    }
  });

  it("regenerates cleanly twice in one process", () => {
    const baseline = JSON.stringify(generateUniverse({ studentCount: SMALL }));

    // Mutate a generated universe as hard as a caller plausibly might: if any
    // catalogue were shared between generations, the next one would inherit it.
    const scribbled = generateUniverse({ studentCount: SMALL });
    scribbled.programs[0].name = "MUTATED";
    scribbled.terms.push({ id: "junk", code: "JUNK" });
    scribbled.students[0].email = "mutated@example.invalid";
    scribbled.students.length = 5;
    scribbled.holdTypes[0].blocksRegistration = false;
    scribbled.personas[0].headline = "MUTATED";

    const after = generateUniverse({ studentCount: SMALL });
    assert.equal(JSON.stringify(after), baseline);
    assert.notEqual(after.programs[0].name, "MUTATED");
    assert.equal(after.terms.length, 3);
    assert.equal(after.holdTypes[0].blocksRegistration, true);
    assert.equal(after.students.length, SMALL);
  });
});

describe("synthetic university invariants", () => {
  it("accepts the default universe", () => {
    const violations = validateUniverse(generateUniverse());
    assert.deepEqual(violations, [], violations.slice(0, 10).join("\n"));
  });

  it("accepts universes built from other seeds and sizes", () => {
    for (const seed of [1, 777, 20_270_101]) {
      const violations = validateUniverse(
        generateUniverse({ seed, studentCount: SMALL }),
      );
      assert.deepEqual(violations, [], `seed ${seed}: ${violations.slice(0, 5).join("\n")}`);
    }
  });

  it("catches a disbursement against an award that was never accepted", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    const declined = universe.aidAwards.find((award) => award.status === "declined");
    assert.ok(declined, "expected the population to contain a declined award");
    universe.disbursements.push({
      id: "corrupt-disbursement",
      studentId: declined.studentId,
      awardId: declined.id,
      term: universe.terms[0].code,
      amountUsd: 1_000,
      scheduledFor: "2026-08-20T12:00:00.000Z",
      disbursedAt: null,
      status: "scheduled",
      holdReason: null,
      isSynthetic: true,
    });
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some(
        (message) =>
          message.includes("corrupt-disbursement") && message.includes("declined"),
      ),
      violations.join("\n"),
    );
  });

  it("catches an orphaned foreign key", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    universe.holds.push({
      id: "corrupt-hold",
      studentId: "00000000-0000-4000-8000-000000000000",
      holdType: "financial",
      label: "Orphan",
      reason: "Orphan",
      placedAt: "2026-07-01T12:00:00.000Z",
      releasedAt: null,
      blocksRegistration: true,
      blocksTranscript: true,
      resolutionOffice: "Office of Student Accounts",
      isSynthetic: true,
    });
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some((message) => message.includes("corrupt-hold")),
      violations.join("\n"),
    );
  });

  it("catches a housing assignment with no posted enrolment deposit", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    const assignment = universe.housingAssignments[0];
    assert.ok(assignment);
    universe.accountLedger = universe.accountLedger.filter(
      (entry) =>
        !(entry.studentId === assignment.studentId && entry.code === "enrollment_deposit"),
    );
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some(
        (message) =>
          message.includes(assignment.id) && message.includes("enrolment deposit"),
      ),
      violations.join("\n"),
    );
  });

  it("catches a verification requirement without a selected FAFSA", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    const requirement = universe.verificationRequirements[0];
    assert.ok(requirement);
    const fafsa = universe.fafsaRecords.find(
      (record) => record.studentId === requirement.studentId,
    );
    fafsa.status = "verification_complete";
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some((message) => message.includes(requirement.id)),
      violations.join("\n"),
    );
  });

  it("catches an accepted amount above the offered amount", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    const award = universe.aidAwards.find((entry) => entry.status === "accepted");
    award.acceptedAmountUsd = award.offeredAmountUsd + 1;
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some((message) => message.includes(award.id)),
      violations.join("\n"),
    );
  });

  it("catches aid credits that exceed what was disbursed", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    const credit = universe.accountLedger.find((entry) => entry.type === "aid_credit");
    assert.ok(credit);
    credit.amountUsd -= 10_000;
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some((message) => message.includes("of aid credit against")),
      violations.join("\n"),
    );
  });

  it("catches records attached to a denied applicant", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    const denied = universe.applications.find((entry) => entry.decision === "denied");
    assert.ok(denied, "expected the population to contain a denied applicant");
    universe.aidAwards.push({
      id: "corrupt-award",
      studentId: denied.studentId,
      aidYear: "2026-2027",
      awardType: "grant",
      fundCode: "PELL",
      name: "Federal Pell Grant",
      source: "federal",
      offeredAmountUsd: 5_000,
      acceptedAmountUsd: 5_000,
      status: "accepted",
      isEstimated: false,
      requiresPromissoryNote: false,
      requiresEntranceCounseling: false,
      offeredAt: "2026-04-01T12:00:00.000Z",
      isSynthetic: true,
    });
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some(
        (message) => message.includes("corrupt-award") && message.includes("denied"),
      ),
      violations.join("\n"),
    );
  });

  it("catches a status history that runs backwards or disagrees with the status", () => {
    const backwards = generateUniverse({ studentCount: SMALL });
    const multiStep = backwards.documents.find(
      (document) => document.statusHistory.length >= 3,
    );
    assert.ok(multiStep);
    multiStep.statusHistory[1].at = "2000-01-01T00:00:00.000Z";
    assert.ok(
      validateUniverse(backwards).some(
        (message) => message.includes(multiStep.id) && message.includes("backwards"),
      ),
    );

    const mismatched = generateUniverse({ studentCount: SMALL });
    const target = mismatched.documents.find(
      (document) => document.status === "ACCEPTED",
    );
    target.status = "REJECTED";
    assert.ok(
      validateUniverse(mismatched).some(
        (message) => message.includes(target.id) && message.includes("last history entry"),
      ),
    );
  });

  it("catches a cost-of-attendance total that does not match its components", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    universe.costOfAttendance[0].totalUsd += 1;
    const violations = validateUniverse(universe);
    assert.ok(
      violations.some((message) => message.includes("components sum to")),
      violations.join("\n"),
    );
  });
});

describe("synthetic university state coverage", () => {
  it("populates every state in the matrix", () => {
    const universe = generateUniverse();
    for (const entry of STATE_MATRIX) {
      const matching = studentsByStateKey(universe, entry.key);
      assert.ok(
        matching.length > 0,
        `no student was assigned the "${entry.key}" state`,
      );
    }
  });

  it("populates every state even at the smallest allowed population", () => {
    const universe = generateUniverse({ studentCount: SMALL });
    for (const entry of STATE_MATRIX) {
      assert.ok(
        studentsByStateKey(universe, entry.key).length > 0,
        `no student was assigned the "${entry.key}" state`,
      );
    }
  });

  it("uses unique kebab-case keys", () => {
    const keys = STATE_MATRIX.map((entry) => entry.key);
    assert.equal(new Set(keys).size, keys.length);
    for (const key of keys) assert.match(key, /^[a-z0-9]+(-[a-z0-9]+)*$/);
  });
});

describe("synthetic university personas", () => {
  it("defines exactly ten personas with unique keys and ids", () => {
    assert.equal(SYNTHETIC_PERSONAS.length, 10);
    assert.equal(new Set(SYNTHETIC_PERSONAS.map((p) => p.key)).size, 10);
    assert.equal(new Set(SYNTHETIC_PERSONAS.map((p) => p.studentId)).size, 10);
    for (const persona of SYNTHETIC_PERSONAS) {
      assert.match(persona.key, /^[a-z0-9]+(-[a-z0-9]+)*$/);
      assert.match(persona.externalRef, /^SYN-\d{6}$/);
      assert.ok(persona.interestingQuestions.length >= 3);
      assert.ok(persona.interestingQuestions.length <= 5);
      assert.ok(persona.scenario.length > 120);
    }
  });

  it("resolves every persona to a real student in the universe", () => {
    const universe = generateUniverse();
    const byId = new Map(universe.students.map((student) => [student.id, student]));
    for (const persona of SYNTHETIC_PERSONAS) {
      const student = byId.get(persona.studentId);
      assert.ok(student, `persona "${persona.key}" has no student`);
      assert.equal(student.externalRef, persona.externalRef);
      assert.equal(student.firstName, persona.firstName);
      assert.equal(student.lastName, persona.lastName);
      assert.equal(student.personaKey, persona.key);
      assert.deepEqual(student.stateKeys, [...persona.stateKeys]);
    }
  });

  it("keeps persona ids stable across seeds and sizes", () => {
    const other = generateUniverse({ seed: 999, studentCount: SMALL });
    const ids = new Set(other.students.map((student) => student.id));
    for (const persona of SYNTHETIC_PERSONAS) {
      assert.ok(ids.has(persona.studentId), `persona "${persona.key}" moved`);
    }
  });

  it("references only state keys the matrix actually defines", () => {
    const known = new Set(STATE_MATRIX.map((entry) => entry.key));
    for (const persona of SYNTHETIC_PERSONAS) {
      for (const key of persona.stateKeys) {
        assert.ok(known.has(key), `persona "${persona.key}" uses unknown state "${key}"`);
      }
    }
  });

  it("backs each persona's headline claim with the data", () => {
    const universe = generateUniverse();
    const rowsFor = (collection, studentId) =>
      universe[collection].filter((row) => row.studentId === studentId);
    const balanceFor = (studentId) =>
      rowsFor("accountLedger", studentId).reduce((sum, row) => sum + row.amountUsd, 0);
    const find = (key) =>
      SYNTHETIC_PERSONAS.find((persona) => persona.key === key).studentId;

    // Clean and complete: nothing outstanding anywhere.
    const clean = find("clean-and-complete");
    assert.equal(rowsFor("holds", clean).length, 0);
    assert.equal(balanceFor(clean), 0);
    assert.equal(rowsFor("housingAssignments", clean).length, 1);
    assert.ok(rowsFor("documents", clean).every((d) => d.status === "ACCEPTED"));
    assert.ok(rowsFor("aidAwards", clean).every((a) => a.isEstimated === false));
    assert.equal(rowsFor("registrationEligibility", clean)[0].eligible, true);

    // Missing documents: nothing was ever received for two categories.
    const missing = find("missing-documents");
    const missingDocs = rowsFor("documents", missing).filter((d) =>
      ["transcript", "immunization"].includes(d.category),
    );
    assert.equal(missingDocs.length, 2);
    assert.ok(missingDocs.every((d) => d.status === "NOT_SUBMITTED"));
    assert.ok(missingDocs.every((d) => d.statusHistory.length === 1));
    assert.equal(rowsFor("holds", missing).length, 1);

    // Under review and rejected are distinct terminal states.
    const underReview = rowsFor("documents", find("document-under-review")).find(
      (d) => d.category === "transcript",
    );
    assert.equal(underReview.status, "UNDER_REVIEW");
    assert.ok(underReview.submittedAt);
    const rejected = rowsFor("documents", find("document-rejected")).find(
      (d) => d.category === "transcript",
    );
    assert.equal(rejected.status, "REJECTED");

    // Multiple holds from more than one office, plus a real balance.
    const holdsPersona = find("multiple-holds-blocked");
    const holds = rowsFor("holds", holdsPersona);
    assert.equal(holds.length, 3);
    assert.equal(new Set(holds.map((hold) => hold.holdType)).size, 3);
    assert.ok(balanceFor(holdsPersona) > 0);
    assert.ok(rowsFor("aidAwards", holdsPersona).every((a) => a.isEstimated === false));

    // No aid at all: not a single record in the aid tables.
    const noAid = find("no-aid-self-pay");
    assert.equal(rowsFor("fafsaRecords", noAid).length, 0);
    assert.equal(rowsFor("aidAwards", noAid).length, 0);
    assert.equal(rowsFor("disbursements", noAid).length, 0);
    assert.equal(rowsFor("verificationRequirements", noAid).length, 0);
    assert.ok(balanceFor(noAid) > 0);

    // Refund due: a credit balance backed by money that actually moved.
    const refund = find("disbursed-with-refund");
    const disbursed = rowsFor("disbursements", refund);
    assert.ok(disbursed.length > 0);
    assert.ok(disbursed.every((d) => d.status === "disbursed"));
    assert.ok(balanceFor(refund) < 0);
    assert.equal(
      rowsFor("accountLedger", refund).filter((row) => row.type === "refund").length,
      0,
    );

    // International: immigration requirements and a check-in gate the others lack.
    const international = find("international-check-in");
    assert.equal(universe.students.find((s) => s.id === international).residency, "international");
    assert.equal(rowsFor("internationalRequirements", international).length, 4);
    assert.ok(
      rowsFor("registrationEligibility", international)[0].gates.some(
        (gate) => gate.code === "international_check_in",
      ),
    );
    assert.equal(rowsFor("fafsaRecords", international)[0].status, "selected_for_verification");
    assert.ok(rowsFor("verificationRequirements", international).length > 0);

    // Transfer: admit type plus a transcript still being evaluated.
    const transfer = find("transfer-credit-review");
    assert.equal(universe.students.find((s) => s.id === transfer).admitType, "transfer");
    assert.equal(
      rowsFor("documents", transfer).find((d) => d.category === "transcript").status,
      "UNDER_REVIEW",
    );

    // Deadline passed: one root cause with several visible symptoms.
    const late = find("deposit-deadline-passed");
    assert.ok(
      Date.parse(rowsFor("applications", late)[0].depositDeadline) <
        Date.parse(universe.meta.generatedFor),
    );
    assert.equal(rowsFor("housingApplications", late).length, 0);
    assert.equal(rowsFor("housingAssignments", late).length, 0);
    assert.equal(rowsFor("registrationEligibility", late)[0].eligible, false);
    assert.equal(
      rowsFor("registrationEligibility", late)[0].gates.find(
        (gate) => gate.code === "deposit_posted",
      ).satisfied,
      false,
    );
  });
});
