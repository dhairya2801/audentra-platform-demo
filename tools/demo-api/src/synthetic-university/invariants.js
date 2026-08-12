/**
 * Consistency rules for a generated universe.
 *
 * `generate.js` builds these relationships correctly by construction, so in
 * normal operation `validateUniverse` returns an empty array. It earns its
 * keep in two places: as a regression net when the generator changes, and as
 * an executable statement of what the data actually promises. Anything an
 * assistant is allowed to infer from this data — a disbursement means an award
 * was accepted, a room means a deposit cleared — has to be checked here, or it
 * is not a promise at all.
 *
 * Violations come back as human-readable strings rather than objects: they are
 * read by people and printed by tests, and a structured type would only get in
 * the way.
 */

import { DOCUMENT_STATUSES } from "./generate.js";

const LEDGER_TYPES = new Set(["charge", "payment", "aid_credit", "refund"]);
const AWARD_STATUSES = new Set(["offered", "accepted", "declined", "cancelled", "pending"]);
const DISBURSEMENT_STATUSES = new Set(["scheduled", "disbursed", "held"]);

/**
 * Groups rows by their `studentId` in one pass, so the per-student checks stay
 * linear in the size of the universe rather than quadratic.
 *
 * @param {readonly {studentId: string}[]} rows
 * @returns {Map<string, any[]>}
 */
function groupByStudent(rows) {
  const grouped = new Map();
  for (const row of rows) {
    const existing = grouped.get(row.studentId);
    if (existing) existing.push(row);
    else grouped.set(row.studentId, [row]);
  }
  return grouped;
}

/**
 * @param {object} universe as returned by `generateUniverse`
 * @returns {string[]} violations; an empty array means the universe is valid
 */
