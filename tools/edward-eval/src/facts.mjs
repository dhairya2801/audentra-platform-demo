/**
 * Facts derived from a canonical persona snapshot.
 *
 * These derivations deliberately mirror the assistant's own tool projections
 * (apps/api/src/audentra/integrations/assistant/tools.py) so an expectation
 * like "the missing documents" means exactly what Edward's own reads would
 * say. Cases reference facts by name; nothing here is per-case data.
 */

const OPEN_STATUSES = new Set(["blocked", "ready", "in_progress"]);
const SUBMITTED_STATUSES = new Set(["submitted", "under_review"]);
const DONE_STATUSES = new Set(["completed", "waived", "not_applicable"]);

// Mirrors _REQUIREMENT_GATE_CODES in tools.py.
const REQUIREMENT_GATE_CODES = {
  final_transcript: "final_transcript",
  transcript: "final_transcript",
  immunization: "immunization_cleared",
  immunization_record: "immunization_cleared",
  immunization_records: "immunization_cleared",
  official_transcript: "final_transcript",
  advising: "advising_complete",
  orientation: "orientation_complete",
  housing_preference: "housing_preference_selected",
  enrollment_deposit: "enrollment_deposit_posted",
};

const items = (value) => (Array.isArray(value?.items) ? value.items : []);

