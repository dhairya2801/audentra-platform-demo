import { Pool } from "pg";
import {
  DEMO_IDS,
  loadAppConfig,
} from "../config/app-config";

async function main(): Promise<void> {
  const config = loadAppConfig();
  const pool = new Pool({
    connectionString: config.databaseUrl,
    application_name: "vv-api-seeder",
  });
  const client = await pool.connect();
  const initialDashboard = {
    student: {
      id: DEMO_IDS.studentId,
      preferredName: "Alex",
      fullName: "Alex Morgan",
      classYear: 2027,
    },
    offer: {
      id: DEMO_IDS.offerId,
      programName: "Computer Science",
      termName: "Fall 2027",
      campusName: "Main Campus",
      responseDeadline: "2027-08-15",
      depositAmountCents: 50000,
      status: "offered",
    },
    journey: {
      id: null,
      status: "not_started",
      completionPercent: 0,
      nextAction: {
        code: "accept_offer",
        label: "Review and accept your offer",
        href: "/offer",
      },
      requirements: [],
    },
    unreadMessageCount: 0,
    projectionVersion: 1,
    generatedAt: "2026-07-24T00:00:00.000Z",
  };

  try {
    await client.query("BEGIN");
    await client.query(
      `INSERT INTO tenant (id, name)
       VALUES ($1, 'VV Demo University')
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.tenantId],
    );
    await client.query(
      `INSERT INTO person (
         id, tenant_id, preferred_name, first_name, last_name
       )
       VALUES ($1, $2, 'Alex', 'Alex', 'Morgan')
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.personId, DEMO_IDS.tenantId],
    );
    await client.query(
      `INSERT INTO student (
         id, tenant_id, person_id, class_year
       )
       VALUES ($1, $2, $3, 2027)
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.studentId, DEMO_IDS.tenantId, DEMO_IDS.personId],
    );
    await client.query(
      `INSERT INTO campus (id, tenant_id, name)
       VALUES ($1, $2, 'Main Campus')
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.campusId, DEMO_IDS.tenantId],
    );
    await client.query(
      `INSERT INTO academic_term (id, tenant_id, name, starts_on)
       VALUES ($1, $2, 'Fall 2027', '2027-09-01')
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.academicTermId, DEMO_IDS.tenantId],
    );
    await client.query(
      `INSERT INTO program (
         id, tenant_id, code, name, degree, total_credits, description
       )
       VALUES (
         $1, $2, 'BS-CS', 'Computer Science', 'Bachelor of Science', 120,
         'Builds a foundation in software, algorithms, systems, data, and responsible computing.'
       )
       ON CONFLICT (id) DO UPDATE SET
         code = EXCLUDED.code,
         degree = EXCLUDED.degree,
         total_credits = EXCLUDED.total_credits,
         description = EXCLUDED.description`,
      [DEMO_IDS.programId, DEMO_IDS.tenantId],
    );
    const mechanicalProgramId = "20000000-0000-7000-8000-000000000102";
    const businessProgramId = "20000000-0000-7000-8000-000000000103";
    const additionalPrograms = [
      {
        id: mechanicalProgramId,
        code: "BS-ME",
        name: "Mechanical Engineering",
        degree: "Bachelor of Science",
        totalCredits: 128,
        description:
          "Combines mechanics, design, energy systems, mathematics, and hands-on engineering practice.",
      },
      {
        id: businessProgramId,
        code: "BBA",
        name: "Business Administration",
        degree: "Bachelor of Business Administration",
        totalCredits: 120,
        description:
          "Develops analytical, financial, managerial, and customer-centered business leadership.",
      },
    ];
    for (const item of additionalPrograms) {
      await client.query(
        `INSERT INTO program (
           id, tenant_id, code, name, degree, total_credits, description
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT (id) DO UPDATE SET
           code = EXCLUDED.code,
           name = EXCLUDED.name,
           degree = EXCLUDED.degree,
           total_credits = EXCLUDED.total_credits,
           description = EXCLUDED.description`,
        [
          item.id,
          DEMO_IDS.tenantId,
          item.code,
          item.name,
          item.degree,
          item.totalCredits,
          item.description,
        ],
      );
    }

    const catalogId = "21000000-0000-7000-8000-000000000101";
    await client.query(
      `INSERT INTO course_catalog_version (
         id, tenant_id, code, effective_from, effective_until, status
       )
       VALUES ($1, $2, '2027-2028.v1', '2027-07-01', '2028-06-30', 'active')
       ON CONFLICT (id) DO NOTHING`,
      [catalogId, DEMO_IDS.tenantId],
    );
    const catalogCourses = [
      ["10000000-0000-7000-8000-000000000101", "MATH 151", "Calculus I", "Limits, derivatives, applications of differentiation, and an introduction to integration.", 4, 100],
      ["10000000-0000-7000-8000-000000000102", "MATH 152", "Calculus II", "Techniques and applications of integration, sequences, series, and parametric curves.", 4, 100],
      ["10000000-0000-7000-8000-000000000103", "MATH 251", "Multivariable Calculus", "Vectors, partial derivatives, multiple integrals, and vector calculus.", 4, 200],
      ["10000000-0000-7000-8000-000000000104", "PHYS 201", "University Physics I", "Calculus-based mechanics, energy, momentum, rotation, and oscillation.", 4, 200],
      ["10000000-0000-7000-8000-000000000201", "CS 101", "Programming Fundamentals", "Problem solving, algorithms, program design, and introductory software development.", 4, 100],
      ["10000000-0000-7000-8000-000000000202", "CS 201", "Data Structures", "Abstract data types, algorithm analysis, linked structures, trees, graphs, and hashing.", 4, 200],
      ["10000000-0000-7000-8000-000000000203", "CS 230", "Computer Systems", "Digital representation, assembly, memory hierarchy, processes, and systems programming.", 4, 200],
      ["10000000-0000-7000-8000-000000000204", "CS 310", "Software Engineering", "Team-based design, testing, delivery, and maintenance of production software systems.", 4, 300],
      ["10000000-0000-7000-8000-000000000301", "ENGR 101", "Engineering Design", "Design thinking, prototyping, technical communication, ethics, and collaborative engineering.", 3, 100],
      ["10000000-0000-7000-8000-000000000302", "ME 210", "Statics", "Equilibrium of particles and rigid bodies, trusses, frames, friction, and centroids.", 3, 200],
      ["10000000-0000-7000-8000-000000000303", "ME 220", "Dynamics", "Kinematics and kinetics of particles and rigid bodies with engineering applications.", 3, 200],
      ["10000000-0000-7000-8000-000000000304", "ME 330", "Thermodynamics", "Energy, entropy, properties of substances, cycles, and thermodynamic system analysis.", 3, 300],
      ["10000000-0000-7000-8000-000000000401", "BUS 101", "Foundations of Business", "Organizations, markets, business models, ethics, and the major functional areas of business.", 3, 100],
      ["10000000-0000-7000-8000-000000000402", "ACCT 201", "Financial Accounting", "Financial statements, the accounting cycle, assets, liabilities, and equity.", 3, 200],
      ["10000000-0000-7000-8000-000000000403", "ECON 201", "Microeconomics", "Consumer and producer behavior, markets, competition, and public policy.", 3, 200],
      ["10000000-0000-7000-8000-000000000404", "FIN 301", "Business Finance", "Time value of money, capital budgeting, risk, return, and financing decisions.", 3, 300],
      ["10000000-0000-7000-8000-000000000405", "MKTG 301", "Principles of Marketing", "Customer insight, segmentation, positioning, product, pricing, and channels.", 3, 300],
      ["10000000-0000-7000-8000-000000000501", "WRIT 101", "Academic Writing", "Evidence-based writing, research practices, revision, and academic argument.", 3, 100],
    ] as const;
    for (const course of catalogCourses) {
      await client.query(
        `INSERT INTO catalog_course (
           id, tenant_id, catalog_version_id, code, title, description, credits, level
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
         ON CONFLICT (id) DO UPDATE SET
           title = EXCLUDED.title,
           description = EXCLUDED.description,
           credits = EXCLUDED.credits,
           level = EXCLUDED.level`,
        [
          course[0],
          DEMO_IDS.tenantId,
          catalogId,
          course[1],
          course[2],
          course[3],
          course[4],
          course[5],
        ],
      );
    }
    const courseIdByCode = new Map<string, string>(
      catalogCourses.map((course) => [course[1], course[0]]),
    );
    const courseId = (code: string) => {
      const id = courseIdByCode.get(code);
      if (!id) throw new Error(`Missing seeded course ${code}`);
      return id;
    };
    const prerequisites = [
      ["MATH 152", "MATH 151"],
      ["MATH 251", "MATH 152"],
      ["PHYS 201", "MATH 151"],
      ["CS 201", "CS 101"],
      ["CS 230", "CS 101"],
      ["CS 310", "CS 201"],
      ["ME 210", "MATH 151"],
      ["ME 210", "PHYS 201"],
      ["ME 220", "ME 210"],
      ["ME 330", "MATH 152"],
      ["FIN 301", "ACCT 201"],
      ["FIN 301", "ECON 201"],
      ["MKTG 301", "BUS 101"],
    ] as const;
    for (const [courseCode, prerequisiteCode] of prerequisites) {
      await client.query(
        `INSERT INTO course_prerequisite (
           tenant_id, catalog_version_id, course_id, prerequisite_course_id, minimum_grade
         )
         VALUES ($1, $2, $3, $4, 'C')
         ON CONFLICT DO NOTHING`,
        [
          DEMO_IDS.tenantId,
          catalogId,
          courseId(courseCode),
          courseId(prerequisiteCode),
        ],
      );
    }
    const programRequirements = [
      [DEMO_IDS.programId, "CS 101", "major_core", 1],
      [DEMO_IDS.programId, "MATH 151", "math_science", 1],
      [DEMO_IDS.programId, "WRIT 101", "general_education", 1],
      [DEMO_IDS.programId, "CS 201", "major_core", 2],
      [DEMO_IDS.programId, "CS 230", "major_core", 2],
      [DEMO_IDS.programId, "MATH 152", "math_science", 2],
      [DEMO_IDS.programId, "CS 310", "major_core", 3],
      [mechanicalProgramId, "ENGR 101", "major_core", 1],
      [mechanicalProgramId, "MATH 151", "math_science", 1],
      [mechanicalProgramId, "WRIT 101", "general_education", 1],
      [mechanicalProgramId, "MATH 152", "math_science", 2],
      [mechanicalProgramId, "PHYS 201", "math_science", 2],
      [mechanicalProgramId, "ME 210", "major_core", 3],
      [mechanicalProgramId, "ME 220", "major_core", 4],
      [mechanicalProgramId, "ME 330", "major_core", 4],
      [businessProgramId, "BUS 101", "major_core", 1],
      [businessProgramId, "WRIT 101", "general_education", 1],
      [businessProgramId, "ACCT 201", "major_core", 2],
      [businessProgramId, "ECON 201", "major_core", 2],
      [businessProgramId, "FIN 301", "major_core", 3],
      [businessProgramId, "MKTG 301", "major_core", 3],
    ] as const;
    let programRequirementIndex = 1;
    for (const [programId, courseCode, category, term] of programRequirements) {
      const id = `22000000-0000-7000-8000-${String(programRequirementIndex).padStart(12, "0")}`;
      programRequirementIndex += 1;
      await client.query(
        `INSERT INTO program_requirement (
           id, tenant_id, program_id, catalog_version_id, course_id,
           category, recommended_term, required
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, true)
         ON CONFLICT DO NOTHING`,
        [
          id,
          DEMO_IDS.tenantId,
          programId,
          catalogId,
          courseId(courseCode),
          category,
          term,
        ],
      );
    }
    await client.query(
      `INSERT INTO course_equivalency_rule (
         id, tenant_id, catalog_version_id, code, source_type, source_code,
         minimum_score, target_course_id, confidence, version
       )
       VALUES
         (
           '23000000-0000-7000-8000-000000000101', $1, $2,
           'AP-CALC-AB-4-MATH151', 'ap', 'AP Calculus AB', 4, $3, 1, 1
         ),
         (
           '23000000-0000-7000-8000-000000000102', $1, $2,
           'AP-CSA-4-CS101', 'ap', 'AP Computer Science A', 4, $4, 1, 1
         )
       ON CONFLICT DO NOTHING`,
      [
        DEMO_IDS.tenantId,
        catalogId,
        courseId("MATH 151"),
        courseId("CS 101"),
      ],
    );
    await client.query(
      `INSERT INTO admission_offer (
         id,
         tenant_id,
         student_id,
         program_id,
         academic_term_id,
         campus_id,
         response_deadline,
         deposit_amount_cents,
         status,
         version
       )
       VALUES ($1, $2, $3, $4, $5, $6, '2027-08-15', 50000, 'offered', 1)
       ON CONFLICT (id) DO NOTHING`,
      [
        DEMO_IDS.offerId,
        DEMO_IDS.tenantId,
        DEMO_IDS.studentId,
        DEMO_IDS.programId,
        DEMO_IDS.academicTermId,
        DEMO_IDS.campusId,
      ],
    );
    await client.query(
      `INSERT INTO journey_definition_version (
         id, tenant_id, code, version, active
       )
       VALUES ($1, $2, 'standard_undergraduate_enrollment', 1, 1)
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.journeyDefinitionVersionId, DEMO_IDS.tenantId],
    );

    const requirements = [
      {
        id: DEMO_IDS.profileRequirementDefinitionVersionId,
        code: "profile_verification",
        title: "Verify your profile",
        description:
          "Confirm your personal and contact information before continuing.",
        blocking: 1,
        displayOrder: 10,
        dependsOnCodes: [],
        dueOffsetDays: 7,
        submissionType: "form",
        responsibleOffice: "Enrollment Services",
      },
      {
        id: DEMO_IDS.identityRequirementDefinitionVersionId,
        code: "identity_document",
        title: "Provide identity documentation",
        description:
          "Upload an accepted identity document for institutional review.",
        blocking: 1,
        displayOrder: 20,
        dependsOnCodes: ["profile_verification"],
        dueOffsetDays: 14,
        submissionType: "document",
        responsibleOffice: "Registrar",
      },
      {
        id: "00000000-0000-7000-8000-000000000304",
        code: "official_transcript",
        title: "Submit your official transcript",
        description:
          "Upload your transcript for document review and potential course-credit matching.",
        blocking: 1,
        displayOrder: 30,
        dependsOnCodes: ["profile_verification"],
        dueOffsetDays: 14,
        submissionType: "document",
        responsibleOffice: "Registrar",
      },
      {
        id: "00000000-0000-7000-8000-000000000305",
        code: "financial_aid_verification",
        title: "Complete financial-aid verification",
        description:
          "Submit the requested verification worksheet and review your aid package.",
        blocking: 1,
        displayOrder: 40,
        dependsOnCodes: [],
        dueOffsetDays: 10,
        submissionType: "document",
        responsibleOffice: "Financial Aid",
      },
      {
        id: "00000000-0000-7000-8000-000000000306",
        code: "immunization_record",
        title: "Provide immunization records",
        description:
          "Upload the required health clearance documentation before arrival.",
        blocking: 1,
        displayOrder: 50,
        dependsOnCodes: ["profile_verification"],
        dueOffsetDays: 30,
        submissionType: "document",
        responsibleOffice: "Student Health",
      },
      {
        id: "00000000-0000-7000-8000-000000000307",
        code: "housing_preference",
        title: "Confirm housing plans",
        description:
          "Tell Aster whether you plan to live on campus, off campus, or are undecided.",
        blocking: 0,
        displayOrder: 60,
        dependsOnCodes: [],
        dueOffsetDays: 18,
        submissionType: "form",
        responsibleOffice: "Housing & Residence Life",
      },
      {
        id: DEMO_IDS.depositRequirementDefinitionVersionId,
        code: "enrollment_deposit",
        title: "Pay your enrollment deposit",
        description:
          "Complete the enrollment deposit through the approved payment flow.",
        blocking: 1,
        displayOrder: 70,
        dependsOnCodes: [],
        dueOffsetDays: 21,
        submissionType: "payment",
        responsibleOffice: "Student Accounts",
      },
      {
        id: "00000000-0000-7000-8000-000000000308",
        code: "orientation_registration",
        title: "Register for orientation",
        description:
          "Choose an orientation session after your deposit and core records are complete.",
        blocking: 1,
        displayOrder: 80,
        dependsOnCodes: ["enrollment_deposit", "identity_document"],
        dueOffsetDays: 35,
        submissionType: "form",
        responsibleOffice: "New Student Programs",
      },
    ];

    for (const requirement of requirements) {
      await client.query(
        `INSERT INTO requirement_definition_version (
           id,
           tenant_id,
           code,
           title,
           description,
           blocking,
           display_order,
           depends_on_codes,
           due_offset_days,
           submission_type,
           responsible_office,
           version
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 1)
         ON CONFLICT (id) DO NOTHING`,
        [
          requirement.id,
          DEMO_IDS.tenantId,
          requirement.code,
          requirement.title,
          requirement.description,
          requirement.blocking,
          requirement.displayOrder,
          requirement.dependsOnCodes,
          requirement.dueOffsetDays,
          requirement.submissionType,
          requirement.responsibleOffice,
        ],
      );
      await client.query(
        `INSERT INTO journey_requirement_definition (
           journey_definition_version_id,
           requirement_definition_version_id
         )
         VALUES ($1, $2)
         ON CONFLICT DO NOTHING`,
        [DEMO_IDS.journeyDefinitionVersionId, requirement.id],
      );
    }

    await client.query(
      `INSERT INTO student_portal_projection (
         tenant_id,
         student_id,
         projection_version,
         dashboard,
         source_updated_at
       )
       VALUES ($1, $2, 1, $3::jsonb, '2026-07-24T00:00:00.000Z')
       ON CONFLICT (tenant_id, student_id) DO NOTHING`,
      [
        DEMO_IDS.tenantId,
        DEMO_IDS.studentId,
        JSON.stringify(initialDashboard),
      ],
    );
    await client.query(
      `INSERT INTO student_onboarding (
         tenant_id, student_id, status, current_step, completed_steps, payload, version
       )
       VALUES ($1, $2, 'in_progress', 'offer', '{}', '{}'::jsonb, 1)
       ON CONFLICT (tenant_id, student_id) DO NOTHING`,
      [DEMO_IDS.tenantId, DEMO_IDS.studentId],
    );
    await client.query(
      `INSERT INTO student_profile (
         tenant_id,
         student_id,
         preferred_name,
         pronouns,
         mobile_phone,
         communication_preference,
         version
       )
       VALUES ($1, $2, 'Alex', NULL, NULL, 'email', 1)
       ON CONFLICT (tenant_id, student_id) DO NOTHING`,
      [DEMO_IDS.tenantId, DEMO_IDS.studentId],
    );
    await client.query(
      `INSERT INTO student_message (
         id, tenant_id, student_id, subject, body, sender_name, sent_at
       )
       VALUES
         (
           $1, $3, $4, 'Welcome to your enrollment portal',
           'Your portal keeps every enrollment action in one place.',
           'Enrollment Services', '2026-07-24T09:00:00.000Z'
         ),
         (
           $2, $3, $4, 'Your next enrollment step',
           'Review your admission offer and continue when you are ready.',
           'Admissions Office', '2026-07-24T10:00:00.000Z'
         )
       ON CONFLICT (id) DO NOTHING`,
      [
        DEMO_IDS.welcomeMessageId,
        DEMO_IDS.reminderMessageId,
        DEMO_IDS.tenantId,
        DEMO_IDS.studentId,
      ],
    );
    await client.query(
      `INSERT INTO document_record (
         id,
         tenant_id,
         student_id,
         file_name,
         mime_type,
         size_bytes,
         category,
         status,
         storage_provider
       )
       VALUES (
         $1, $2, $3, 'identity-document-placeholder.pdf',
         'application/pdf', 2048, 'identity', 'placeholder',
         'local_placeholder'
       )
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.sampleDocumentId, DEMO_IDS.tenantId, DEMO_IDS.studentId],
    );
    await client.query(
      `INSERT INTO student_appointment (
         id, tenant_id, student_id, type, starts_at, notes, status
       )
       VALUES (
         $1, $2, $3, 'enrollment_support',
         '2027-08-05T14:00:00.000Z',
         'Welcome and enrollment planning', 'scheduled'
       )
       ON CONFLICT (id) DO NOTHING`,
      [
        DEMO_IDS.sampleAppointmentId,
        DEMO_IDS.tenantId,
        DEMO_IDS.studentId,
      ],
    );
    const helpArticles = [
      {
        id: DEMO_IDS.helpGettingStartedId,
        category: "getting_started",
        question: "Where should I begin?",
        answer:
          "Start with the next action shown on your dashboard and complete onboarding when prompted.",
        sortOrder: 10,
      },
      {
        id: DEMO_IDS.helpDocumentsId,
        category: "documents",
        question: "Which document formats are accepted?",
        answer:
          "The local portal accepts PDF, JPEG, and PNG metadata up to 10 MB per document.",
        sortOrder: 20,
      },
      {
        id: DEMO_IDS.helpPaymentsId,
        category: "payments",
        question: "How does the demo deposit work?",
        answer:
          "The development payment adapter records a deterministic successful deposit without charging a real payment method.",
        sortOrder: 30,
      },
    ];
    for (const article of helpArticles) {
      await client.query(
        `INSERT INTO help_article (
           id, tenant_id, category, question, answer, sort_order, active
         )
         VALUES ($1, $2, $3, $4, $5, $6, true)
         ON CONFLICT (id) DO NOTHING`,
        [
          article.id,
          DEMO_IDS.tenantId,
          article.category,
          article.question,
          article.answer,
          article.sortOrder,
        ],
      );
    }
    await client.query(
      `INSERT INTO student_transcript_credit (
         id, tenant_id, student_id, source_type, source_code, title,
         grade_or_score, institution_name, reviewed_at
       )
       VALUES
         (
           '30000000-0000-7000-8000-000000000101', $1, $2, 'ap',
           'AP Calculus AB', 'AP Calculus AB', '5', 'College Board', now()
         ),
         (
           '30000000-0000-7000-8000-000000000102', $1, $2, 'ap',
           'AP Computer Science A', 'AP Computer Science A', '4',
           'College Board', now()
         )
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.tenantId, DEMO_IDS.studentId],
    );
    await client.query(
      `INSERT INTO course_exemption_recommendation (
         id, tenant_id, student_id, program_id, catalog_version_id,
         transcript_credit_id, target_course_id, equivalency_rule_id,
         status, confidence, rationale
       )
       VALUES
         (
           '31000000-0000-7000-8000-000000000101', $1, $2, $3, $4,
           '30000000-0000-7000-8000-000000000101', $5,
           '23000000-0000-7000-8000-000000000101', 'suggested', 1,
           'AP Calculus AB score 5 meets the stored minimum score of 4.'
         ),
         (
           '31000000-0000-7000-8000-000000000102', $1, $2, $3, $4,
           '30000000-0000-7000-8000-000000000102', $6,
           '23000000-0000-7000-8000-000000000102', 'suggested', 1,
           'AP Computer Science A score 4 meets the stored minimum score of 4.'
         )
       ON CONFLICT DO NOTHING`,
      [
        DEMO_IDS.tenantId,
        DEMO_IDS.studentId,
        DEMO_IDS.programId,
        catalogId,
        courseId("MATH 151"),
        courseId("CS 101"),
      ],
    );
    await client.query(
      `INSERT INTO student_financial_summary (
         tenant_id, student_id, academic_year, cost_of_attendance_cents
       )
       VALUES ($1, $2, '2027-2028', 3240000)
       ON CONFLICT DO NOTHING`,
      [DEMO_IDS.tenantId, DEMO_IDS.studentId],
    );
    const awards = [
      ["40000000-0000-7000-8000-000000000101", "federal", "Federal Pell Grant", "grant", 739500, 739500, "accepted", false],
      ["40000000-0000-7000-8000-000000000102", "institutional", "Aster Achievement Scholarship", "scholarship", 800000, 800000, "accepted", false],
      ["40000000-0000-7000-8000-000000000103", "federal", "Direct Subsidized Loan", "loan", 350000, 0, "offered", true],
      ["40000000-0000-7000-8000-000000000104", "federal", "Federal Work-Study", "work_study", 250000, 0, "pending", false],
    ] as const;
    for (const award of awards) {
      await client.query(
        `INSERT INTO student_financial_award (
           id, tenant_id, student_id, academic_year, source, name, type,
           offered_amount_cents, accepted_amount_cents, status, requires_action
         )
         VALUES ($1, $2, $3, '2027-2028', $4, $5, $6, $7, $8, $9, $10)
         ON CONFLICT (id) DO NOTHING`,
        [
          award[0],
          DEMO_IDS.tenantId,
          DEMO_IDS.studentId,
          award[1],
          award[2],
          award[3],
          award[4],
          award[5],
          award[6],
          award[7],
        ],
      );
    }
    const financialDocuments = [
      ["41000000-0000-7000-8000-000000000101", "fafsa", "FAFSA", "Federal application received and matched to Aster.", "verified", null],
      ["41000000-0000-7000-8000-000000000102", "verification_worksheet", "Verification worksheet", "Upload the signed worksheet requested by Financial Aid.", "action_required", "2027-08-03T23:59:59.000Z"],
      ["41000000-0000-7000-8000-000000000103", "award_acceptance", "Award acceptance", "Review which loans or work-study awards you want to accept.", "not_started", "2027-08-10T23:59:59.000Z"],
    ] as const;
    for (const document of financialDocuments) {
      await client.query(
        `INSERT INTO financial_document_requirement (
           id, tenant_id, student_id, code, title, description, status, due_at
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
         ON CONFLICT (id) DO NOTHING`,
        [
          document[0],
          DEMO_IDS.tenantId,
          DEMO_IDS.studentId,
          document[1],
          document[2],
          document[3],
          document[4],
          document[5],
        ],
      );
    }
    const paymentPlans = [
      ["42000000-0000-7000-8000-000000000101", "5-month semester plan", 5, 5000],
      ["42000000-0000-7000-8000-000000000102", "Monthly academic-year plan", 10, 7500],
    ] as const;
    for (const plan of paymentPlans) {
      await client.query(
        `INSERT INTO student_payment_plan (
           id, tenant_id, student_id, academic_year, name,
           installment_count, enrollment_fee_cents, status
         )
         VALUES ($1, $2, $3, '2027-2028', $4, $5, $6, 'available')
         ON CONFLICT (id) DO NOTHING`,
        [
          plan[0],
          DEMO_IDS.tenantId,
          DEMO_IDS.studentId,
          plan[1],
          plan[2],
          plan[3],
        ],
      );
    }
    await client.query(
      `INSERT INTO student_sap_status (
         tenant_id, student_id, academic_year, status, cumulative_gpa,
         minimum_gpa, completion_rate_percent,
         minimum_completion_rate_percent, attempted_credits,
         maximum_attempted_credits
       )
       VALUES ($1, $2, '2027-2028', 'meeting', 3.42, 2.0, 78, 67, 28, 180)
       ON CONFLICT DO NOTHING`,
      [DEMO_IDS.tenantId, DEMO_IDS.studentId],
    );
    const campusEvents = [
      ["50000000-0000-7000-8000-000000000101", "Welcome Week Block Party", "Food, music, student organizations, and a relaxed first look at campus life.", "2027-08-28T18:00:00.000Z", "2027-08-28T21:00:00.000Z", "University Green", "social", "gold"],
      ["50000000-0000-7000-8000-000000000102", "First-Year Research Showcase", "Meet faculty mentors and discover research opportunities open to first-year students.", "2027-09-02T16:00:00.000Z", "2027-09-02T18:00:00.000Z", "Innovation Hall", "academic", "blue"],
      ["50000000-0000-7000-8000-000000000103", "Internship Ready Lab", "Bring your résumé for a quick review and practice a two-minute introduction.", "2027-09-08T15:30:00.000Z", "2027-09-08T17:00:00.000Z", "Career Commons", "career", "navy"],
    ] as const;
    for (const event of campusEvents) {
      await client.query(
        `INSERT INTO campus_event (
           id, tenant_id, title, description, starts_at, ends_at,
           location, category, featured, accent
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, true, $9)
         ON CONFLICT (id) DO NOTHING`,
        [
          event[0],
          DEMO_IDS.tenantId,
          event[1],
          event[2],
          event[3],
          event[4],
          event[5],
          event[6],
          event[7],
        ],
      );
    }
    const clubs = [
      ["51000000-0000-7000-8000-000000000101", "Aster Robotics", "Engineering & Technology", "Design, build, and compete with autonomous robots in multidisciplinary teams.", "Maya Chen", "Club President", "robotics@aster.edu", "New-member build teams open this week.", "Open Lab · Sep 4, 6:00 PM"],
      ["51000000-0000-7000-8000-000000000102", "Code Collective", "Computing", "Peer learning, hack nights, open-source projects, and conversations with alumni.", "Noah Williams", "Community Lead", "codecollective@aster.edu", "Fall project pitches are now posted.", "Hack Night · Sep 6, 7:00 PM"],
      ["51000000-0000-7000-8000-000000000103", "Women in Business", "Professional", "Mentoring, leadership workshops, community projects, and employer networking.", "Jordan Ellis", "Membership Chair", "wib@aster.edu", "Peer mentor matching closes Friday.", "Coffee & Careers · Sep 7, 4:30 PM"],
      ["51000000-0000-7000-8000-000000000104", "Outdoor Aster", "Recreation & Wellness", "Low-cost hikes, climbing sessions, service trips, and outdoor skills workshops.", "Eli Torres", "Trip Coordinator", "outdoors@aster.edu", "Beginner hike registration is open.", "Trail Basics · Sep 9, 9:00 AM"],
    ] as const;
    for (const club of clubs) {
      await client.query(
        `INSERT INTO student_club (
           id, tenant_id, name, category, description, contact_name,
           contact_role, contact_channel, latest_update, next_activity
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
         ON CONFLICT (id) DO NOTHING`,
        [
          club[0],
          DEMO_IDS.tenantId,
          club[1],
          club[2],
          club[3],
          club[4],
          club[5],
          club[6],
          club[7],
          club[8],
        ],
      );
    }
    await client.query("COMMIT");
    process.stdout.write("Demo seed is ready\n");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
    await pool.end();
  }
}

void main();
