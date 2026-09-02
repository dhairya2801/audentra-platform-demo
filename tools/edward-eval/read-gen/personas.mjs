/**
 * Personas for the READ generalization suite.
 *
 * Every persona is a real row in the frozen aster-demo snapshot, chosen with
 * SQL for a property the bank needs (see the note on each). None of the
 * student personas appears in the tuned banks (student-v3, staff-db,
 * university) except Ada Kettleby, who is the only student in the tenant
 * with more than one unread message. Ids are never written here: the runner
 * resolves each ref against ground-truth.json (`students.<ref>.id` /
 * `students.<ref>.personId` / `staff.byRef.<ref>.id`) so a regenerated
 * snapshot cannot drift from the cases.
 */

export const STUDENTS = {
  // In progress, 2 overdue requirements (transcript under review, FA
  // verification), 7 open work items across four offices, four assigned
  // staff incl. an international adviser. Three students share her name.
  lucia: { ref: "SYN-001278", note: "in progress + overdue + busy Action Center; name x3" },
  // Same-name siblings, only referenced by staff disambiguation cases.
  lucia_done: { ref: "SYN-001366", note: "Lucia Zephyrine, completed journey, no primary adviser" },
  lucia_design: { ref: "SYN-001898", note: "Lucia Zephyrine, Design 2028, rejected transcript" },
  // Completed journey: 8/8 requirements, all documents accepted, no open work.
  bruno: { ref: "SYN-001645", note: "completed journey, honest-empty" },
  // No primary adviser, unpaid deposit (due next January), nothing overdue,
  // no open work items. Four students share his name.
  omar: { ref: "SYN-001726", note: "no primary adviser; unpaid but not-yet-due deposit; name x4" },
  omar_chem: { ref: "SYN-001941", note: "Omar Vellacourt in Chemistry — disambiguation pick" },
  // No primary adviser, five overdue requirements incl. the deposit.
  gustav: { ref: "SYN-002720", note: "no primary adviser; everything overdue; name x2" },
  // Adviser Junia Pemberwell is on leave; zero appointments ever; one open
  // housing work item.
  noor: { ref: "SYN-000897", note: "adviser on leave; no appointments at all" },
  // Adviser Quentin Zephyrine has departed; 4 open work items; 2 docs in review.
  adria: { ref: "SYN-000728", note: "adviser departed; preferred name Ada" },
  // Unpaid, overdue deposit; six overdue requirements; upcoming advising appointment.
  petra: { ref: "SYN-001217", note: "unpaid overdue deposit; upcoming appointment" },
  // Rejected immunization document; five assigned staff (incl. housing +
  // international); six open work items incl. one urgent and one blocked.
  kwame: { ref: "SYN-000631", note: "rejected document; richest cross-entity persona" },
  // Two upcoming appointments (adviser + international check-in); deposit in progress.
  hana: { ref: "SYN-001030", note: "two upcoming appointments; unpaid deposit" },
  // Six unread messages; upcoming appointment; archived help inquiry; no open work.
  ada: { ref: "SYN-000061", note: "unread messages; archived inquiry" },
  // Archived help inquiry; rejected immunization document; nothing overdue.
  camila: { ref: "SYN-001566", note: "archived inquiry; rejected document; nothing overdue" },
  // Zero open work items; adviser Harriet Vasquez is on vacation this week.
  greta_e: { ref: "SYN-000665", note: "zero open work; adviser on vacation" },
};

export const STAFF = {
  // Financial-aid counselor: 541 assignments, 0 primary advisees, 55 open items.
  greta: { ref: "SYN-STF-FA-C06", note: "FA counselor; big caseload, no advisees" },
  // Academic adviser with 77 advisees; 14 appointments today; on vacation Sep 3-6.
  ada_a: { ref: "SYN-ADV-000", note: "adviser with advisees; vacation soon" },
  // Admissions counselor: 299 assignments, 16 open items, reports to Priya Shah.
  zelda: { ref: "SYN-STF-ADM-C08", note: "admissions counselor" },
  // International adviser (DSO): 319 assignments, 276 open items, 73 urgent.
  matthias: { ref: "SYN-STF-ISS-DSO2", note: "international adviser; huge queue" },
  // University Registrar: 6 direct reports, 32 open items (22 transcript reviews).
  registrar: { ref: "SYN-STF-REG-DIR", note: "director with a team" },
  // Senior adviser on leave until 2026-09-21; 67 advisees; 2 open items.
  junia: { ref: "SYN-ADV-005", note: "staff member on leave" },
  // Records Specialist, back office, not student-facing; 67 open items.
  marcus: { ref: "SYN-STF-REG-REC", note: "back-office role" },
  // Associate Director of Admissions; 12 direct reports; owns 0 work items.
  priya: { ref: "SYN-STF-ADM-AD", note: "honest-empty queue" },
  // Director of Academic Advising; 10 direct reports; 21 open items.
  leandro: { ref: "SYN-STF-ADV-DIR", note: "advising director" },
};

/** Colleagues referenced by name in cases (never signed in as). */
export const COLLEAGUES = {
  hana_d: "SYN-ADV-001", // Hana Dunmire, Ada Kettleby's adviser, 90 advisees
  harriet: "SYN-STF-ADV-AD2", // Harriet Vasquez, Greta Everlyn's adviser, on vacation
  quentin: "SYN-ADV-008", // Quentin Zephyrine, departed
  bianca_n: "SYN-ADV-021", // Bianca Netherby, adviser to Kwame and Camila
  noor_b: "SYN-ADV-017", // Noor Brightwater, Petra's adviser
  omar_g: "SYN-ADV-002", // Omar Glimmerly, Hana Mossbank's adviser
  caleb_m: "SYN-ADV-004", // Caleb Mossbank, Lucia's adviser
  ivo: "SYN-ADV-024", // Ivo Whitlowe, Bruno's adviser
  camila_o: "SYN-STF-REG-EV2", // Camila Okonkwo, transcript evaluator
};

export function actorRef(kind, actor) {
  const table = kind === "student" ? STUDENTS : STAFF;
  if (table[actor]) return table[actor].ref;
  if (/^SYN-/.test(actor)) return actor;
  throw new Error(`Unknown ${kind} persona "${actor}"`);
}
