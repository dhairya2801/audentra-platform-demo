import { demoTenants, publicTenantContext, tenantConfigForSlug } from "./tenant-config.js";
import {
  harvardAcademicContent,
  harvardCampusLifeContent,
} from "./tenant-content.js";
import { createManagedConfigurations } from "./managed-config.js";

export const FIXTURE_VERSION = "vv-demo-v4";
export const TENANT_CONTENT_VERSION = "tenant-content-v6";
export const ONBOARDING_STEPS = Object.freeze([
  "offer",
  "about_you",
  "housing",
  "campus_life",
  "emergency_contacts",
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
  transcriptRequirement: "00000000-0000-7000-8000-000000000604",
  financialRequirement: "00000000-0000-7000-8000-000000000605",
  healthRequirement: "00000000-0000-7000-8000-000000000606",
  housingRequirement: "00000000-0000-7000-8000-000000000607",
  orientationRequirement: "00000000-0000-7000-8000-000000000608",
  welcomeMessage: "00000000-0000-7000-8000-000000000701",
  reminderMessage: "00000000-0000-7000-8000-000000000702",
  helpGettingStarted: "00000000-0000-7000-8000-000000000801",
  helpDocuments: "00000000-0000-7000-8000-000000000802",
  helpPayments: "00000000-0000-7000-8000-000000000803",
  staffAdvisor: "00000000-0000-7000-8000-000000000901",
  staffReviewer: "00000000-0000-7000-8000-000000000902",
  staffOnboardingWorkItem: "00000000-0000-7000-8000-000000000911",
  staffOutreachWorkItem: "00000000-0000-7000-8000-000000000912",
  staffOnboardingLog: "00000000-0000-7000-8000-000000000921",
  staffOutreachLog: "00000000-0000-7000-8000-000000000922",
});

const seedTimestamp = "2026-07-24T00:00:00.000Z";

