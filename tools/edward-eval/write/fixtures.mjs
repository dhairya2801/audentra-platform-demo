/**
 * Named actors and referenced records for the Edward write suite.
 *
 * Cases refer to people by these keys, never by UUID: the university is
 * regenerated from seeds, so an external reference (`SYN-…`) is the stable
 * identity and the UUID is whatever that run produced. `resolveFixtures`
 * turns the references into ids, names and live record state once per run.
 */

/** Staff actors, by `staff_member.external_ref`. */
export const STAFF = {
  // 90 advisees, 14 open items. Narrow scope: may act only on her own
  // caseload and her own work items. The default actor.
  adviser: {
    ref: "SYN-ADV-001",
    role: "academic_adviser",
    component: "Academic Advising",
    broad: false,
  },
  // Second adviser, used to prove one adviser cannot act on another's student.
  adviserOther: {
    ref: "SYN-ADV-012",
    role: "academic_adviser",
    component: "Academic Advising",
    broad: false,
  },
  // Director of Academic Advising: `edward.student.any` +
  // `edward.cohort.follow_up.create`. The only actor that may act in bulk.
  director: {
    ref: "SYN-STF-ADV-DIR",
    role: "director",
    component: "Academic Advising",
    broad: true,
  },
  // VP of Enrollment. Seeded WITHOUT broad scope — the capability migration
  // grants it by role_code and `vp` is not on that list. Cases assert what
  // the tenant actually grants, and the report calls the gap out.
  vp: {
    ref: "SYN-STF-VP",
    role: "vp",
    component: "Enrollment Leadership",
    broad: false,
  },
  // Health Records: 231 open items, no caseload. Component scope only.
  healthRecords: {
    ref: "SYN-STF-SHS-REC1",
    role: "health_records",
    component: "Student Health",
    broad: false,
  },
};

/**
 * Students, by `student.external_ref`. `adviser` names the staff key whose
 * caseload they are on — the difference between an allowed and a denied
 * target for a narrow-scope actor.
 */
export const STUDENTS = {
  ashgrove: { ref: "SYN-000386", name: "Hana Ashgrove", adviser: "adviser" },
  kettleby: { ref: "SYN-000061", name: "Ada Kettleby", adviser: "adviser" },
  pemberwell: { ref: "SYN-000098", name: "Elena Pemberwell", adviser: "adviser" },
  everlyn: { ref: "SYN-000554", name: "Elena Everlyn", adviser: "adviser" },
  jessamy: { ref: "SYN-001041", name: "Kaito Jessamy", adviser: "adviser" },
  whitlowe: { ref: "SYN-001236", name: "Tessa Whitlowe", adviser: "adviser" },
  yarrowby: { ref: "SYN-002533", name: "Petra Yarrowby", adviser: "adviser" },
  oakenshaw: { ref: "SYN-002844", name: "Greta Oakenshaw", adviser: "adviser" },
  ravensworth: { ref: "SYN-002986", name: "Ada Ravensworth", adviser: "adviser" },
  underhollow: { ref: "SYN-000009", name: "Georgina Underhollow", adviser: "adviser" },
  // The one Caleb Dunmire (of eight) on STAFF.adviser's caseload. Added
  // 2026-09-01 when write-action target resolution became caseload-aware:
  // w-fup-011 now pins that the adviser's "Caleb Dunmire" binds exactly this
  // student rather than asking which of eight (seven of whom she cannot act
  // on anyway).
  dunmireCaleb: { ref: "SYN-002584", name: "Caleb Dunmire", adviser: "adviser" },
  // No primary adviser at all: in scope only for an actor holding
  // `edward.student.any`, and the natural target for a director's cases.
  unassignedA: { ref: "SYN-000007", name: "Ines Calderwood", adviser: null },
  unassignedB: { ref: "SYN-000033", name: "Liora Glimmerly", adviser: null },
  unassignedC: { ref: "SYN-000174", name: "Farid Stonebrook", adviser: null },
};

/** A name shared by eight students — every mention is ambiguous. */
export const AMBIGUOUS_NAME = "Caleb Dunmire";

/** Open work items owned by `STAFF.adviser`, by `staff_work_item.key`. */
export const WORK_ITEMS = {
  ownTodo: "AST-00183", // todo, medium
  ownBlocked: "AST-00456", // blocked, high
  ownInProgress: "AST-00006", // in_progress, medium
  ownTodoHigh: "AST-00533", // todo, high
  ownTodoSecond: "AST-01070", // todo, high
  ownTodoThird: "AST-01344", // todo, medium
  ownTodoFourth: "AST-01641", // todo, medium
  ownTodoFifth: "AST-01794", // todo, medium
  ownTodoSixth: "AST-01936", // todo, medium
  ownInProgressSecond: "AST-02441", // in_progress, medium
  ownBlockedSecond: "AST-01482", // blocked, medium
  // Student Health, owned by someone else: outside an adviser's scope on
  // every axis (not her assignment, not her component, not her item).
  foreign: "AST-00001",
};

/** The eval-only shared mailbox seeded by `fixture.sql`. */
export const MAILBOX_ADDRESS = "advising@synthetic.aster.example";
