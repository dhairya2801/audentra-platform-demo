/**
 * Seeded student states for evaluation.
 *
 * Each persona is a mutation applied to the preview seed after it is created,
 * so a scenario is described by what is *different* about the student rather
 * than by a whole duplicated fixture. Keep these internally consistent with
 * `tools/demo-api/src/synthetic-university.js`.
 */

const complete = (state, code, completedAt = "2026-07-20T12:00:00.000Z") => {
  const requirement = state.requirements?.find((item) => item.code === code);
  if (!requirement) return;
  requirement.status = "completed";
  requirement.progressPercent = 100;
  requirement.completedAt = completedAt;
};

const setDue = (state, code, dueAt) => {
  const requirement = state.requirements?.find((item) => item.code === code);
  if (requirement) requirement.dueAt = dueAt;
};

export const PERSONAS = Object.freeze({
  /** Freshly accepted, nothing done. The default preview student. */
  new_admit: {
    description: "Accepted, no onboarding steps complete, deposit unpaid.",
    apply() {},
  },

  /** Deposit posted: the gate that opens housing and clears the billing hold. */
  deposit_posted: {
    description: "Enrollment deposit has posted; housing should be reachable.",
    apply(state) {
      complete(state, "enrollment_deposit");
    },
  },

  /**
   * The distinction the brief cares about: the student believes they paid, and
   * a payment exists, but it has not cleared, so dependent gates stay closed.
   */
  payment_pending: {
    description: "Deposit payment recorded but still pending, not posted.",
    apply(state) {
      state.payments = [
        {
          id: "payment-pending-deposit",
          label: "Enrollment deposit",
          amountUsd: 500,
          state: "pending",
          postedAt: null,
          appliesToChargeCode: "enrollment_deposit",
        },
      ];
    },
  },

  /** Most of the way through onboarding, so "what's next" has a narrow answer. */
  nearly_complete: {
    description:
      "Deposit, identity, transcript, and immunisation complete; housing and orientation open.",
    apply(state) {
      for (const code of [
        "profile_verification",
        "enrollment_deposit",
        "identity_document",
        "official_transcript",
        "immunization_record",
      ]) {
        complete(state, code);
      }
      state.appointments = [
        {
          id: "appointment-advising-complete",
          type: "advising",
          status: "completed",
          startsAt: "2026-07-28T15:00:00.000Z",
          endsAt: "2026-07-28T15:30:00.000Z",
          location: "Advising Center",
          withWhom: "Academic Advising",
        },
      ];
    },
  },

  /** An official enrollment hold, which outranks ordinary incomplete steps. */
  official_hold: {
    description: "An official enrollment hold is open on the journey record.",
    apply(state) {
      complete(state, "enrollment_deposit");
      if (state.journey) state.journey.status = "on_hold";
    },
  },

  /** Deadlines already in the past, to test overdue handling. */
  deadline_passed: {
    description: "Several onboarding deadlines are already overdue.",
    apply(state) {
      setDue(state, "enrollment_deposit", "2026-07-01T12:00:00.000Z");
      setDue(state, "identity_document", "2026-07-05T12:00:00.000Z");
      setDue(state, "official_transcript", "2026-07-08T12:00:00.000Z");
    },
  },

  /** Has an advising appointment booked but not yet attended. */
  advising_booked: {
    description: "Advising appointment scheduled but not completed.",
    apply(state) {
      complete(state, "enrollment_deposit");
      state.appointments = [
        {
          id: "appointment-advising-upcoming",
          type: "advising",
          status: "scheduled",
          startsAt: "2026-08-18T15:00:00.000Z",
          endsAt: "2026-08-18T15:30:00.000Z",
          location: "Advising Center, Room 210",
          withWhom: "Academic Advising",
        },
      ];
    },
  },

  /** Assigned a room, so assignment questions have a real answer. */
  housing_assigned: {
    description: "Deposit posted, housing preference recorded, room assigned.",
    apply(state) {
      complete(state, "enrollment_deposit");
      complete(state, "housing_preference");
      state.housingAssignment = {
        state: "assigned",
        residenceName: "Larkspur Hall",
        roomLabel: "312B",
        moveInAt: "2026-08-22T14:00:00.000Z",
      };
    },
  },

  /**
   * Aid states. These exist because financial-aid answers were thin, and thin
   * answers were mostly thin *state*: one seeded student cannot exercise
   * "no aid", "estimated", "finalized", and "covered in full" at once.
   */

  /** FAFSA never filed: the package cannot start. */
  fafsa_missing: {
    description: "No FAFSA on file, so no aid package exists yet.",
    apply(state) {
      const fafsa = state.financials.requiredDocuments.find(
        (item) => item.code === "fafsa",
      );
      if (fafsa) fafsa.status = "not_started";
      state.financials.awards = [];
    },
  },

  /** FAFSA in, verification outstanding: aid is estimated, not settled. */
  aid_verification_outstanding: {
    description:
      "FAFSA received and selected for verification; worksheet still outstanding.",
    apply(state) {
      const worksheet = state.financials.requiredDocuments.find(
        (item) => item.code === "verification_worksheet",
      );
      if (worksheet) worksheet.status = "action_required";
    },
  },

  /** Everything settled: awards accepted, nothing outstanding. */
  aid_finalized: {
    description:
      "Verification complete and every award decided, so aid is finalized.",
    apply(state) {
      for (const item of state.financials.requiredDocuments) {
        item.status = "verified";
      }
      for (const award of state.financials.awards) {
        award.status = "accepted";
        award.acceptedAmountCents = award.offeredAmountCents;
        award.requiresAction = false;
      }
      complete(state, "financial_aid_verification");
    },
  },

  /** Aid finalized and enrollment confirmed: disbursement is merely waiting. */
  aid_ready_to_disburse: {
    description:
      "Aid finalized and deposit posted, so disbursement is scheduled rather than held.",
    apply(state) {
      PERSONAS.aid_finalized.apply(state);
      complete(state, "enrollment_deposit");
    },
  },

  /** Aid exceeds the bill, so the answer is a refund rather than a balance. */
  aid_refund_due: {
    description: "Accepted aid exceeds cost of attendance; a refund is due.",
    apply(state) {
      PERSONAS.aid_ready_to_disburse.apply(state);
      state.financials.costOfAttendanceCents = 1_200_000;
    },
  },

  /** No aid at all: the honest answer is that there is nothing to report. */
  no_aid: {
    description: "No FAFSA, no awards, no aid requirements.",
    apply(state) {
      state.financials.awards = [];
      state.financials.requiredDocuments = [];
    },
  },

  /**
   * A transcript already uploaded and with the registrar. The state the
   * reported bug produced a wrong answer for.
   */
  transcript_under_review: {
    description: "Transcript uploaded and under review; other documents missing.",
    apply(state) {
      state.documents = [
        {
          id: "10000000-0000-7000-8000-000000000901",
          requirementId: state.requirements.find(
            (item) => item.code === "official_transcript",
          )?.id,
          fileName: "official-transcript.pdf",
          mimeType: "application/pdf",
          sizeBytes: 4096,
          category: "transcript",
          processingMode: "agentic",
          status: "under_review",
          sha256: "b".repeat(64),
          storageKey: "10000000-0000-7000-8000-000000000901.pdf",
          contentStored: true,
          extraction: null,
          createdAt: "2026-08-04T09:00:00.000Z",
        },
      ];
    },
  },

  /** A document a reviewer sent back: the student has to act again. */
  document_needs_resubmission: {
    description: "Transcript reviewed and returned for resubmission.",
    apply(state) {
      PERSONAS.transcript_under_review.apply(state);
      const document = state.documents[0];
      document.status = "needs_resubmission";
      document.review = {
        decision: "needs_resubmission",
        decidedAt: "2026-08-05T09:00:00.000Z",
        note: "The copy provided is unofficial.",
        synthetic: true,
      };
    },
  },
});

export const PERSONA_NAMES = Object.keys(PERSONAS);
