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
      `INSERT INTO program (id, tenant_id, name)
       VALUES ($1, $2, 'Computer Science')
       ON CONFLICT (id) DO NOTHING`,
      [DEMO_IDS.programId, DEMO_IDS.tenantId],
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
        id: DEMO_IDS.depositRequirementDefinitionVersionId,
        code: "enrollment_deposit",
        title: "Pay your enrollment deposit",
        description:
          "Complete the enrollment deposit through the approved payment flow.",
        blocking: 1,
        displayOrder: 30,
        dependsOnCodes: [],
        dueOffsetDays: 21,
        submissionType: "payment",
        responsibleOffice: "Student Accounts",
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
