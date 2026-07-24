export const FIXTURE_VERSION = "vv-demo-v2";
export const ONBOARDING_STEPS = Object.freeze([
  "offer",
  "about_you",
  "housing",
  "campus_life",
  "emergency_contacts",
  "other_records",
  "family_permissions",
  "review_and_sign",
  "deposit",
]);

export const ids = Object.freeze({
  tenant: "00000000-0000-7000-8000-000000000001",
  actor: "00000000-0000-7000-8000-000000000100",
  student: "00000000-0000-7000-8000-000000000101",
  offer: "00000000-0000-7000-8000-000000000201",
  journey: "00000000-0000-7000-8000-000000000501",
  profileRequirement: "00000000-0000-7000-8000-000000000601",
  identityRequirement: "00000000-0000-7000-8000-000000000602",
  depositRequirement: "00000000-0000-7000-8000-000000000603",
  welcomeMessage: "00000000-0000-7000-8000-000000000701",
  reminderMessage: "00000000-0000-7000-8000-000000000702",
  helpGettingStarted: "00000000-0000-7000-8000-000000000801",
  helpDocuments: "00000000-0000-7000-8000-000000000802",
  helpPayments: "00000000-0000-7000-8000-000000000803",
});

const seedTimestamp = "2026-07-24T00:00:00.000Z";

export function createSeedState() {
  return {
    schemaVersion: 2,
    fixture: {
      version: FIXTURE_VERSION,
      seededAt: seedTimestamp,
      updatedAt: seedTimestamp,
      revision: 1,
    },
    auth: {
      demoIdentity: {
        actorId: ids.actor,
        studentId: ids.student,
        tenantId: ids.tenant,
        displayName: "Alex Morgan",
      },
    },
    profile: {
      studentId: ids.student,
      preferredName: "Alex",
      firstName: "Alex",
      lastName: "Morgan",
      classYear: 2027,
      timezone: "America/New_York",
      pronouns: null,
      mobilePhone: null,
      communicationPreference: "email",
      version: 1,
      updatedAt: seedTimestamp,
    },
    onboarding: {
      status: "in_progress",
      currentStep: "offer",
      completedAt: null,
      completedSteps: [],
      data: {},
      version: 1,
      updatedAt: seedTimestamp,
    },
    offer: {
      id: ids.offer,
      programName: "Computer Science",
      termName: "Fall 2027",
      campusName: "Main Campus",
      responseDeadline: "2027-08-15",
      depositAmountCents: 50000,
      status: "offered",
      acceptedAt: null,
      version: 1,
    },
    portalProjectionVersion: 1,
    journey: null,
    requirements: [],
    messages: [
      {
        id: ids.welcomeMessage,
        subject: "Welcome to Aster University",
        body: "Welcome, Alex. Review your offer when you are ready.",
        sentAt: "2026-07-23T14:00:00.000Z",
        readAt: null,
        senderName: "Aster Enrollment Team",
      },
      {
        id: ids.reminderMessage,
        subject: "Enrollment support is available",
        body: "Use the Help area if you have a question about enrollment.",
        sentAt: "2026-07-22T14:00:00.000Z",
        readAt: seedTimestamp,
        senderName: "Aster Enrollment Team",
      },
    ],
    documents: [],
    appointments: [],
    payments: [],
    helpRequests: [],
    activities: [],
    idempotency: {},
  };
}

export function createJourney(acceptedAt) {
  return {
    id: ids.journey,
    status: "in_progress",
    startedAt: acceptedAt,
    version: 1,
  };
}

export function createRequirements(acceptedAt) {
  const accepted = new Date(acceptedAt);
  const dueAt = (days) =>
    new Date(accepted.getTime() + days * 86_400_000).toISOString();

  return [
    {
      id: ids.profileRequirement,
      code: "profile_verification",
      title: "Verify your profile",
      description:
        "Confirm your personal information before continuing.",
      status: "ready",
      blocking: true,
      dueAt: dueAt(7),
      progressPercent: 0,
      dependsOnCodes: [],
      submissionType: "form",
      responsibleOffice: "Enrollment Services",
    },
    {
      id: ids.identityRequirement,
      code: "identity_document",
      title: "Provide identity documentation",
      description:
        "Add metadata for an accepted identity document for review.",
      status: "blocked",
      blocking: true,
      dueAt: dueAt(14),
      progressPercent: 0,
      dependsOnCodes: ["profile_verification"],
      submissionType: "document",
      responsibleOffice: "Enrollment Documentation",
    },
    {
      id: ids.depositRequirement,
      code: "enrollment_deposit",
      title: "Pay your enrollment deposit",
      description:
        "Complete the simulated enrollment deposit.",
      status: "ready",
      blocking: true,
      dueAt: dueAt(21),
      progressPercent: 0,
      dependsOnCodes: [],
      submissionType: "payment",
      responsibleOffice: "Student Accounts",
    },
  ];
}
