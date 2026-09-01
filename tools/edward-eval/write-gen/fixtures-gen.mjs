/**
 * Fixtures for the generalization suite, merged over the regression suite's.
 *
 * Everything here is resolved by external reference against the same eval
 * database. The additional students are chosen for properties the base
 * fixtures do not exercise: same-full-name pairs on one caseload, rejected
 * documents, unpaid deposits, an already-set preferred name, and students on
 * the *other* adviser's caseload for scope contrasts.
 */

import { STAFF as BASE_STAFF, STUDENTS as BASE_STUDENTS, WORK_ITEMS as BASE_ITEMS } from "../write/fixtures.mjs";

export const STAFF = { ...BASE_STAFF };

export const STUDENTS = {
  ...BASE_STUDENTS,
  // --- additional caseload students of STAFF.adviser (SYN-ADV-001) ---------
  // Rejected transcript, December — the "gone stale" review case.
  mossbankRosa: { ref: "SYN-000354", name: "Rosa Mossbank", adviser: "adviser" },
  glimmerlyTessa: { ref: "SYN-000762", name: "Tessa Glimmerly", adviser: "adviser" },
  stonebrookZara: { ref: "SYN-002118", name: "Zara Stonebrook", adviser: "adviser" },
  ravensworthHelia: { ref: "SYN-002079", name: "Helia Ravensworth", adviser: "adviser" },
  // Rejected health/immunization documents.
  ashgroveNadia: { ref: "SYN-002083", name: "Nadia Ashgrove", adviser: "adviser" },
  netherbyMilo: { ref: "SYN-001086", name: "Milo Netherby", adviser: "adviser" },
  jessamyBianca: { ref: "SYN-001837", name: "Bianca Jessamy", adviser: "adviser" },
  stonebrookCaleb: { ref: "SYN-002762", name: "Caleb Stonebrook", adviser: "adviser" },
  ironwoodHana: { ref: "SYN-000952", name: "Hana Ironwood", adviser: "adviser" },
  // Same full name, same caseload — every bare mention is ambiguous.
  everlynHana1: { ref: "SYN-000524", name: "Hana Everlyn", adviser: "adviser" },
  everlynHana2: { ref: "SYN-001943", name: "Hana Everlyn", adviser: "adviser" },
  calderwoodElena1: { ref: "SYN-001882", name: "Elena Calderwood", adviser: "adviser" },
  calderwoodElena2: { ref: "SYN-002996", name: "Elena Calderwood", adviser: "adviser" },
  hallowayKaito1: { ref: "SYN-000443", name: "Kaito Halloway", adviser: "adviser" },
  // The Kaito Halloway with the rejected transcript.
  hallowayKaito2: { ref: "SYN-002253", name: "Kaito Halloway", adviser: "adviser" },
  // "The other Ada" — three Adas share the caseload with kettleby/ravensworth.
  larkspurAda: { ref: "SYN-002719", name: "Ada Larkspur", adviser: "adviser" },
  // Ordinary caseload students used as targets and student actors.
  kettlebyEmre: { ref: "SYN-002875", name: "Emre Kettleby", adviser: "adviser" },
  everlynYusuf: { ref: "SYN-001814", name: "Yusuf Everlyn", adviser: "adviser" },
  pemberwellKaito: { ref: "SYN-000428", name: "Kaito Pemberwell", adviser: "adviser" },
  pemberwellAnton: { ref: "SYN-000831", name: "Anton Pemberwell", adviser: "adviser" },
  mossbankVera: { ref: "SYN-000426", name: "Vera Mossbank", adviser: "adviser" },
  calderwoodFiona: { ref: "SYN-001750", name: "Fiona Calderwood", adviser: "adviser" },
  larkspurQuentin: { ref: "SYN-001820", name: "Quentin Larkspur", adviser: "adviser" },
  oakenshawBruno: { ref: "SYN-000655", name: "Bruno Oakenshaw", adviser: "adviser" },
  brightwaterLiora: { ref: "SYN-000628", name: "Liora Brightwater", adviser: "adviser" },
  zephyrineDmitri: { ref: "SYN-002244", name: "Dmitri Zephyrine", adviser: "adviser" },
  quillfeatherCamila: { ref: "SYN-000540", name: "Camila Quillfeather", adviser: "adviser" },
  hallowayDelphine: { ref: "SYN-000110", name: "Delphine Halloway", adviser: "adviser" },
  // On the OTHER adviser's caseload (SYN-ADV-012) — scope denials.
  larkspurFiona: { ref: "SYN-000052", name: "Fiona Larkspur", adviser: "adviserOther" },
  stonebrookOmar: { ref: "SYN-000038", name: "Omar Stonebrook", adviser: "adviserOther" },
  // Class-of-2029 deposit-unpaid cohort members for spot checks.
  cohortFarid: { ref: "SYN-000174", name: "Farid Stonebrook", adviser: null },
  cohortNadiaIronwood: { ref: "SYN-002854", name: "Nadia Ironwood", adviser: null },
};

export const WORK_ITEMS = {
  ...BASE_ITEMS,
  // Adviser-owned open items not used by the regression bank's fixtures.
  ownBlockedThird: "AST-00900", // blocked, medium — Jolene Quillfeather
  ownBlockedFourth: "AST-00507", // blocked, medium — Elena Calderwood (SYN-001882)
  ownInProgressThird: "AST-01481", // in_progress, medium — Jolene Yarrowby
  // Petra Yarrowby's open item, owned by an admissions counselor (not the
  // adviser) — the duplicate-check case.
  petraExisting: "AST-02018",
};

/** Deterministic fixture email address for a student row. */
export function fixtureEmail(student) {
  const [first, ...rest] = student.name.toLowerCase().split(" ");
  return `${first}.${rest.join("")}.${student.ref.toLowerCase()}@students.aster.example`;
}
