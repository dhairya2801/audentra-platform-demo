# Synthetic university data layer

A whole fictional institution — students, applications, documents, holds,
financial aid, ledgers, housing, orientation, registration — generated from a
single integer seed.

**Everything here is synthetic.** No record describes a real institution, a real
person, or real policy. Records carry `isSynthetic: true`, student references
use a `SYN-` prefix, and email addresses use the reserved `.example` domain
(`@synthetic.aster.example`). The tenant, "Aster University", is fictional. If
any value here ever looks like a real institution's rule, that is a bug.

This directory supersedes `../synthetic-university.js` in scope. That file
remains the source of the demo tenant's calendar and policy text; this one adds
the population the demo and the evaluation harness read against.

## Using it

```js
import {
  syntheticUniverse,
  generateUniverse,
  validateUniverse,
  SYNTHETIC_PERSONAS,
  studentRecord,
} from "./synthetic-university/index.js";

const universe = syntheticUniverse();          // memoised, default seed, read-only
const fresh = generateUniverse({ seed: 42 });  // fresh objects, safe to mutate
const violations = validateUniverse(fresh);    // [] means internally consistent

const wren = studentRecord(universe, SYNTHETIC_PERSONAS[0].studentId);
```

`generateUniverse` accepts `{ seed, studentCount, now }`. `studentCount` must be
between 2000 and 4000. `now` is an ISO instant, defaulting to the fixed
`2026-08-05T12:00:00.000Z` — the generator never reads the wall clock, so
regeneration is idempotent and safe to rerun.

## Determinism

The same seed produces byte-identical JSON. That is load-bearing: an evaluation
transcript recorded last week has to refer to the same student today. Two rules
keep it true, and both are enforced by the test suite:

- No `Math.random` and no `Date.now` anywhere in this directory. Randomness
  comes from `random.js` (mulberry32), time comes from the `now` parameter.
- Catalogue rows and persona students take their ids from `uuidFromString`, so
  they are constants of the source rather than of the draw order.

To reseed, pass a different `seed`. Everything downstream changes; nothing has
to be cleaned up first.

## Policies live in the data

Deadlines, gates, hold semantics, and thresholds are fields on records, not
sentences in a prompt: `holdTypes[].blocksRegistration`,
`registrationEligibility[].gates`, `applications[].depositDeadline`,
`BALANCE_HOLD_THRESHOLD_USD`. Changing a rule is a data change.

## Guaranteed states, not lucky ones

`state-matrix.js` forces every interesting state into the population before the
remainder is randomised, so a rejected-document student or a refund-due student
is present on every run rather than most runs. Each student records the states
it was assigned in `student.stateKeys`.

**Adding a state:**

1. Append an entry to `STATE_MATRIX` in `state-matrix.js` with a kebab-case
   `key`, a `label`, and an `assign(student, universe, rng)` that sets only the
   traits it cares about on `student.plan`.
2. If the new trait cannot be expressed with the existing plan fields, add the
   field to `emptyPlan()` in `generate.js`, give it a default draw in
   `normalisePlan`, and read it where the records are built.
3. If two traits cannot coexist, reconcile them in `normalisePlan` rather than
   in the matrix entry — that is where every contradiction is resolved.
4. Add or extend a check in `invariants.js` if the new state introduces a
   relationship that must hold.
5. Optionally reference the key from a persona in `personas.js`.

The test suite asserts that every matrix key has at least one student, so a new
state cannot be silently unreachable.

## Personas

`personas.js` names ten students whose situations make at least one Edward
question interesting. Their student ids are derived from their keys, so they can
be referenced without generating the universe first. Anything a persona's
`scenario` text asserts is forced by a state key rather than left to the dice —
if you edit a scenario, make sure a matrix key still guarantees it.

## Invariants

`validateUniverse` returns human-readable violation strings. Among other things
it checks that every foreign key resolves, that a disbursement implies an
accepted award, that a housing assignment implies a posted enrolment deposit,
that verification requirements imply a FAFSA selected for verification, that
accepted award amounts never exceed offers, that aid credits never exceed
disbursed amounts, that a denied applicant has no housing, registration, or aid
records, that document status histories move forward in time and end on the
document's current status, and that cost-of-attendance totals equal the sum of
their components.