export function validateUniverse(universe) {
  /** @type {string[]} */
  const violations = [];
  const report = (message) => violations.push(message);

  if (!universe || typeof universe !== "object") {
    return ["universe is not an object"];
  }

  const required = [
    "terms", "programs", "advisors", "courses", "sections", "residenceHalls",
    "mealPlans", "holdTypes", "checklistTaskCatalogue", "documentCategories",
    "awardFunds", "orientationSessions", "costOfAttendance", "students",
    "applications", "checklistTasks", "documents", "holds", "fafsaRecords",
    "verificationRequirements", "aidAwards", "disbursements", "sapStatus",
    "accountLedger", "housingApplications", "housingAssignments",
    "orientationRegistrations", "registrationEligibility",
    "internationalRequirements",
  ];
  for (const collection of required) {
    if (!Array.isArray(universe[collection])) {
      report(`collection "${collection}" is missing or is not an array`);
    }
  }
  if (violations.length) return violations;

  /* ---------------------------------------------------------------- */
  /* Reference sets                                                    */
  /* ---------------------------------------------------------------- */

  const studentIds = new Set(universe.students.map((student) => student.id));
  const studentById = new Map(universe.students.map((student) => [student.id, student]));
  const termCodes = new Set(universe.terms.map((term) => term.code));
  const programCodes = new Set(universe.programs.map((program) => program.code));
  const advisorIds = new Set(universe.advisors.map((advisor) => advisor.id));
  const courseIds = new Set(universe.courses.map((course) => course.id));
  const hallIds = new Set(universe.residenceHalls.map((hall) => hall.id));
  const mealPlanCodes = new Set(universe.mealPlans.map((plan) => plan.code));
  const holdTypeCodes = new Set(universe.holdTypes.map((type) => type.code));
  const taskCodes = new Set(universe.checklistTaskCatalogue.map((task) => task.code));
  const documentCategoryCodes = new Set(
    universe.documentCategories.map((category) => category.code),
  );
  const fundCodes = new Set(universe.awardFunds.map((fund) => fund.fundCode));
  const sessionIds = new Set(universe.orientationSessions.map((session) => session.id));
  const awardById = new Map(universe.aidAwards.map((award) => [award.id, award]));
  const housingApplicationIds = new Set(
    universe.housingApplications.map((application) => application.id),
  );
  const documentStatuses = new Set(DOCUMENT_STATUSES);

  /** Reports one violation per offending row, naming the row's own id. */
  const checkFk = (rows, label, field, allowed, allowNull = false) => {
    for (const row of rows) {
      const value = row[field];
      if (value === null || value === undefined) {
        if (!allowNull) report(`${label} ${row.id} has a null ${field}`);
        continue;
      }
      if (!allowed.has(value)) {
        report(`${label} ${row.id} references unknown ${field} "${value}"`);
      }
    }
  };

  /* ---------------------------------------------------------------- */
  /* Foreign keys                                                      */
  /* ---------------------------------------------------------------- */

  checkFk(universe.students, "student", "programCode", programCodes);
  checkFk(universe.students, "student", "advisorId", advisorIds);
  checkFk(universe.students, "student", "cohort", termCodes);
  checkFk(universe.sections, "section", "courseId", courseIds);
  checkFk(universe.sections, "section", "termCode", termCodes);

  const studentScoped = [
    ["application", universe.applications],
    ["checklist task", universe.checklistTasks],
    ["document", universe.documents],
    ["hold", universe.holds],
    ["FAFSA record", universe.fafsaRecords],
    ["verification requirement", universe.verificationRequirements],
    ["aid award", universe.aidAwards],
    ["disbursement", universe.disbursements],
    ["SAP status", universe.sapStatus],
    ["ledger entry", universe.accountLedger],
    ["housing application", universe.housingApplications],
    ["housing assignment", universe.housingAssignments],
    ["orientation registration", universe.orientationRegistrations],
    ["registration eligibility", universe.registrationEligibility],
    ["international requirement", universe.internationalRequirements],
  ];
  for (const [label, rows] of studentScoped) {
    checkFk(rows, label, "studentId", studentIds);
  }

  checkFk(universe.applications, "application", "termCode", termCodes);
  checkFk(universe.checklistTasks, "checklist task", "taskCode", taskCodes);
  checkFk(universe.documents, "document", "category", documentCategoryCodes);
  checkFk(universe.holds, "hold", "holdType", holdTypeCodes);
  checkFk(universe.aidAwards, "aid award", "fundCode", fundCodes);
  checkFk(universe.disbursements, "disbursement", "awardId", new Set(awardById.keys()));
  checkFk(universe.disbursements, "disbursement", "term", termCodes);
  checkFk(universe.accountLedger, "ledger entry", "term", termCodes, true);
  checkFk(universe.housingApplications, "housing application", "termCode", termCodes);
  checkFk(universe.housingAssignments, "housing assignment", "applicationId", housingApplicationIds);
  checkFk(universe.housingAssignments, "housing assignment", "hallId", hallIds);
  checkFk(universe.housingAssignments, "housing assignment", "mealPlanCode", mealPlanCodes);
  checkFk(universe.orientationRegistrations, "orientation registration", "sessionId", sessionIds);
  checkFk(universe.registrationEligibility, "registration eligibility", "termCode", termCodes);

  for (const entry of universe.accountLedger) {
    if (!LEDGER_TYPES.has(entry.type)) {
      report(`ledger entry ${entry.id} has unknown type "${entry.type}"`);
    }
  }
  for (const award of universe.aidAwards) {
    if (!AWARD_STATUSES.has(award.status)) {
      report(`aid award ${award.id} has unknown status "${award.status}"`);
    }
  }
  for (const disbursement of universe.disbursements) {
    if (!DISBURSEMENT_STATUSES.has(disbursement.status)) {
      report(`disbursement ${disbursement.id} has unknown status "${disbursement.status}"`);
    }
  }

  /* ---------------------------------------------------------------- */
  /* Aid                                                               */
  /* ---------------------------------------------------------------- */

  // A disbursement is money moving against an award the student accepted.
  // Anything else means the package and the cashiering system disagree.
  for (const disbursement of universe.disbursements) {
    const award = awardById.get(disbursement.awardId);
    if (!award) continue;
    if (award.status !== "accepted") {
      report(
        `disbursement ${disbursement.id} exists for award ${award.id} whose status is "${award.status}", not "accepted"`,
      );
    }
    if (disbursement.studentId !== award.studentId) {
      report(
        `disbursement ${disbursement.id} belongs to student ${disbursement.studentId} but its award belongs to ${award.studentId}`,
      );
    }
  }

  for (const award of universe.aidAwards) {
    if (award.acceptedAmountUsd === null || award.acceptedAmountUsd === undefined) continue;
    if (award.acceptedAmountUsd > award.offeredAmountUsd) {
      report(
        `aid award ${award.id} accepts $${award.acceptedAmountUsd} against an offer of $${award.offeredAmountUsd}`,
      );
    }
  }

  const fafsaByStudent = new Map(
    universe.fafsaRecords.map((record) => [record.studentId, record]),
  );
  for (const requirement of universe.verificationRequirements) {
    const fafsa = fafsaByStudent.get(requirement.studentId);
    if (!fafsa) {
      report(
        `verification requirement ${requirement.id} exists for student ${requirement.studentId} who has no FAFSA record`,
      );
    } else if (fafsa.status !== "selected_for_verification") {
      report(
        `verification requirement ${requirement.id} exists for student ${requirement.studentId} whose FAFSA status is "${fafsa.status}"`,
      );
    }
  }

  /* ---------------------------------------------------------------- */
  /* Ledger and housing                                                */
  /* ---------------------------------------------------------------- */

  const ledgerByStudent = groupByStudent(universe.accountLedger);
  const disbursementsByStudent = groupByStudent(universe.disbursements);

  for (const assignment of universe.housingAssignments) {
    const entries = ledgerByStudent.get(assignment.studentId) ?? [];
    const deposited = entries.some(
      (entry) => entry.type === "payment" && entry.code === "enrollment_deposit",
    );
    if (!deposited) {
      report(
        `housing assignment ${assignment.id} exists for student ${assignment.studentId} whose enrolment deposit has not posted to the ledger`,
      );
    }
  }

  for (const [studentId, entries] of ledgerByStudent) {
    const creditTotal = entries
      .filter((entry) => entry.type === "aid_credit")
      .reduce((sum, entry) => sum - entry.amountUsd, 0);
    const disbursedTotal = (disbursementsByStudent.get(studentId) ?? [])
      .filter((disbursement) => disbursement.status === "disbursed")
      .reduce((sum, disbursement) => sum + disbursement.amountUsd, 0);
    if (creditTotal > disbursedTotal) {
      report(
        `student ${studentId} carries $${creditTotal} of aid credit against $${disbursedTotal} of disbursed aid`,
      );
    }
    for (const entry of entries) {
      if (entry.type === "charge" && entry.amountUsd < 0) {
        report(`ledger charge ${entry.id} has a negative amount ${entry.amountUsd}`);
      }
      if ((entry.type === "payment" || entry.type === "aid_credit") && entry.amountUsd > 0) {
        report(`ledger credit ${entry.id} has a positive amount ${entry.amountUsd}`);
      }
    }
  }

  /* ---------------------------------------------------------------- */
  /* Denied applicants                                                 */
  /* ---------------------------------------------------------------- */

  const deniedStudentIds = new Set(
    universe.applications
      .filter((application) => application.decision === "denied")
      .map((application) => application.studentId),
  );
  const forbiddenForDenied = [
    ["housing application", universe.housingApplications],
    ["housing assignment", universe.housingAssignments],
    ["registration eligibility", universe.registrationEligibility],
    ["aid award", universe.aidAwards],
    ["disbursement", universe.disbursements],
    ["FAFSA record", universe.fafsaRecords],
    ["verification requirement", universe.verificationRequirements],
  ];
  for (const [label, rows] of forbiddenForDenied) {
    for (const row of rows) {
      if (deniedStudentIds.has(row.studentId)) {
        report(
          `${label} ${row.id} exists for student ${row.studentId} whose application was denied`,
        );
      }
    }
  }

  /* ---------------------------------------------------------------- */
  /* Document status history                                           */
  /* ---------------------------------------------------------------- */

  for (const document of universe.documents) {
    if (!documentStatuses.has(document.status)) {
      report(`document ${document.id} has unknown status "${document.status}"`);
    }
    if (!Array.isArray(document.statusHistory) || document.statusHistory.length === 0) {
      report(`document ${document.id} has an empty status history`);
      continue;
    }
    let previous = Number.NEGATIVE_INFINITY;
    for (const entry of document.statusHistory) {
      const at = Date.parse(entry.at);
      if (Number.isNaN(at)) {
        report(`document ${document.id} has a status history entry with an unparseable timestamp "${entry.at}"`);
        break;
      }
      if (at < previous) {
        report(`document ${document.id} has a status history that runs backwards at ${entry.at}`);
        break;
      }
      previous = at;
    }
    const last = document.statusHistory[document.statusHistory.length - 1];
    if (last.status !== document.status) {
      report(
        `document ${document.id} is "${document.status}" but its last history entry is "${last.status}"`,
      );
    }
  }

  /* ---------------------------------------------------------------- */
  /* Cost of attendance                                                */
  /* ---------------------------------------------------------------- */

  for (const row of universe.costOfAttendance) {
    const components =
      row.tuitionUsd + row.feesUsd + row.housingUsd + row.mealPlanUsd + row.booksUsd + row.personalUsd;
    if (components !== row.totalUsd) {
      report(
        `cost of attendance ${row.cohort}/${row.residency} totals $${row.totalUsd} but its components sum to $${components}`,
      );
    }
    if (!termCodes.has(row.cohort)) {
      report(`cost of attendance ${row.id} references unknown cohort "${row.cohort}"`);
    }
  }

  /* ---------------------------------------------------------------- */
  /* One application per student, one student per persona              */
  /* ---------------------------------------------------------------- */

  const applicationsByStudent = groupByStudent(universe.applications);
  for (const student of universe.students) {
    const applications = applicationsByStudent.get(student.id) ?? [];
    if (applications.length !== 1) {
      report(`student ${student.id} has ${applications.length} applications; expected exactly 1`);
    }
  }
  for (const persona of universe.personas ?? []) {
    if (!studentById.has(persona.studentId)) {
      report(`persona "${persona.key}" points at student ${persona.studentId}, who does not exist`);
    }
  }

  return violations;
}
