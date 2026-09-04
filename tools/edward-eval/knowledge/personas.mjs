/**
 * Personas for the institutional-knowledge suite. Every one is a real row in
 * vv_enrollment_synthu chosen for a situation that makes an institutional
 * question interesting; ids come from ground-truth.json at run time.
 */

export const STUDENTS = {
  petra: { ref: "SYN-001217", note: "domestic first-year, unpaid overdue deposit, undecided housing, BS-EE" },
  lucia: { ref: "SYN-001278", note: "international first-year, on campus, BS-EE" },
  kwame: { ref: "SYN-000631", note: "international, rejected immunization document, BA-ECON" },
  ada: { ref: "SYN-000061", note: "domestic first-year, off-campus plan, BS-DS" },
  ivo: { ref: "SYN-001478", note: "domestic transfer (class of 2028), on campus, BS-CE" },
  noor: { ref: "SYN-000897", note: "adviser on leave until 21 Sep 2026" },
  adria: { ref: "SYN-000728", note: "international, adviser departed" },
  omar: { ref: "SYN-001726", note: "Spring 2027 admit, no adviser, deposit not yet due" },
  bruno: { ref: "SYN-001645", note: "international Spring admit, completed journey" },
  milo: { ref: "SYN-000023", note: "BSN first-year, adviser over cap" },
  ximena: { ref: "SYN-000039", note: "Spring 2027 first-year, BBA, adviser on leave" },
  gustav: { ref: "SYN-002720", note: "everything overdue, no adviser" },
  camila: { ref: "SYN-001566", note: "Spring admit, rejected immunization document" },
  greta_e: { ref: "SYN-000665", note: "international Spring admit" },
  hana: { ref: "SYN-001030", note: "international BS-ME, deposit pending" },
};

export const STAFF = {
  hana_d: { ref: "SYN-ADV-001", note: "Senior Academic Adviser" },
  leandro: { ref: "SYN-STF-ADV-DIR", note: "Director of Academic Advising" },
  priya: { ref: "SYN-STF-ADM-AD", note: "Associate Director of Admissions" },
  greta: { ref: "SYN-STF-FA-C06", note: "Financial Aid Counselor" },
  matthias: { ref: "SYN-STF-ISS-DSO2", note: "International adviser (DSO)" },
  registrar: { ref: "SYN-STF-REG-DIR", note: "University Registrar" },
  housing: { ref: "SYN-STF-HRL-DIR", note: "Director of Housing & Residence Life" },
  uma: { ref: "SYN-STF-ES-DIR", note: "Director of Enrollment Services" },
  zubin: { ref: "SYN-STF-SHS-MGR", note: "Health Compliance Manager" },
  elena: { ref: "SYN-ADV-012", note: "over-cap adviser" },
};

export const COLLEAGUES = {};

export function actorRef(kind, actor) {
  const table = kind === "student" ? STUDENTS : STAFF;
  if (table[actor]) return table[actor].ref;
  if (/^SYN-/.test(actor)) return actor;
  throw new Error(`Unknown ${kind} persona "${actor}"`);
}