const courses = [
  {
    id: "10000000-0000-7000-8000-000000000101",
    code: "MATH 151",
    title: "Calculus I",
    description:
      "Limits, derivatives, applications of differentiation, and an introduction to integration.",
    credits: 4,
    level: 100,
    prerequisites: [],
  },
  {
    id: "10000000-0000-7000-8000-000000000102",
    code: "MATH 152",
    title: "Calculus II",
    description:
      "Techniques and applications of integration, sequences, series, and parametric curves.",
    credits: 4,
    level: 100,
    prerequisites: [{ courseCode: "MATH 151", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000103",
    code: "MATH 251",
    title: "Multivariable Calculus",
    description:
      "Vectors, partial derivatives, multiple integrals, and vector calculus.",
    credits: 4,
    level: 200,
    prerequisites: [{ courseCode: "MATH 152", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000104",
    code: "PHYS 201",
    title: "University Physics I",
    description:
      "Calculus-based mechanics, energy, momentum, rotation, and oscillation.",
    credits: 4,
    level: 200,
    prerequisites: [{ courseCode: "MATH 151", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000201",
    code: "CS 101",
    title: "Programming Fundamentals",
    description:
      "Problem solving, algorithms, program design, and introductory software development.",
    credits: 4,
    level: 100,
    prerequisites: [],
  },
  {
    id: "10000000-0000-7000-8000-000000000202",
    code: "CS 201",
    title: "Data Structures",
    description:
      "Abstract data types, algorithm analysis, linked structures, trees, graphs, and hashing.",
    credits: 4,
    level: 200,
    prerequisites: [{ courseCode: "CS 101", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000203",
    code: "CS 230",
    title: "Computer Systems",
    description:
      "Digital representation, assembly, memory hierarchy, processes, and systems programming.",
    credits: 4,
    level: 200,
    prerequisites: [{ courseCode: "CS 101", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000204",
    code: "CS 310",
    title: "Software Engineering",
    description:
      "Team-based design, testing, delivery, and maintenance of production software systems.",
    credits: 4,
    level: 300,
    prerequisites: [{ courseCode: "CS 201", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000301",
    code: "ENGR 101",
    title: "Engineering Design",
    description:
      "Design thinking, prototyping, technical communication, ethics, and collaborative engineering.",
    credits: 3,
    level: 100,
    prerequisites: [],
  },
  {
    id: "10000000-0000-7000-8000-000000000302",
    code: "ME 210",
    title: "Statics",
    description:
      "Equilibrium of particles and rigid bodies, trusses, frames, friction, and centroids.",
    credits: 3,
    level: 200,
    prerequisites: [
      { courseCode: "MATH 151", minimumGrade: "C" },
      { courseCode: "PHYS 201", minimumGrade: "C" },
    ],
  },
  {
    id: "10000000-0000-7000-8000-000000000303",
    code: "ME 220",
    title: "Dynamics",
    description:
      "Kinematics and kinetics of particles and rigid bodies with engineering applications.",
    credits: 3,
    level: 200,
    prerequisites: [{ courseCode: "ME 210", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000304",
    code: "ME 330",
    title: "Thermodynamics",
    description:
      "Energy, entropy, properties of substances, cycles, and thermodynamic system analysis.",
    credits: 3,
    level: 300,
    prerequisites: [{ courseCode: "MATH 152", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000401",
    code: "BUS 101",
    title: "Foundations of Business",
    description:
      "Organizations, markets, business models, ethics, and the major functional areas of business.",
    credits: 3,
    level: 100,
    prerequisites: [],
  },
  {
    id: "10000000-0000-7000-8000-000000000402",
    code: "ACCT 201",
    title: "Financial Accounting",
    description:
      "Financial statements, the accounting cycle, assets, liabilities, and equity.",
    credits: 3,
    level: 200,
    prerequisites: [],
  },
  {
    id: "10000000-0000-7000-8000-000000000403",
    code: "ECON 201",
    title: "Microeconomics",
    description:
      "Consumer and producer behavior, markets, competition, and public policy.",
    credits: 3,
    level: 200,
    prerequisites: [],
  },
  {
    id: "10000000-0000-7000-8000-000000000404",
    code: "FIN 301",
    title: "Business Finance",
    description:
      "Time value of money, capital budgeting, risk, return, and financing decisions.",
    credits: 3,
    level: 300,
    prerequisites: [
      { courseCode: "ACCT 201", minimumGrade: "C" },
      { courseCode: "ECON 201", minimumGrade: "C" },
    ],
  },
  {
    id: "10000000-0000-7000-8000-000000000405",
    code: "MKTG 301",
    title: "Principles of Marketing",
    description:
      "Customer insight, segmentation, positioning, product, pricing, and channels.",
    credits: 3,
    level: 300,
    prerequisites: [{ courseCode: "BUS 101", minimumGrade: "C" }],
  },
  {
    id: "10000000-0000-7000-8000-000000000501",
    code: "WRIT 101",
    title: "Academic Writing",
    description:
      "Evidence-based writing, research practices, revision, and academic argument.",
    credits: 3,
    level: 100,
    prerequisites: [],
  },
];

const openCourseResources = Object.freeze({
  programming: [
    {
      id: "think-python-2e",
      title: "Think Python, 2nd Edition",
      description: "A beginner-friendly guide to programming and problem solving with Python.",
      url: "https://greenteapress.com/thinkpython2/thinkpython2.pdf",
      format: "pdf",
      provider: "Green Tea Press",
      licenseLabel: "CC BY-NC 3.0",
    },
  ],
  discreteMath: [
    {
      id: "mit-mathematics-for-computer-science",
      title: "Mathematics for Computer Science",
      description: "MIT OpenCourseWare's undergraduate text on proofs, graphs, counting, and probability.",
      url: "https://ocw.mit.edu/courses/6-042j-mathematics-for-computer-science-spring-2015/mit6_042js15_textbook.pdf",
      format: "pdf",
      provider: "MIT OpenCourseWare",
      licenseLabel: "Creative Commons",
    },
  ],
  algorithms: [
    {
      id: "erickson-algorithms",
      title: "Algorithms",
      description: "A rigorous open textbook covering recursion, dynamic programming, graphs, and complexity.",
      url: "https://jeffe.cs.illinois.edu/teaching/algorithms/book/Algorithms-JeffE.pdf",
      format: "pdf",
      provider: "Jeff Erickson · UIUC",
      licenseLabel: "CC BY 4.0",
    },
  ],
  linearAlgebra: [
    {
      id: "hefferon-linear-algebra",
      title: "Linear Algebra",
      description: "A free first-course text with worked examples, exercises, and applications.",
      url: "https://hefferon.net/linearalgebra/book.pdf",
      format: "pdf",
      provider: "Jim Hefferon",
      licenseLabel: "CC BY-SA 4.0",
    },
  ],
});

for (const course of courses) {
  course.resources =
    course.code === "CS 101"
      ? structuredClone(openCourseResources.programming)
      : course.code === "CS 201"
        ? structuredClone(openCourseResources.algorithms)
        : course.code === "MATH 251"
          ? structuredClone(openCourseResources.linearAlgebra)
          : [];
}

const programs = [
  {
    id: "20000000-0000-7000-8000-000000000101",
    code: "BS-CS",
    name: "Computer Science",
    degree: "Bachelor of Science",
    totalCredits: 120,
    description:
      "Builds a foundation in software, algorithms, systems, data, and responsible computing.",
    requirements: [
      ["CS 101", "major_core", 1],
      ["MATH 151", "math_science", 1],
      ["WRIT 101", "general_education", 1],
      ["CS 201", "major_core", 2],
      ["CS 230", "major_core", 2],
      ["MATH 152", "math_science", 2],
      ["CS 310", "major_core", 3],
    ],
  },
  {
    id: "20000000-0000-7000-8000-000000000102",
    code: "BS-ME",
    name: "Mechanical Engineering",
    degree: "Bachelor of Science",
    totalCredits: 128,
    description:
      "Combines mechanics, design, energy systems, mathematics, and hands-on engineering practice.",
    requirements: [
      ["ENGR 101", "major_core", 1],
      ["MATH 151", "math_science", 1],
      ["WRIT 101", "general_education", 1],
      ["MATH 152", "math_science", 2],
      ["PHYS 201", "math_science", 2],
      ["ME 210", "major_core", 3],
      ["ME 220", "major_core", 4],
      ["ME 330", "major_core", 4],
    ],
  },
  {
    id: "20000000-0000-7000-8000-000000000103",
    code: "BBA",
    name: "Business Administration",
    degree: "Bachelor of Business Administration",
    totalCredits: 120,
    description:
      "Develops analytical, financial, managerial, and customer-centered business leadership.",
    requirements: [
      ["BUS 101", "major_core", 1],
      ["WRIT 101", "general_education", 1],
      ["ACCT 201", "major_core", 2],
      ["ECON 201", "major_core", 2],
      ["FIN 301", "major_core", 3],
      ["MKTG 301", "major_core", 3],
    ],
  },
];

export function createSeedState(options = {}) {
  const tenant =
    tenantConfigForSlug(options.tenantSlug) ?? demoTenants.aster;
  const state = {
    schemaVersion: 4,
    fixture: {
      version: FIXTURE_VERSION,
      contentVersion: TENANT_CONTENT_VERSION,
      seededAt: seedTimestamp,
      updatedAt: seedTimestamp,
      revision: 1,
    },
    tenant: publicTenantContext(tenant),
    auth: {
      demoIdentity: {
        actorId: ids.actor,
        studentId: ids.student,
        tenantId: tenant.id,
        displayName: "Alex Morgan",
      },
    },
    staff: {
      members: [
        {
          id: ids.staffAdvisor,
          name: "Priya Shah",
          email: `priya.shah@${tenant.slug}.example.edu`,
          component: "Admissions",
        },
        {
          id: ids.staffReviewer,
          name: "Marcus Lee",
          email: `marcus.lee@${tenant.slug}.example.edu`,
          component: "Registrar",
        },
      ],
      workItems: [
        {
          id: ids.staffOnboardingWorkItem,
          key: "ENR-104",
          studentId: ids.student,
          title: "Review Alex's onboarding support choices",
          description:
            "Confirm residency, housing, and accommodation follow-up choices before the next enrollment milestone.",
          status: "todo",
          priority: "high",
          type: "enrollment",
          component: "Admissions",
          dueAt: "2027-07-29T17:00:00.000Z",
          escalated: false,
          assigneeId: ids.staffAdvisor,
          source: { type: "onboarding", id: ids.student },
          version: 1,
          createdAt: seedTimestamp,
          updatedAt: seedTimestamp,
        },
        {
          id: ids.staffOutreachWorkItem,
          key: "COM-208",
          studentId: ids.student,
          title: "Follow up on enrollment communication preference",
          description:
            "Confirm the best channel for time-sensitive enrollment reminders.",
          status: "in_progress",
          priority: "medium",
          type: "communication",
          component: "Admissions",
          dueAt: "2027-08-01T17:00:00.000Z",
          escalated: false,
          assigneeId: ids.staffAdvisor,
          source: { type: "message", id: ids.reminderMessage },
          version: 1,
          createdAt: seedTimestamp,
          updatedAt: seedTimestamp,
        },
      ],
      workLogs: [
        {
          id: ids.staffOnboardingLog,
          workItemId: ids.staffOnboardingWorkItem,
          action: "created",
          message: "Created from the enrollment onboarding queue.",
          actorName: "VV workflow",
          occurredAt: seedTimestamp,
        },
        {
          id: ids.staffOutreachLog,
          workItemId: ids.staffOutreachWorkItem,
          action: "created",
          message: "Created from the student communication queue.",
          actorName: "VV workflow",
          occurredAt: seedTimestamp,
        },
      ],
      knowledgeBase: [
        {
          id: "00000000-0000-7000-8000-000000000931",
          title: "Enrollment deposit policy",
          summary:
            "Approved guidance for deposit deadlines, waivers, and student escalation.",
          body:
            "Enrollment deposits reserve a place in the incoming class. Staff should verify the offer deadline before discussing extensions or waiver eligibility.",
          category: "Enrollment",
          audience: "internal",
          status: "published",
          owner: "Admissions Operations",
          version: 1,
          updatedAt: seedTimestamp,
        },
        {
          id: "00000000-0000-7000-8000-000000000932",
          title: "Transcript review expectations",
          summary:
            "What students and reviewers should expect after a transcript upload.",
          body:
            "Parsing may continue after the student leaves the page. Extracted fields remain suggestions until a staff reviewer confirms the official document decision.",
          category: "Documents",
          audience: "student",
          status: "published",
          owner: "Registrar",
          version: 1,
          updatedAt: seedTimestamp,
        },
        {
          id: "00000000-0000-7000-8000-000000000933",
          title: "Housing follow-up guide",
          summary:
            "Internal routing notes for undecided and off-campus students.",
          body:
            "Use the housing preference and accommodation-interest fields to route the student to the appropriate advising queue.",
          category: "Housing",
          audience: "internal",
          status: "draft",
          owner: "Student Life",
          version: 1,
          updatedAt: seedTimestamp,
        },
      ],
      corePlays: [
        {
          id: "00000000-0000-7000-8000-000000000941",
          title: "Deposit deadline rescue",
          description:
            "A coordinated sequence for students with an approaching deposit deadline.",
          trigger: "Deposit due within 72 hours and requirement incomplete",
          audience: "Admitted students with incomplete deposits",
          steps: [
            "Verify the student has an active offer",
            "Check for an approved waiver or extension",
            "Draft a concise reminder with the secure payment link",
            "Escalate unresolved cases to the admissions component",
          ],
          status: "active",
          owner: "Admissions Operations",
          version: 1,
          updatedAt: seedTimestamp,
        },
        {
          id: "00000000-0000-7000-8000-000000000942",
          title: "Missing document recovery",
          description:
            "A standard follow-up path for blocking enrollment documents.",
          trigger: "Blocking document is rejected or seven days overdue",
          audience: "Students with blocking document requirements",
          steps: [
            "Confirm the exact document and rejection reason",
            "Draft student-safe resubmission instructions",
            "Create a two-day follow-up task",
          ],
          status: "draft",
          owner: "Registrar",
          version: 1,
          updatedAt: seedTimestamp,
        },
      ],
      journeyBlueprint: [
        {
          id: "onboarding-offer",
          kind: "onboarding",
          title: "Review your offer",
          description: "Confirm the admitted program, term, and campus.",
          owner: "Admissions",
          required: true,
          published: true,
          order: 1,
        },
        {
          id: "onboarding-about-you",
          kind: "onboarding",
          title: "About you",
          description: "Collect student identity and contact preferences.",
          owner: "Admissions",
          required: true,
          published: true,
          order: 2,
        },
        {
          id: "onboarding-housing",
          kind: "onboarding",
          title: "Housing",
          description: "Capture housing plans and accommodation follow-up.",
          owner: "Housing",
          required: true,
          published: true,
          order: 3,
        },
        {
          id: "onboarding-campus-life",
          kind: "onboarding",
          title: "Campus life",
          description: "Let students select interests and communities.",
          owner: "Student Life",
          required: false,
          published: true,
          order: 4,
        },
        {
          id: "onboarding-review",
          kind: "onboarding",
          title: "Review and sign",
          description: "Present the final summary and required consent.",
          owner: "Registrar",
          required: true,
          published: true,
          order: 5,
        },
        {
          id: "enrollment-profile",
          kind: "enrollment",
          title: "Verify your profile",
          description: "Confirm the student's enrollment profile.",
          owner: "Admissions",
          required: true,
          published: true,
          order: 1,
        },
        {
          id: "enrollment-transcript",
          kind: "enrollment",
          title: "Submit an official transcript",
          description: "Upload a transcript for review and credit matching.",
          owner: "Registrar",
          required: true,
          published: true,
          order: 2,
        },
        {
          id: "enrollment-deposit",
          kind: "enrollment",
          title: "Pay your enrollment deposit",
          description: "Complete the enrollment deposit requirement.",
          owner: "Student Accounts",
          required: true,
          published: true,
          order: 3,
        },
        {
          id: "enrollment-orientation",
          kind: "enrollment",
          title: "Register for orientation",
          description: "Choose an available new-student orientation session.",
          owner: "New Student Programs",
          required: true,
          published: true,
          order: 4,
        },
      ],
      outreachRuns: [],
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
    rewards: createRewardState(tenant),
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
    aiProviderResponses: [],
    academicCatalog: {
      version: "2027-2028.v1",
      programs,
      courses,
      equivalencyRules: [
        {
          code: "AP-CALC-AB-4-MATH151",
          sourceType: "ap",
          sourceCode: "AP Calculus AB",
          minimumScore: 4,
          targetCourseCode: "MATH 151",
          confidence: 1,
        },
        {
          code: "AP-CSA-4-CS101",
          sourceType: "ap",
          sourceCode: "AP Computer Science A",
          minimumScore: 4,
          targetCourseCode: "CS 101",
          confidence: 1,
        },
      ],
    },
    academics: {
      selectedProgramCode: "BS-CS",
      transcriptCredits: [
        {
          id: "30000000-0000-7000-8000-000000000101",
          sourceType: "ap",
          sourceCode: "AP Calculus AB",
          title: "AP Calculus AB",
          gradeOrScore: "5",
          credits: null,
          institutionName: "College Board",
          sourceDocumentId: null,
        },
        {
          id: "30000000-0000-7000-8000-000000000102",
          sourceType: "ap",
          sourceCode: "AP Computer Science A",
          title: "AP Computer Science A",
          gradeOrScore: "4",
          credits: null,
          institutionName: "College Board",
          sourceDocumentId: null,
        },
      ],
      exemptionRecommendations: [],
    },
    financials: {
      academicYear: "2027–2028",
      costOfAttendanceCents: 3240000,
      paymentsCents: 0,
      awards: [
        {
          id: "40000000-0000-7000-8000-000000000101",
          source: "federal",
          name: "Federal Pell Grant",
          type: "grant",
          offeredAmountCents: 739500,
          acceptedAmountCents: 739500,
          status: "accepted",
          requiresAction: false,
          updatedAt: seedTimestamp,
        },
        {
          id: "40000000-0000-7000-8000-000000000102",
          source: "institutional",
          name: "Aster Achievement Scholarship",
          type: "scholarship",
          offeredAmountCents: 800000,
          acceptedAmountCents: 800000,
          status: "accepted",
          requiresAction: false,
          updatedAt: seedTimestamp,
        },
        {
          id: "40000000-0000-7000-8000-000000000103",
          source: "federal",
          name: "Direct Subsidized Loan",
          type: "loan",
          offeredAmountCents: 350000,
          acceptedAmountCents: 0,
          status: "offered",
          requiresAction: true,
          updatedAt: seedTimestamp,
        },
        {
          id: "40000000-0000-7000-8000-000000000104",
          source: "federal",
          name: "Federal Work-Study",
          type: "work_study",
          offeredAmountCents: 250000,
          acceptedAmountCents: 0,
          status: "pending",
          requiresAction: false,
          updatedAt: seedTimestamp,
        },
      ],
      requiredDocuments: [
        {
          id: "41000000-0000-7000-8000-000000000101",
          code: "fafsa",
          title: "FAFSA",
          description: "Federal application received and matched to Aster.",
          status: "verified",
          dueAt: null,
          href: "/documents",
          version: 1,
          updatedAt: seedTimestamp,
        },
        {
          id: "41000000-0000-7000-8000-000000000102",
          code: "verification_worksheet",
          title: "Verification worksheet",
          description: "Upload the signed worksheet requested by Financial Aid.",
          status: "action_required",
          dueAt: "2027-08-03T23:59:59.000Z",
          href: "/documents",
          version: 1,
          updatedAt: seedTimestamp,
        },
        {
          id: "41000000-0000-7000-8000-000000000103",
          code: "award_acceptance",
          title: "Award acceptance",
          description: "Review which loans or work-study awards you want to accept.",
          status: "not_started",
          dueAt: "2027-08-10T23:59:59.000Z",
          href: "/financials",
          version: 1,
          updatedAt: seedTimestamp,
        },
      ],
      paymentPlans: [
        {
          id: "42000000-0000-7000-8000-000000000101",
          name: "5-month semester plan",
          installmentCount: 5,
          enrollmentFeeCents: 5000,
          status: "available",
        },
        {
          id: "42000000-0000-7000-8000-000000000102",
          name: "Monthly academic-year plan",
          installmentCount: 10,
          enrollmentFeeCents: 7500,
          status: "available",
        },
      ],
      sap: {
        status: "meeting",
        cumulativeGpa: 3.42,
        minimumGpa: 2,
        completionRatePercent: 78,
        minimumCompletionRatePercent: 67,
        attemptedCredits: 28,
        maximumAttemptedCredits: 180,
      },
    },
    financialAidPolicies: [
      {
        id: "71000000-0000-7000-8000-000000000001",
        topic: "verification_worksheet",
        requirementCode: "verification_worksheet",
        title: "Verification worksheet requirement (synthetic demo)",
        sourceOwner: `${tenant.shortName} Financial Aid (synthetic demo)`,
        version: 1,
        status: "published",
        effectiveFrom: "2026-01-01T00:00:00.000Z",
        effectiveUntil: null,
        academicYear: "2027–2028",
        sectionId: "worksheet-purpose",
        studentVisibleText:
          "This synthetic demo institution uses the verification worksheet to collect student attestations needed for its verification review.",
        citationLabel: "Synthetic demo Financial Aid policy",
        citationUrl:
          "https://example.edu/demo/financial-aid/verification-worksheet",
        synthetic: true,
      },
    ],
    financialAidSupport: {
      email: `financial-aid@${tenant.slug}.example.edu`,
      phone: "+1-555-0107",
      hours: "Monday-Friday, 9:00-17:00 ET",
      appointmentRoute: "/appointments",
      synthetic: true,
    },
    campusLife: {
      events: [
        {
          id: "50000000-0000-7000-8000-000000000101",
          title: "Welcome Week Block Party",
          description:
            "Food, music, student organizations, and a relaxed first look at campus life.",
          startsAt: "2027-08-28T18:00:00.000Z",
          endsAt: "2027-08-28T21:00:00.000Z",
          location: "University Green",
          category: "social",
          featured: true,
          accent: "gold",
          visualTheme: "festival",
          imageUrl: "/media/events/welcome-week-block-party.webp",
          imageAlt:
            "Students enjoying music, food stalls, and conversation at a welcome-week block party on a campus lawn",
          imageAttribution: "Original portal artwork generated with OpenAI",
          imageSourceUrl: null,
        },
        {
          id: "50000000-0000-7000-8000-000000000102",
          title: "First-Year Research Showcase",
          description:
            "Meet faculty mentors and discover research opportunities open to first-year students.",
          startsAt: "2027-09-02T16:00:00.000Z",
          endsAt: "2027-09-02T18:00:00.000Z",
          location: "Innovation Hall",
          category: "academic",
          featured: true,
          accent: "blue",
          visualTheme: "discovery",
          imageUrl: "/media/events/first-year-research-showcase.webp",
          imageAlt:
            "Students presenting robotics projects and research posters in a university innovation hall",
          imageAttribution: "Original portal artwork generated with OpenAI",
          imageSourceUrl: null,
        },
        {
          id: "50000000-0000-7000-8000-000000000103",
          title: "Internship Ready Lab",
          description:
            "Bring your résumé for a quick review and practice a two-minute introduction.",
          startsAt: "2027-09-08T15:30:00.000Z",
          endsAt: "2027-09-08T17:00:00.000Z",
          location: "Career Commons",
          category: "career",
          featured: true,
          accent: "navy",
          visualTheme: "career",
          imageUrl: "/media/events/internship-ready-lab.webp",
          imageAlt:
            "Students working with career coaches on resumes and interview practice in a campus career commons",
          imageAttribution: "Original portal artwork generated with OpenAI",
          imageSourceUrl: null,
        },
      ],
      clubs: [
        {
          id: "51000000-0000-7000-8000-000000000101",
          name: "Aster Robotics",
          category: "Engineering & Technology",
          description:
            "Design, build, and compete with autonomous robots in multidisciplinary teams.",
          contactName: "Maya Chen",
          contactRole: "Club President",
          contactChannel: "robotics@aster.edu",
          latestUpdate: "New-member build teams open this week.",
          nextActivity: "Open Lab · Sep 4, 6:00 PM",
          imageUrl: "/media/clubs/robotics.jpg",
          imageAlt:
            "Students collaborating on a robotics project in a workshop",
          imageAttribution: "Photo by Vanessa Loring via Pexels",
          imageSourceUrl:
            "https://www.pexels.com/photo/young-students-doing-robotics-together-7869041/",
        },
        {
          id: "51000000-0000-7000-8000-000000000102",
          name: "Code Collective",
          category: "Computing",
          description:
            "Peer learning, hack nights, open-source projects, and conversations with alumni.",
          contactName: "Noah Williams",
          contactRole: "Community Lead",
          contactChannel: "codecollective@aster.edu",
          latestUpdate: "Fall project pitches are now posted.",
          nextActivity: "Hack Night · Sep 6, 7:00 PM",
          imageUrl: "/media/clubs/code-collective.jpg",
          imageAlt:
            "College students researching together around a library table",
          imageAttribution: "Photo by Tima Miroshnichenko via Pexels",
          imageSourceUrl:
            "https://www.pexels.com/photo/college-students-studying-and-researching-6549913/",
        },
        {
          id: "51000000-0000-7000-8000-000000000103",
          name: "Women in Business",
          category: "Professional",
          description:
            "Mentoring, leadership workshops, community projects, and employer networking.",
          contactName: "Jordan Ellis",
          contactRole: "Membership Chair",
          contactChannel: "wib@aster.edu",
          latestUpdate: "Peer mentor matching closes Friday.",
          nextActivity: "Coffee & Careers · Sep 7, 4:30 PM",
          imageUrl: "/media/clubs/women-in-business.jpg",
          imageAlt:
            "Women collaborating around documents during a business workshop",
          imageAttribution: "Photo by RDNE Stock project via Pexels",
          imageSourceUrl:
            "https://www.pexels.com/photo/businesswomen-in-a-meeting-7648511/",
        },
        {
          id: "51000000-0000-7000-8000-000000000104",
          name: "Outdoor Aster",
          category: "Recreation & Wellness",
          description:
            "Low-cost hikes, climbing sessions, service trips, and outdoor skills workshops.",
          contactName: "Eli Torres",
          contactRole: "Trip Coordinator",
          contactChannel: "outdoors@aster.edu",
          latestUpdate: "Beginner hike registration is open.",
          nextActivity: "Trail Basics · Sep 9, 9:00 AM",
          imageUrl: "/media/clubs/outdoor-aster.jpg",
          imageAlt:
            "A group of friends hiking together on a forest trail",
          imageAttribution: "Photo by Gustavo Denuncio via Pexels",
          imageSourceUrl:
            "https://www.pexels.com/photo/group-of-friends-hiking-in-forest-trail-30273507/",
        },
      ],
    },
    appointments: [],
    payments: [],
    helpRequests: [
      {
        id: "00000000-0000-7000-8000-000000000951",
        topicCode: "documents",
        subject: "Which transcript should I upload?",
        message:
          "I completed dual enrollment at two schools. Should I upload both transcripts or only the most recent one?",
        status: "new",
        priority: "high",
        assigneeId: null,
        createdAt: "2026-07-24T10:30:00.000Z",
        updatedAt: "2026-07-24T10:30:00.000Z",
        version: 1,
      },
    ],
    activities: [],
    idempotency: {},
  };
  if (options.studentId) {
    state.auth.demoIdentity.studentId = options.studentId;
    state.profile.studentId = options.studentId;
  }
  if (options.actorId) {
    state.auth.demoIdentity.actorId = options.actorId;
  }
  if (options.phone) {
    state.profile.mobilePhone = options.phone;
    state.onboarding.data.mobilePhone = options.phone;
  }
  if (options.email) {
    state.profile.email = options.email;
    state.profile.emailVerified = false;
    state.profile.phoneVerified = false;
  }
  if (options.freshStudent) {
    state.auth.demoIdentity.displayName = "New student";
    state.profile.preferredName = "Student";
    state.profile.firstName = "";
    state.profile.lastName = "";
    state.messages[0].body =
      `Welcome to ${tenant.shortName}. Complete onboarding to create your student profile.`;
    state.academics.transcriptCredits = [];
    state.academics.exemptions = [];
  }
  // This accepted-student variant is fictional V1 fixture state only. New
  // credential signups deliberately do not opt into it: acceptance must come
  // from the demo offer workflow, never from account creation alone.
  if (options.acceptedStudent || options.completedOnboarding) {
    const acceptedAt = "2026-07-24T12:00:00.000Z";
    state.offer.status = "accepted";
    state.offer.acceptedAt = acceptedAt;
    state.offer.version = 2;
    state.journey = createJourney(acceptedAt);
    state.requirements = createRequirements(acceptedAt);
    const completedProfile = state.requirements.find(
      (requirement) => requirement.code === "profile_verification",
    );
    if (completedProfile) {
      completedProfile.status = "completed";
      completedProfile.progressPercent = 100;
    }
    for (const requirement of state.requirements) {
      if (
        requirement.status === "blocked" &&
        requirement.dependsOnCodes.length === 1 &&
        requirement.dependsOnCodes[0] === "profile_verification"
      ) {
        requirement.status = "ready";
      }
    }
    state.profile.version = 2;
    state.profile.updatedAt = acceptedAt;
    state.onboarding = {
      ...state.onboarding,
      currentStep: "housing",
      completedSteps: ["offer", "about_you"],
      data: {
        firstName: state.profile.firstName,
        lastName: state.profile.lastName,
        preferredName: state.profile.preferredName,
        mobilePhone: state.profile.mobilePhone,
        communicationPreference: state.profile.communicationPreference,
        skippedSteps: [],
      },
      version: 3,
      updatedAt: acceptedAt,
    };
    state.journey.version = 2;
    state.portalProjectionVersion = 2;
  }
  if (options.completedOnboarding) {
    const acceptedAt = state.offer.acceptedAt;
    state.onboarding = {
      status: "completed",
      currentStep: "deposit",
      completedAt: acceptedAt,
      completedSteps: [...ONBOARDING_STEPS],
      data: { skippedSteps: ["deposit"] },
      version: 10,
      updatedAt: acceptedAt,
    };
  }
  customizeSeedForTenant(state, tenant);
  normalizeTenantContent(state);
  state.staff.managedConfigurations = createManagedConfigurations(
    state,
    tenant,
    seedTimestamp,
  );
  seedStaffCohort(state, tenant, 400);
  return state;
}

function customizeSeedForTenant(state, tenant) {
  if (tenant.slug === "aster") return;

  state.offer.campusName = "Cambridge Campus";
  state.messages[0].subject = `Welcome to ${tenant.name}`;
  state.messages[0].body = state.messages[0].body.replaceAll(
    "Aster",
    tenant.shortName,
  );
  for (const message of state.messages) {
    message.senderName = `${tenant.shortName} Enrollment Team`;
  }

  const institutionalAward = state.financials.awards.find(
    (award) => award.source === "institutional",
  );
  if (institutionalAward) {
    institutionalAward.name = `${tenant.shortName} Achievement Scholarship`;
  }
  for (const document of state.financials.requiredDocuments) {
    document.description = document.description.replaceAll(
      "Aster",
      tenant.shortName,
    );
  }

  state.offer.programName = "Computer Science Concentration";
  state.academicCatalog = {
    version: harvardAcademicContent.version,
    programs: structuredClone(harvardAcademicContent.programs),
    courses: structuredClone(harvardAcademicContent.courses),
    equivalencyRules: [],
  };
  state.academics.selectedProgramCode = "AB-CS";
  state.academics.transcriptCredits = [];
  state.academics.exemptionRecommendations = [];
  state.campusLife = structuredClone(harvardCampusLifeContent);

  for (const requirement of state.requirements) {
    requirement.title = requirement.title.replaceAll("Aster", tenant.shortName);
    requirement.description = requirement.description.replaceAll(
      "Aster",
      tenant.shortName,
    );
  }
}

function normalizeTenantContent(state) {
  for (const program of state.academicCatalog.programs) {
    program.source ??= null;
  }
  for (const course of state.academicCatalog.courses) {
    course.availabilityLabel ??= "2027–2028 tenant catalog preview";
    course.instructorNames ??= [];
    course.meetingPattern ??= null;
    course.source ??= null;
    course.resources ??= [];
  }
  for (const event of state.campusLife.events) {
    event.source ??= null;
    event.registrationUrl ??= null;
    event.visualTheme ??=
      event.category === "career"
        ? "career"
        : event.category === "academic"
          ? "discovery"
          : event.category === "wellness"
            ? "community"
            : "festival";
    event.imageUrl ??= null;
    event.imageAlt ??= null;
    event.imageAttribution ??= null;
    event.imageSourceUrl ??= null;
  }
  for (const [index, club] of state.campusLife.clubs.entries()) {
    club.source ??= null;
    club.socialLinks ??= [];
    club.longDescription ??=
      `${club.description} New members can meet the team, explore current projects, and take part at their own pace.`;
    club.meetingSchedule ??=
      index % 2 === 0
        ? "Weekly · Thursdays at 6:00 PM"
        : "Every other week · Tuesdays at 7:00 PM";
    club.membershipOpen ??= true;
    club.version ??= 1;
    club.updatedAt ??= seedTimestamp;
    club.events ??= [
      {
        id: `${club.id}-welcome`,
        title: `${club.name} welcome meetup`,
        description:
          "Meet student leaders, hear what the club is working on, and find a comfortable first way to participate.",
        startsAt: `2027-09-${String(4 + index).padStart(2, "0")}T22:00:00.000Z`,
        endsAt: `2027-09-${String(4 + index).padStart(2, "0")}T23:30:00.000Z`,
        location: index % 2 === 0 ? "Student Commons · Studio A" : "Campus Center · Room 204",
        category: "social",
        registrationUrl: null,
      },
      {
        id: `${club.id}-workshop`,
        title: `${club.name} hands-on session`,
        description:
          "A guided, beginner-friendly session led by returning members. Materials and support are provided.",
        startsAt: `2027-09-${String(12 + index).padStart(2, "0")}T21:00:00.000Z`,
        endsAt: `2027-09-${String(12 + index).padStart(2, "0")}T23:00:00.000Z`,
        location: "Innovation Hall · Collaboration Lab",
        category: "workshop",
        registrationUrl: null,
      },
      {
        id: `${club.id}-community`,
        title: "Open community night",
        description:
          "Bring a friend, meet other members, and preview the club's projects and calendar for the semester.",
        startsAt: `2027-09-${String(20 + index).padStart(2, "0")}T23:00:00.000Z`,
        endsAt: `2027-09-${String(21 + index).padStart(2, "0")}T00:30:00.000Z`,
        location: "University Green",
        category: index === 3 ? "service" : "meeting",
        registrationUrl: null,
      },
    ];
  }
}

function seedStaffCohort(state, tenant, count) {
  const firstNames = [
    "Avery",
    "Jordan",
    "Taylor",
    "Maya",
    "Noah",
    "Sophia",
    "Ethan",
    "Olivia",
    "Lucas",
    "Amara",
    "Mateo",
    "Nora",
    "Elijah",
    "Zoe",
    "Kai",
    "Leila",
  ];
  const lastNames = [
    "Carter",
    "Nguyen",
    "Rivera",
    "Patel",
    "Williams",
    "Kim",
    "Johnson",
    "Garcia",
    "Brown",
    "Davis",
    "Wilson",
    "Martinez",
    "Anderson",
    "Clark",
    "Lewis",
    "Walker",
  ];
  const programs = [
    state.offer.programName,
    "Biology",
    "Business Administration",
    "Mechanical Engineering",
    "Psychology",
    "Data Science",
  ];
  const diagnoses = [
    {
      category: "financial",
      reason:
        "Financial aid verification is incomplete after the award letter was opened twice.",
      signals: [
        "Aid package opened",
        "Verification incomplete",
        "Deposit not submitted",
      ],
      action: "Call to explain verification and net cost",
      channel: "voice",
      expectedImpact: "Reduce affordability uncertainty",
    },
    {
      category: "belonging",
      reason:
        "The student attended an admitted event but has not engaged with a club or peer since.",
      signals: [
        "Admitted event attended",
        "No club engagement",
        "Portal activity declining",
      ],
      action: "Connect with a peer ambassador",
      channel: "email",
      expectedImpact: "Strengthen campus connection",
    },
    {
      category: "administrative",
      reason:
        "A blocking enrollment document remains incomplete near the due date.",
      signals: [
        "Document missing",
        "Deadline within 72 hours",
        "Checklist revisited",
      ],
      action: "Send the exact completion path and follow up",
      channel: "sms",
      expectedImpact: "Remove the enrollment blocker",
    },
    {
      category: "academic",
      reason:
        "The student changed academic interests and repeatedly viewed curriculum pages.",
      signals: [
        "Program interest changed",
        "Curriculum viewed four times",
        "No faculty interaction",
      ],
      action: "Arrange a faculty conversation",
      channel: "email",
      expectedImpact: "Increase confidence in program fit",
    },
    {
      category: "engagement",
      reason:
        "Portal and message engagement fell sharply after an initially active period.",
      signals: [
        "No portal login for 12 days",
        "Two unread reminders",
        "Previously high engagement",
      ],
      action: "Make a personal counselor call",
      channel: "voice",
      expectedImpact: "Re-engage before the deposit deadline",
    },
  ];

  const cohort = [
    {
      id: state.profile.studentId,
      name: `${state.profile.firstName} ${state.profile.lastName}`,
      preferredName: state.profile.preferredName,
      programName: state.offer.programName,
      classYear: state.profile.classYear,
      assignedStaffId: ids.staffAdvisor,
      syntheticSeed: true,
      journey: {
        stage: "Offer accepted",
        completedTasks: 4,
        totalTasks: 9,
        lastActivityAt: "2026-07-30T17:45:00.000Z",
      },
      risk: {
        score: 82,
        band: "high",
        category: "financial",
        meltLikelihoodPercent: 61,
        recoveryLikelihoodPercent: 68,
        reason:
          "Financial aid verification is incomplete and the deposit deadline is approaching.",
        signals: [
          "Aid package opened twice",
          "Verification worksheet incomplete",
          "No portal activity for eight days",
        ],
        modelVersion: "melt-diagnostic-v1",
        evaluatedAt: "2026-07-31T06:00:00.000Z",
      },
      recommendedAction: {
        title: "Call to explain financial aid verification",
        rationale:
          "A personal explanation is the highest-confidence intervention for this risk pattern.",
        channel: "voice",
        expectedImpact: "68% modeled recovery opportunity",
        taskId: ids.staffOnboardingWorkItem,
        recommendedToday: true,
      },
      communicationHistory: [
        {
          id: "seed-communication-alex-1",
          channel: "email",
          direction: "outbound",
          summary: "Sent financial aid checklist and secure verification link.",
          outcome: "opened",
          occurredAt: "2026-07-28T14:20:00.000Z",
        },
        {
          id: "seed-communication-alex-2",
          channel: "portal",
          direction: "inbound",
          summary: "Asked whether the scholarship changes the amount due.",
          outcome: "needs_follow_up",
          occurredAt: "2026-07-29T18:12:00.000Z",
        },
      ],
    },
  ];

  for (let index = 1; index < count; index += 1) {
    const firstName = firstNames[index % firstNames.length];
    const lastName = lastNames[(index * 7) % lastNames.length];
    const studentId = deterministicSeedUuid("60", index);
    const diagnosis = diagnoses[index % diagnoses.length];
    const score = 38 + ((index * 17) % 59);
    const band =
      score >= 88
        ? "critical"
        : score >= 72
          ? "high"
          : score >= 55
            ? "medium"
            : "low";
    const assignedStaffId =
      index <= 29 || index % 3 !== 0
        ? ids.staffAdvisor
        : ids.staffReviewer;
    const taskId = index <= 120 ? deterministicSeedUuid("61", index) : null;
    cohort.push({
      id: studentId,
      name: `${firstName} ${lastName}`,
      preferredName: firstName,
      programName: programs[index % programs.length],
      classYear: 2027,
      assignedStaffId,
      syntheticSeed: true,
      journey: {
        stage:
          index % 4 === 0
            ? "Deposit pending"
            : index % 4 === 1
              ? "Documents"
              : index % 4 === 2
                ? "Housing"
                : "Orientation",
        completedTasks: 2 + (index % 6),
        totalTasks: 9,
        lastActivityAt: new Date(
          Date.parse("2026-07-31T08:00:00.000Z") -
            (index % 18) * 86_400_000,
        ).toISOString(),
      },
      risk: {
        score,
        band,
        category: diagnosis.category,
        meltLikelihoodPercent: Math.min(92, 18 + Math.round(score * 0.7)),
        recoveryLikelihoodPercent: Math.max(
          24,
          82 - Math.round(score * 0.22),
        ),
        reason: diagnosis.reason,
        signals: diagnosis.signals,
        modelVersion: "melt-diagnostic-v1",
        evaluatedAt: "2026-07-31T06:00:00.000Z",
      },
      recommendedAction: {
        title: diagnosis.action,
        rationale:
          "Recommended from observable engagement and enrollment-state signals; staff judgment is required.",
        channel: diagnosis.channel,
        expectedImpact: diagnosis.expectedImpact,
        taskId,
        recommendedToday: index <= 29,
      },
      communicationHistory: [
        {
          id: `seed-communication-${index}-1`,
          channel: index % 2 === 0 ? "email" : "sms",
          direction: "outbound",
          summary:
            index % 3 === 0
              ? "Shared a personalized checklist reminder."
              : "Sent a counselor introduction and next-step link.",
          outcome: index % 4 === 0 ? "no_response" : "opened",
          occurredAt: new Date(
            Date.parse("2026-07-30T15:00:00.000Z") -
              (index % 10) * 86_400_000,
          ).toISOString(),
        },
        {
          id: `seed-communication-${index}-2`,
          channel: "portal",
          direction: index % 5 === 0 ? "inbound" : "outbound",
          summary:
            index % 5 === 0
              ? "Student asked for clarification about the next deadline."
              : "Posted an enrollment-center notification.",
          outcome: index % 5 === 0 ? "needs_follow_up" : "delivered",
          occurredAt: new Date(
            Date.parse("2026-07-28T12:00:00.000Z") -
              (index % 7) * 86_400_000,
          ).toISOString(),
        },
      ],
    });

    if (taskId) {
      const status =
        index % 11 === 0
          ? "done"
          : index % 4 === 0
            ? "in_progress"
            : "todo";
      state.staff.workItems.push({
        id: taskId,
        key: `ENR-${String(300 + index).padStart(3, "0")}`,
        studentId,
        title: diagnosis.action,
        description: diagnosis.reason,
        status,
        priority:
          band === "critical"
            ? "urgent"
            : band === "high"
              ? "high"
              : band === "medium"
                ? "medium"
                : "low",
        type:
          diagnosis.category === "administrative"
            ? "document_review"
            : diagnosis.category === "engagement"
              ? "communication"
              : "enrollment",
        component:
          diagnosis.category === "financial"
            ? "Financial Aid"
            : diagnosis.category === "administrative"
              ? "Registrar"
              : "Admissions",
        dueAt: new Date(
          Date.parse("2026-07-31T17:00:00.000Z") +
            (index % 5) * 86_400_000,
        ).toISOString(),
        escalated: band === "critical",
        assigneeId: assignedStaffId,
        source: { type: "onboarding", id: studentId },
        version: 1,
        createdAt: seedTimestamp,
        updatedAt: seedTimestamp,
      });
      state.staff.workLogs.push({
        id: deterministicSeedUuid("62", index),
        workItemId: taskId,
        action: "created",
        message: "Created from the deterministic staff cohort test seed.",
        actorName: "VV workflow",
        occurredAt: seedTimestamp,
      });
    }
  }

  state.staff.cohort = cohort;
  state.staff.cohortSeed = {
    synthetic: true,
    count,
    purpose: "Deterministic staff workflow and scale testing only",
    generatedAt: seedTimestamp,
    tenantSlug: tenant.slug,
  };
}

function deterministicSeedUuid(prefix, index) {
  return `${prefix}000000-0000-7000-8000-${String(index).padStart(12, "0")}`;
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
      id: ids.transcriptRequirement,
      code: "official_transcript",
      title: "Submit your official transcript",
      description:
        "Upload your transcript for document review and potential course-credit matching.",
      status: "blocked",
      blocking: true,
      dueAt: dueAt(14),
      progressPercent: 0,
      dependsOnCodes: ["profile_verification"],
      submissionType: "document",
      responsibleOffice: "Registrar",
    },
    {
      id: ids.financialRequirement,
      code: "financial_aid_verification",
      title: "Complete financial-aid verification",
      description:
        "Submit the requested verification worksheet and review your aid package.",
      status: "ready",
      blocking: true,
      dueAt: dueAt(10),
      progressPercent: 35,
      dependsOnCodes: [],
      submissionType: "document",
      responsibleOffice: "Financial Aid",
    },
    {
      id: ids.healthRequirement,
      code: "immunization_record",
      title: "Provide immunization records",
      description:
        "Upload the required health clearance documentation before arrival.",
      status: "blocked",
      blocking: true,
      dueAt: dueAt(30),
      progressPercent: 0,
      dependsOnCodes: ["profile_verification"],
      submissionType: "document",
      responsibleOffice: "Student Health",
    },
    {
      id: ids.housingRequirement,
      code: "housing_preference",
      title: "Confirm housing plans",
      description:
        "Tell Aster whether you plan to live on campus, off campus, or are undecided.",
      status: "ready",
      blocking: false,
      dueAt: dueAt(18),
      progressPercent: 50,
      dependsOnCodes: [],
      submissionType: "form",
      responsibleOffice: "Housing & Residence Life",
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
    {
      id: ids.orientationRequirement,
      code: "orientation_registration",
      title: "Register for orientation",
      description:
        "Choose an orientation session after your deposit and core records are complete.",
      status: "blocked",
      blocking: true,
      dueAt: dueAt(35),
      progressPercent: 0,
      dependsOnCodes: ["enrollment_deposit", "identity_document"],
      submissionType: "form",
      responsibleOffice: "New Student Programs",
    },
  ];
}

function createRewardState(tenant) {
  const pageRewards = [
    ["dashboard", 5],
    ["enrollment", 5],
    ["financials", 10],
    ["classrooms", 15],
    ["campus_life", 15],
    ["edward", 10],
    ["documents", 10],
    ["profile", 5],
  ];
  const taskRewards = [
    ["profile_verification", "Verify your profile", 30],
    ["identity_document", "Provide identity documentation", 40],
    ["official_transcript", "Submit an official transcript", 80],
    ["financial_aid_verification", "Complete financial-aid verification", 50],
    ["immunization_record", "Provide immunization records", 60],
    ["housing_preference", "Confirm housing plans", 25],
    ["enrollment_deposit", "Complete the enrollment deposit", 50],
    ["orientation_registration", "Register for orientation", 40],
  ];
  const rules = [
    {
      code: "complete_onboarding",
      title: "Complete onboarding",
      description: "Finish every required onboarding stage.",
      triggerType: "onboarding_completed",
      triggerKey: "onboarding",
      triggerProperties: {},
      points: 100,
    },
    ...taskRewards.map(([triggerKey, title, points]) => ({
      code: `complete_${triggerKey}`,
      title,
      description: `Complete ${String(title).toLowerCase()}.`,
      triggerType: "requirement_completed",
      triggerKey,
      triggerProperties: {},
      points,
    })),
    ...pageRewards.map(([section, points]) => ({
      code: `explore_${section}`,
      title: `Explore ${String(section).replaceAll("_", " ")}`,
      description: "Visit this student-portal section for the first time.",
      triggerType: "activity_event",
      triggerKey: "ui.portal_section_viewed.v1",
      triggerProperties: { section },
      points,
    })),
  ].map((rule, index) => ({
    id: `reward-rule:${tenant.slug}:${rule.code}`,
    ...rule,
    maxAwardsPerStudent: 1,
    displayOrder: index,
    enabled: true,
  }));
  return {
    program: {
      pointName: `${tenant.shortName} Points`,
      pointsPerUsd: 100,
      enabled: true,
    },
    rules,
    ledger: [],
  };
}