export function usd(cents) {
  if (typeof cents !== "number") return null;
  return `$${(cents / 100).toLocaleString("en-US", {
    minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Everything the fact checks and judge ground truth can reference. */
export function deriveFacts(snapshot) {
  const requirements = items(snapshot.requirements);
  const documents = items(snapshot.documents);
  const payments = items(snapshot.payments);
  const financials = snapshot.financials ?? {};
  const offer = snapshot.dashboard?.offer ?? {};
  const onboardingData = snapshot.onboarding?.data ?? {};
  const sap = financials.sap ?? {};
  const mailingAddress = [
    onboardingData.streetAddress,
    onboardingData.addressLine2,
    onboardingData.city,
    onboardingData.stateOrProvince,
    onboardingData.postalCode,
    onboardingData.country,
  ]
    .filter(Boolean)
    .join(", ");
  const now = Date.now();

  const open = requirements.filter(
    (item) => !DONE_STATUSES.has(String(item.status)),
  );
  const openBlocking = open.filter((item) => item.blocking);
  const completed = requirements.filter((item) =>
    DONE_STATUSES.has(String(item.status)),
  );

  // A document requirement is "missing" when it is still the student's move:
  // open, document-typed, and not already submitted/under review.
  const missingDocumentRequirements = open.filter(
    (item) =>
      item.documentCategory && OPEN_STATUSES.has(String(item.status)),
  );
  // "rejected" requirements with a returned upload are the student's move too.
  const resubmissionRequirements = requirements.filter(
    (item) => String(item.status) === "rejected",
  );

  // Mirrors derive_deposit_state in apps/api/src/audentra/domain/student_state.py.
  // The payments ledger is authoritative; the financial summary's deposit
  // schedule row is the second canonical witness. The dashboard projection
  // carries no deposit field at all — reading one from it is what made the
  // assistant report every paid deposit as unpaid.
  const depositSchedule = (financials.paymentSchedule ?? []).find(
    (row) => row?.kind === "deposit",
  );
  const postedDeposit =
    payments.some(
      (payment) =>
        payment.type === "enrollment_deposit" && payment.status === "succeeded",
    ) || depositSchedule?.status === "paid";
  const pendingDeposit =
    !postedDeposit &&
    payments.some(
      (payment) =>
        payment.type === "enrollment_deposit" && payment.status === "pending",
    );
  const depositState = postedDeposit
    ? "posted"
    : pendingDeposit
      ? "pending"
      : "unpaid";
  const depositOutstanding =
    !postedDeposit && !pendingDeposit && (offer.depositAmountCents ?? 0) > 0;

  // Registration gates, mirroring _tool_holds/_tool_registration: the unpaid
  // deposit plus every open blocking requirement, one gate per code.
  const gates = [];
  const seen = new Set();
  if (depositOutstanding) {
    gates.push({ code: "enrollment_deposit_posted", title: "Enrollment deposit not posted" });
    seen.add("enrollment_deposit_posted");
  } else if (pendingDeposit) {
    // Submitted-but-unposted still gates enrollment, but the university owns
    // it: the student must not be told to pay a second time.
    gates.push({
      code: "enrollment_deposit_posted",
      title: "Enrollment deposit payment is still processing",
    });
    seen.add("enrollment_deposit_posted");
  }
  for (const item of requirements) {
    const status = String(item.status ?? "");
    if (!item.blocking || DONE_STATUSES.has(status)) continue;
    const gate =
      REQUIREMENT_GATE_CODES[String(item.code ?? "").toLowerCase()] ??
      String(item.code ?? "requirement");
    if (seen.has(gate)) continue;
    seen.add(gate);
    gates.push({
      code: gate,
      title: String(item.title ?? ""),
      submitted: SUBMITTED_STATUSES.has(status),
    });
  }

  const aidDocuments = Array.isArray(financials.requiredDocuments)
    ? financials.requiredDocuments
    : [];
  const openAidDocuments = aidDocuments.filter(
    (doc) => !["received", "waived"].includes(String(doc.status)),
  );
  const awards = Array.isArray(financials.awards) ? financials.awards : [];

  const housingRequirement = requirements.find(
    (item) => String(item.code ?? "").toLowerCase() === "housing_preference",
  );
  const housingStatus = String(housingRequirement?.status ?? "");
  const housingEligibility = !housingRequirement
    ? "no_housing_step"
    : DONE_STATUSES.has(housingStatus)
      ? "already_completed"
      : housingStatus === "blocked"
        ? "blocked"
        : "eligible_now";

  const overdue = open.filter(
    (item) => item.dueAt && Date.parse(item.dueAt) < now,
  );

  const clubs = Array.isArray(snapshot.campusLife?.clubs)
    ? snapshot.campusLife.clubs
    : [];
  const events = Array.isArray(snapshot.campusLife?.events)
    ? snapshot.campusLife.events
    : [];

  const plan = Array.isArray(snapshot.academics?.plan)
    ? snapshot.academics.plan
    : [];
  const blockedCourses = plan.filter(
    (entry) => (entry.missingPrerequisiteCodes ?? []).length > 0,
  );

  const appointments = items(snapshot.appointments);

  return {
    // enrollment / checklist
    openRequirementTitles: open.map((item) => String(item.title)),
    openBlockingTitles: openBlocking.map((item) => String(item.title)),
    completedRequirementTitles: completed.map((item) => String(item.title)),
    openRequirementCount: open.length,
    nextActionLabel: snapshot.dashboard?.journey?.nextAction?.label ?? null,
    overdueTitles: overdue.map((item) => String(item.title)),

    // documents
    missingDocumentTitles: missingDocumentRequirements.map((item) =>
      String(item.title),
    ),
    missingDocumentCategories: missingDocumentRequirements.map((item) =>
      String(item.documentCategory),
    ),
    resubmissionTitles: resubmissionRequirements.map((item) =>
      String(item.title),
    ),
    documentsOnFile: documents.map((doc) => ({
      category: String(doc.category ?? ""),
      status: String(doc.status ?? ""),
      fileName: String(doc.fileName ?? ""),
    })),
    transcriptDocumentStatus:
      documents.find((doc) => doc.category === "transcript")?.status ?? null,
    transcriptRequirementStatus:
      requirements.find((item) => item.code === "official_transcript")?.status ??
      null,

    // deposit / account
    depositState,
    depositAmountUsd: usd(offer.depositAmountCents),
    remainingBalanceUsd: usd(financials.remainingBalanceCents),
    remainingBalanceCents: financials.remainingBalanceCents ?? null,
    costOfAttendanceUsd: usd(financials.costOfAttendanceCents),
    acceptedAidUsd: usd(financials.acceptedAidCents),
    acceptedAidCents: financials.acceptedAidCents ?? null,
    paymentStatuses: payments.map((payment) => ({
      type: String(payment.type ?? ""),
      status: String(payment.status ?? ""),
    })),
    refundDue:
      typeof financials.remainingBalanceCents === "number" &&
      financials.remainingBalanceCents < 0,

    // registration / blockers
    registrationGates: gates,
    registrationGateCodes: gates.map((gate) => gate.code),
    registrationEligible: gates.length === 0,

    // financial aid
    awardNames: awards.map((award) => String(award.name)),
    acceptedAwardNames: awards
      .filter((award) => award.status === "accepted")
      .map((award) => String(award.name)),
    actionRequiredAwardNames: awards
      .filter((award) => award.requiresAction)
      .map((award) => String(award.name)),
    openAidDocumentTitles: openAidDocuments.map((doc) => String(doc.title)),
    fafsaStatus:
      aidDocuments.find((doc) => doc.code === "fafsa")?.status ?? "absent",
    hasAnyAid: awards.length > 0,

    // housing
    housingEligibility,
    housingStepStatus: housingStatus || null,
    housingPreference: snapshot.housingPlan?.preference ?? null,
    housingResidences: (snapshot.housingPlan?.residences ?? []).map((entry) =>
      String(entry.name ?? ""),
    ),

    // appointments
    upcomingAppointments: appointments.filter(
      (item) => item.status === "scheduled",
    ),
    completedAppointments: appointments.filter(
      (item) => item.status === "completed",
    ),

    // academics
    plannedCourseCodes: plan.map((entry) => String(entry.course?.code ?? "")),
    blockedCourseCodes: blockedCourses.map((entry) =>
      String(entry.course?.code ?? ""),
    ),
    missingPrerequisiteCodes: blockedCourses.flatMap((entry) =>
      (entry.missingPrerequisiteCodes ?? []).map(String),
    ),
    suggestedExemptionCodes: (
      snapshot.academics?.exemptionRecommendations ?? []
    ).map((entry) => String(entry.targetCourseCode ?? "")),
    programName: snapshot.academics?.selectedProgram?.name ?? null,

    // campus life
    clubNames: clubs.map((club) => String(club.name)),
    clubNamesByCategory: Object.fromEntries(
      [...new Set(clubs.map((club) => String(club.category)))].map(
        (category) => [
          category,
          clubs
            .filter((club) => String(club.category) === category)
            .map((club) => String(club.name)),
        ],
      ),
    ),
    eventTitles: events.map((event) => String(event.title)),

    // messages
    unreadMessageCount: snapshot.messages?.unreadCount ?? null,

    // admission / enrollment position (the dashboard program strip)
    offerStatus: offer.status ?? null,
    offerProgramName: offer.programName ?? null,
    termName: offer.termName ?? null,
    campusName: offer.campusName ?? null,
    classYear: snapshot.dashboard?.student?.classYear ?? null,
    journeyStatus: snapshot.dashboard?.journey?.status ?? null,
    completionPercent: snapshot.dashboard?.journey?.completionPercent ?? null,

    // onboarding answers the student supplied themselves
    onboardingStatus: snapshot.onboarding?.status ?? null,
    onboardingCurrentStep: snapshot.onboarding?.currentStep ?? null,
    onboardingCompletedSteps: (snapshot.onboarding?.completedSteps ?? []).map(String),
    citizenshipStatus: onboardingData.citizenshipStatus ?? null,
    residencyStatus: onboardingData.residencyStatus ?? null,
    mailingAddress: mailingAddress || null,
    emergencyContactNames: (onboardingData.emergencyContacts ?? []).map((entry) =>
      String(entry?.name ?? ""),
    ),
    signatureRecorded: Boolean(onboardingData.signatureFullName),

    // academic standing (the Financials SAP card)
    cumulativeGpa: sap.cumulativeGpa ?? null,
    minimumGpa: sap.minimumGpa ?? null,
    sapStatus: sap.status ?? null,
    completionRatePercent: sap.completionRatePercent ?? null,

    // the student's own support conversations
    supportRequestSubjects: (snapshot.help?.requests ?? []).map((entry) =>
      String(entry?.subject ?? ""),
    ),
    openSupportRequestCount: (snapshot.help?.requests ?? []).filter(
      (entry) => !["resolved", "closed"].includes(String(entry?.status)),
    ).length,
  };
}

/**
 * Compact ground-truth lines for the judge, grouped by domain so a case can
 * hand the judge only what is relevant. Every line is a plain sentence a
 * grader can check the answer against.
 */
export function groundTruthLines(facts, groups) {
  const all = {
    checklist: () => [
      `Open checklist items: ${facts.openRequirementTitles.join("; ") || "none"}.`,
      `Completed checklist items: ${facts.completedRequirementTitles.join("; ") || "none"}.`,
      facts.overdueTitles.length > 0
        ? `Overdue items: ${facts.overdueTitles.join("; ")}.`
        : "No checklist item is overdue.",
      facts.nextActionLabel
        ? `The journey's next action is: ${facts.nextActionLabel}.`
        : null,
    ],
    documents: () => [
      `Documents still needed from the student: ${facts.missingDocumentTitles.join("; ") || "none"}.`,
      `Documents on file: ${
        facts.documentsOnFile
          .map((doc) => `${doc.category} (${doc.status.replace(/_/g, " ")})`)
          .join("; ") || "none"
      }.`,
      facts.resubmissionTitles.length > 0
        ? `Returned for resubmission (student must act): ${facts.resubmissionTitles.join("; ")}.`
        : null,
    ],
    deposit: () => [
      `Enrollment deposit state: ${facts.depositState}` +
        (facts.depositState === "pending"
          ? " (a payment exists but has not posted — neither 'unpaid' nor 'posted' is the whole truth)."
          : "."),
      facts.depositAmountUsd
        ? `Deposit amount: ${facts.depositAmountUsd}.`
        : null,
    ],
    account: () => [
      facts.remainingBalanceUsd
        ? `Remaining balance: ${facts.remainingBalanceUsd}${facts.refundDue ? " (negative — aid exceeds cost, a refund scenario)" : ""}.`
        : "No remaining balance figure is recorded.",
      facts.costOfAttendanceUsd
        ? `Cost of attendance: ${facts.costOfAttendanceUsd}.`
        : null,
      facts.acceptedAidUsd ? `Accepted aid total: ${facts.acceptedAidUsd}.` : null,
    ],
    registration: () => [
      facts.registrationEligible
        ? "No gate currently blocks course registration."
        : `Gates blocking registration: ${facts.registrationGates
            .map((gate) => gate.title || gate.code)
            .join("; ")}.`,
      "No term registration window is published by the platform.",
    ],
    aid: () => [
      `Financial-aid awards on file: ${facts.awardNames.join("; ") || "none"}.`,
      `Aid documents still outstanding: ${facts.openAidDocumentTitles.join("; ") || "none"}.`,
      `FAFSA status: ${String(facts.fafsaStatus).replace(/_/g, " ")}.`,
      facts.actionRequiredAwardNames.length > 0
        ? `Awards awaiting a student decision: ${facts.actionRequiredAwardNames.join("; ")}.`
        : null,
      "The platform tracks no disbursement schedule; no disbursement date can be quoted.",
    ],
    housing: () => [
      `Housing step status: ${facts.housingStepStatus ?? "absent"} (eligibility: ${facts.housingEligibility.replace(/_/g, " ")}).`,
      facts.housingPreference
        ? `Recorded housing preference: ${String(facts.housingPreference).replace(/_/g, " ")}.`
        : "No housing preference is recorded.",
      "The platform records preferences only — no room assignment, hall placement, or application window exists.",
    ],
    appointments: () => [
      `Scheduled appointments: ${
        facts.upcomingAppointments
          .map((item) => `${String(item.type).replace(/_/g, " ")} at ${item.startsAt}`)
          .join("; ") || "none"
      }.`,
      `Completed appointments: ${
        facts.completedAppointments
          .map((item) => String(item.type).replace(/_/g, " "))
          .join("; ") || "none"
      }.`,
    ],
    academics: () => [
      `Academic program: ${facts.programName ?? "not selected"}.`,
      `Planned courses: ${facts.plannedCourseCodes.join(", ") || "none"}.`,
      facts.blockedCourseCodes.length > 0
        ? `Courses with missing prerequisites: ${facts.blockedCourseCodes.join(", ")} (missing: ${facts.missingPrerequisiteCodes.join(", ")}).`
        : "No planned course is missing a prerequisite.",
      facts.suggestedExemptionCodes.length > 0
        ? `Suggested exemptions: ${facts.suggestedExemptionCodes.join(", ")}.`
        : null,
    ],
    campus: () => [
      `Clubs listed: ${facts.clubNames.join("; ") || "none"}.`,
      `Upcoming events: ${facts.eventTitles.join("; ") || "none"}.`,
    ],
    enrollment_position: () => [
      `Admission offer status: ${facts.offerStatus ?? "not recorded"}.`,
      `Program: ${facts.offerProgramName ?? "not recorded"}; starting term: ${facts.termName ?? "not recorded"}; campus: ${facts.campusName ?? "not recorded"}.`,
      facts.classYear ? `Class year: ${facts.classYear}.` : null,
      `Enrollment journey: ${facts.journeyStatus ?? "not started"} at ${facts.completionPercent ?? 0}% complete.`,
      `Onboarding: ${facts.onboardingStatus ?? "not recorded"}${facts.onboardingCurrentStep ? ` at the ${facts.onboardingCurrentStep} step` : ""}.`,
    ],
    personal_information: () => [
      `Citizenship status on file: ${facts.citizenshipStatus ?? "not recorded"}.`,
      `Residency status on file: ${facts.residencyStatus ?? "not recorded"}.`,
      `Mailing address on file: ${facts.mailingAddress ?? "not recorded"}.`,
      `Emergency contacts on file: ${facts.emergencyContactNames.join("; ") || "none"}.`,
      `Enrollment signature recorded: ${facts.signatureRecorded ? "yes" : "no"}.`,
    ],
    academic_standing: () => [
      facts.cumulativeGpa === null
        ? "No cumulative GPA is recorded."
        : `Cumulative GPA: ${facts.cumulativeGpa} against a ${facts.minimumGpa} minimum.`,
      `Satisfactory academic progress status: ${facts.sapStatus ?? "not recorded"}.`,
      facts.completionRatePercent === null
        ? null
        : `Completion rate: ${facts.completionRatePercent}%.`,
    ],
    support_requests: () => [
      `Support conversations on record: ${facts.supportRequestSubjects.join("; ") || "none"}.`,
      `Open support conversations: ${facts.openSupportRequestCount}.`,
    ],
    institutional_gaps: () => [
      "The platform has no registrar hold system, no published academic calendar (no orientation/term/move-in dates), no reviewed policy corpus, no room assignments, and no disbursement ledger. Any specific claim in these areas is invented.",
    ],
  };
  const selected = groups?.length ? groups : Object.keys(all);
  return selected
    .flatMap((group) => (all[group] ? all[group]() : []))
    .filter(Boolean);
}
