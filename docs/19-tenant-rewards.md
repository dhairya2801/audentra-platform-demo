# Tenant-managed student rewards

The student rewards subsystem turns authoritative progress and selected portal
exploration into university-owned points. Aster and Harvard use the same
runtime, but each tenant owns its program name, conversion rate, enabled state,
and reward values.

## Data model

- `tenant_reward_program` contains the tenant-facing point name and the
  `points_per_usd` bookstore conversion. The seeded value is 100 points per USD.
- `tenant_reward_rule` contains an editable trigger, point value, display order,
  active window, enabled toggle, and per-student award cap.
- `student_reward_ledger` is an immutable record of the value awarded at that
  time. Later rule edits affect future awards and do not silently rewrite a
  student's existing balance.

The supported trigger types are:

- `onboarding_completed`, sourced from the server-confirmed onboarding state;
- `requirement_completed`, sourced from an authoritative enrollment
  requirement transition;
- `activity_event`, sourced from an authenticated, allowlisted portal activity
  event whose primitive properties contain the rule's `trigger_properties`.

Activity rewards are one-time by default. The ledger uniqueness key and
`max_awards_per_student` prevent refresh farming and event replays. Completion
rewards never trust a client-supplied "completed" flag.

## Student experience

The authenticated bootstrap response includes the current point name, lifetime
balance, conversion rate, and calculated bookstore-credit cents. The portal
shows that balance in the top bar and sidebar. Enrollment requirement responses
include the current reward value and whether that exact requirement award was
earned, so task cards can advertise the reward before completion.

Successful student mutations emit an in-browser record-change event. The shell
then refreshes bootstrap data, allowing a newly awarded balance to appear
without a polling loop.

## Future staff editor

A staff-facing editor can operate directly on the reward program and rule
tables:

1. toggle the full program or an individual rule;
2. change the point name, bookstore conversion, or future point value;
3. choose a supported trigger and optional property matcher;
4. set start/end dates, display order, and award cap;
5. preview affected student tasks before publishing.

Publishing should update rules in a transaction and record an audit event.
Ledger rows should never be updated to apply a new rule value retroactively; an
explicit correction or redemption should be modeled as a separate, audited
transaction rather than changing an existing award.
