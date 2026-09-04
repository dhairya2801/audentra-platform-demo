# Aster University — institutional knowledge corpus (tenant `aster-demo`)

Everything in this directory is **synthetic**. Aster University is fictional; no
rule, date, amount, office or person describes a real institution. The corpus
exists so Edward can answer institutional questions ("what happens if I miss
the deposit deadline?", "does the first-year residency rule apply to me?",
"who handles a transcript that was rejected?") from an approved, versioned
source instead of refusing or inventing.

## Layout

| Path | What it is | Runtime table |
| --- | --- | --- |
| `offices.yaml` | The office directory: every unit a student or staff member can be sent to, with location, hours, mailbox, the staff `component` it maps to, and what it handles. | `institution_office` |
| `calendar.yaml` | The 2026–2027 academic calendar as dated entries (term dates, deadlines, windows) with audience and owning office. | `academic_calendar_entry` |
| `documents/*.md` | Policies, procedures, handbook chapters, program guides, service guides and internal staff procedures — Markdown with YAML front matter. | `institution_knowledge_document` + `institution_knowledge_section` |

`audentra-seed-knowledge --tenant aster-demo` imports the corpus (idempotent,
validated: unique codes, resolvable cross-references and office codes,
well-formed dates, facet vocabulary). PostgreSQL is canonical at runtime; these
files are the seed/import input, exactly like `academics.yaml` and
`campus-life.yaml`.

## The world, in one page (the facts every document must agree with)

These values come from the population generator
(`tools/demo-api/src/synthetic-university/generate.js`), the staff generator
(university explorer) and the seeded database. A document that contradicts
this sheet is wrong, not the sheet.

**Institution.** Aster University, Aster Main Campus, `America/New_York`.
Academic year 2026–2027; current term Fall 2026. Mailboxes end in
`@synthetic.aster.example`. Student IDs look like `SYN-001278`. Nobody has a
telephone number on record — contact is by portal, email and office visit.

**Terms.** Fall 2026: classes Mon 2026-08-31 → Fri 2026-12-18; registration
opens for new students Mon 2026-08-17; add/drop (registration closes) Fri
2026-09-11. Spring 2027: classes Tue 2027-01-19 → Fri 2027-05-07; registration
opens Mon 2026-11-02; add/drop Fri 2027-01-29. Fall 2027: classes 2027-08-30 →
2027-12-17; registration opens 2027-04-05; add/drop 2027-09-10.

**Money (per academic year unless stated).** Enrollment deposit $500 (credited
to Fall tuition; refundable on written withdrawal to Admissions by 2026-06-01
for Fall 2026 admits, 2026-12-01 for Spring 2027 admits). Tuition: in-state
$18,400, out-of-state $28,900, international $31,200. Mandatory fees $1,450.
Standard housing (traditional double) $7,250. Unlimited meal plan $2,900;
14-meal $2,450; 10-meal $2,050; commuter block of sixty $850. Books $1,200.
Personal $2,100 domestic / $3,400 international. Cost of attendance therefore
$33,300 / $43,800 / $47,400. One-time orientation fee $175. Student health
insurance $1,850 (waivable with comparable coverage; not waivable for F-1/J-1).
Payment plan: four instalments per semester, $45 enrolment fee, no interest.
A past-due balance over $250 places a billing hold. Late fee $75 once a
balance is 30 days past due. Official transcript $8.

**Aid year 2026–2027.** Funds: Federal Pell Grant, Federal SEOG, State Access
Grant, Aster Merit Scholarship, Aster Access Scholarship, Federal Direct
Subsidised / Unsubsidised Loan, Federal Work-Study; for international students
Aster Global Scholarship, International Campus Employment award and the
partner-lender International Student Loan. Verification groups V1, V4, V5.
SAP: cumulative GPA ≥ 2.0, completion rate ≥ 67%, maximum timeframe 150% of
program credits (180 attempted credits for a 120-credit program). Fall aid
disburses 2026-09-04; Spring 2027-01-22. Credit-balance refunds within 14 days.

**Housing.** Six residences: Alder Hall (traditional, 320 beds), Birchwood
Commons (suite, 260), Cedarcroft House (traditional, 180), Dunmore Hall (suite,
240), Elmridge Commons (apartment, 200), Fernhollow House (apartment, 150).
Housing application opens 2026-07-15; priority deadline 2026-08-01; final
deadline 2026-08-22 (waitlist after). Move-in Sat 2026-08-22 10:00 → Sun
2026-08-23 18:00. First-year students live on campus both semesters unless
exempt (family within 30 miles, 21 or older at term start, approved
accommodation).

**Orientation (New Student Programs).** Remote session 2026-08-19; Sessions A/B/C
2026-08-24/25/26; Transfer Orientation 2026-08-27; International Check-In and
Orientation 2026-08-21 → 22. Registration closes 2026-08-12. Attendance is
required for new undergraduates.

**Advising.** Every deposited student is assigned an academic adviser within
five business days of the deposit posting, matched to the program's department
(transfer and Spring admits: the Assistant Director, Transfer & Spring Admits'
pool). The first advising meeting is required before registration. Advising
appointments are 30 minutes; financial-aid appointments 45.

**Enrollment checklist (the eight requirements every admitted student has).**
profile verification (due 7 days after acceptance, Enrollment Services);
identity document (14 days, Registrar); official transcript (14 days,
Registrar); financial-aid verification (10 days, Financial Aid; only when
selected); immunization record (30 days, Student Health); housing plans (18
days, Housing & Residence Life; opens after the deposit); enrollment deposit
(21 days, Student Accounts); orientation registration (35 days, New Student
Programs; opens after deposit and identity document).

**Offices and where they sit.** Larkin Hall: Enrollment Services (Lobby),
Admissions (100), Student Accounts (120), Registrar (150), Financial Aid (210),
Vice President for Enrollment Management (400), Title IX (420). Advising Centre
(rooms 200–224). Cedarcroft Commons: Housing & Residence Life. Global Center 2F:
International Student Services. Wellness Center: Student Health Services (1F),
Accessibility Services (1F), Counseling Services (2F). Student Union: New
Student Programs (230), Student Life (300), Student Conduct (310). Innovation
Hall 120: Career Center. Larkin Library: Learning Commons, IT Service Desk.
Gatehouse: Campus Safety, Parking & Transit.

**Departmental service levels (business days).** Enrollment Services 1;
International Student Services 2; Admissions, Student Accounts, New Student
Programs 3; Academic Advising, Financial Aid, Registrar, Housing, Student Life
5; Student Health 7.

## Front matter for a document

```yaml
---
code: housing-first-year-residency        # unique, kebab-case, stable
kind: policy                              # policy | procedure | handbook | program | service | directory | internal
title: First-Year Residency Requirement
summary: One or two sentences a search result can show.
owner_office: HRL                         # a code from offices.yaml
audience: student                         # student | internal | all
status: published                         # published | draft | retired
version: "2026.1"
effective_from: 2026-05-01
effective_until: null
supersedes: null
applies_to:                               # facets; omit a key to mean "everyone"
  class_standing: [first_year]            # first_year | transfer
  residency: [domestic, international]
  admit_term: [Fall 2026, Spring 2027]
  housing_plan: [on_campus, off_campus, family, undecided]
  citizenship: [us_citizen, international]
  program: [BSN]                          # program codes
  department: [Health Sciences]
related: [housing-application-and-assignment]
keywords: [off campus, live at home, commute]
---
```

Sections are `##` headings; the importer stores each section separately so
retrieval can return the paragraph that answers, not the whole document.
