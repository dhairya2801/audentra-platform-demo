# Onboarding Question Alignment

## Decision

The first-session onboarding follows the question intent and branching of the
product-owner reference at:

`https://welcome-home-enrollment.sait-burak20.chatgpt.site/`

The reference was inspected on 2026-07-26. Aster intentionally differs in one
place: identity, health, accessibility, and document uploads are enrollment
requirements, not first-session onboarding questions.

## Before, reference, and implemented flow

| Area | Previous portal | Reference experience | Implemented portal |
|---|---|---|---|
| Offer | Required acceptance checkbox | Accept or continue deciding | Shows the authoritative offer and records acceptance before advancing |
| About you | First, last, preferred name, phone, three redundant confirmation checkboxes, residency and communication choices | Legal/preferred name, personal email, mobile, citizenship/status, permanent address, identity route | Legal/preferred name, personal email, mobile, citizenship/status, and permanent address; confirmation block and identity route removed |
| Housing | On campus, off campus, undecided | Five paths with conditional room, roommate, off-campus, and commuter questions | Same five paths and conditional question sets; may be skipped |
| Campus life | Five broad interest categories and four support categories | Specific clubs, social setting, first-month goals, and support interests | Specific reference club choices, social setting, first-month goals, and support interests; may be skipped |
| Emergency contacts | “I reviewed” checkbox for unspecified existing data | Student enters one or more named contacts with relationship and phone | Student enters one to four contacts with the same fields |
| ID, health, and access | Required confirmation-only step | Optional ID photo, transcript, immunization, and accessibility actions | Removed from onboarding; those records remain separate enrollment tasks |
| Family permissions | “I reviewed” checkbox | Private by default; optional person-by-person FERPA scopes | Private by default; up to four authorized people with relationship, email, scopes, purpose, and expiry |
| Review and sign | Summary plus confirmation checkbox | Generated packet and typed full legal name | Summary plus typed full legal name |
| Deposit | Automatically created a dummy payment on continue | Pay now, pay later, or waiver/deferral | Pay now, pay later, or waiver/deferral; also has **Skip for now** and never pays unless `pay_now` is selected |

## Canonical ordered steps

1. `offer`
2. `about_you`
3. `housing`
4. `campus_life`
5. `emergency_contacts`
6. `family_permissions`
7. `review_and_sign`
8. `deposit`

`housing`, `campus_life`, and `deposit` are skippable. A skipped step is still
recorded in ordered progress so onboarding can complete, while
`data.skippedSteps` tells the dashboard and enrollment surfaces which work
remains available later.

## State and ownership

- The server remains authoritative for `currentStep`, `completedSteps`,
  `skippedSteps`, `version`, and final onboarding status.
- The client submits only the currently rendered answers plus the last
  server-confirmed payload.
- `expectedVersion` prevents two devices from silently overwriting each other.
- About-you completion synchronizes legal/preferred names, mobile phone, and
  communication defaults to the student profile in the same transaction.
- Emergency contacts and FERPA permissions are structured arrays, not boolean
  acknowledgements.
- Selecting `pay_now` invokes the idempotent payment boundary before the
  deposit step is saved. `pay_later`, `waiver_or_deferral`, and skipping never
  create a payment.
- Identity, transcript, financial-aid, immunization, and accessibility
  documents use the document/enrollment workflow and are not stored in the
  onboarding payload.

## Compatibility

Migration `0008_onboarding_question_alignment.sql`:

- advances records currently paused on `other_records` to
  `family_permissions`;
- removes `other_records` from completed and skipped arrays;
- removes obsolete confirmation-only payload fields; and
- replaces the database step constraint with the eight-step sequence.

The preview JSON store performs the equivalent compatibility cleanup while
loading an existing development state file.
