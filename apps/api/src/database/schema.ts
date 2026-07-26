import {
  bigint,
  boolean,
  date,
  index,
  integer,
  jsonb,
  pgTable,
  primaryKey,
  smallint,
  text,
  timestamp,
  uniqueIndex,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";

const timestamps = {
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
};

export const tenant = pgTable("tenant", {
  id: uuid("id").primaryKey(),
  name: varchar("name", { length: 180 }).notNull(),
  ...timestamps,
});

export const person = pgTable(
  "person",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    preferredName: varchar("preferred_name", { length: 120 }),
    firstName: varchar("first_name", { length: 120 }).notNull(),
    lastName: varchar("last_name", { length: 120 }).notNull(),
    ...timestamps,
  },
  (table) => [index("person_tenant_idx").on(table.tenantId)],
);

export const student = pgTable(
  "student",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    personId: uuid("person_id")
      .notNull()
      .references(() => person.id),
    classYear: smallint("class_year").notNull(),
    ...timestamps,
  },
  (table) => [
    uniqueIndex("student_tenant_person_uidx").on(
      table.tenantId,
      table.personId,
    ),
  ],
);

export const studentIdentityInvitation = pgTable(
  "student_identity_invitation",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    studentId: uuid("student_id")
      .notNull()
      .references(() => student.id),
    emailNormalized: varchar("email_normalized", { length: 254 }).notNull(),
    phoneE164: varchar("phone_e164", { length: 16 }).notNull(),
    tokenHash: varchar("token_hash", { length: 64 }).notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    acceptedAt: timestamp("accepted_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    uniqueIndex("student_identity_invitation_token_uidx").on(table.tokenHash),
    index("student_identity_invitation_lookup_idx").on(
      table.tenantId,
      table.emailNormalized,
      table.expiresAt,
    ),
  ],
);

export const credentialAccount = pgTable(
  "credential_account",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    studentId: uuid("student_id")
      .notNull()
      .references(() => student.id),
    emailNormalized: varchar("email_normalized", { length: 254 }).notNull(),
    phoneE164: varchar("phone_e164", { length: 16 }).notNull(),
    passwordHash: text("password_hash").notNull(),
    passwordAlgorithm: varchar("password_algorithm", { length: 32 }).notNull(),
    emailVerifiedAt: timestamp("email_verified_at", { withTimezone: true }),
    phoneVerifiedAt: timestamp("phone_verified_at", { withTimezone: true }),
    status: varchar("status", { length: 24 }).notNull(),
    failedSignInCount: smallint("failed_sign_in_count").notNull().default(0),
    lockedUntil: timestamp("locked_until", { withTimezone: true }),
    passwordChangedAt: timestamp("password_changed_at", {
      withTimezone: true,
    })
      .notNull()
      .defaultNow(),
    lastSignedInAt: timestamp("last_signed_in_at", { withTimezone: true }),
    ...timestamps,
  },
  (table) => [
    uniqueIndex("credential_account_student_uidx").on(
      table.tenantId,
      table.studentId,
    ),
    uniqueIndex("credential_account_email_uidx").on(
      table.tenantId,
      table.emailNormalized,
    ),
    uniqueIndex("credential_account_phone_uidx").on(
      table.tenantId,
      table.phoneE164,
    ),
  ],
);

export const authSession = pgTable(
  "auth_session",
  {
    id: uuid("id").primaryKey(),
    accountId: uuid("account_id")
      .notNull()
      .references(() => credentialAccount.id, { onDelete: "cascade" }),
    tokenHash: varchar("token_hash", { length: 64 }).notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    revokedAt: timestamp("revoked_at", { withTimezone: true }),
    lastSeenAt: timestamp("last_seen_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    uniqueIndex("auth_session_token_uidx").on(table.tokenHash),
    index("auth_session_account_idx").on(table.accountId, table.expiresAt),
  ],
);

export const authVerificationChallenge = pgTable(
  "auth_verification_challenge",
  {
    id: uuid("id").primaryKey(),
    accountId: uuid("account_id")
      .notNull()
      .references(() => credentialAccount.id, { onDelete: "cascade" }),
    channel: varchar("channel", { length: 16 }).notNull(),
    destinationNormalized: varchar("destination_normalized", {
      length: 254,
    }).notNull(),
    tokenHash: varchar("token_hash", { length: 64 }).notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    consumedAt: timestamp("consumed_at", { withTimezone: true }),
    failedAttemptCount: smallint("failed_attempt_count").notNull().default(0),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    uniqueIndex("auth_verification_challenge_token_uidx").on(table.tokenHash),
    index("auth_verification_challenge_account_idx").on(
      table.accountId,
      table.channel,
      table.createdAt,
    ),
  ],
);

export const campus = pgTable("campus", {
  id: uuid("id").primaryKey(),
  tenantId: uuid("tenant_id")
    .notNull()
    .references(() => tenant.id),
  name: varchar("name", { length: 180 }).notNull(),
  ...timestamps,
});

export const academicTerm = pgTable("academic_term", {
  id: uuid("id").primaryKey(),
  tenantId: uuid("tenant_id")
    .notNull()
    .references(() => tenant.id),
  name: varchar("name", { length: 180 }).notNull(),
  startsOn: date("starts_on").notNull(),
  ...timestamps,
});

export const program = pgTable("program", {
  id: uuid("id").primaryKey(),
  tenantId: uuid("tenant_id")
    .notNull()
    .references(() => tenant.id),
  name: varchar("name", { length: 180 }).notNull(),
  ...timestamps,
});

export const admissionOffer = pgTable(
  "admission_offer",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    studentId: uuid("student_id")
      .notNull()
      .references(() => student.id),
    programId: uuid("program_id")
      .notNull()
      .references(() => program.id),
    academicTermId: uuid("academic_term_id")
      .notNull()
      .references(() => academicTerm.id),
    campusId: uuid("campus_id")
      .notNull()
      .references(() => campus.id),
    responseDeadline: date("response_deadline").notNull(),
    depositAmountCents: integer("deposit_amount_cents").notNull(),
    status: varchar("status", { length: 24 }).notNull(),
    acceptedAt: timestamp("accepted_at", { withTimezone: true }),
    version: integer("version").notNull().default(1),
    ...timestamps,
  },
  (table) => [
    index("admission_offer_student_idx").on(
      table.tenantId,
      table.studentId,
      table.createdAt,
    ),
  ],
);

export const journeyDefinitionVersion = pgTable(
  "journey_definition_version",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    code: varchar("code", { length: 100 }).notNull(),
    version: integer("version").notNull(),
    active: integer("active").notNull().default(1),
    ...timestamps,
  },
  (table) => [
    uniqueIndex("journey_definition_version_uidx").on(
      table.tenantId,
      table.code,
      table.version,
    ),
  ],
);

export const requirementDefinitionVersion = pgTable(
  "requirement_definition_version",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    code: varchar("code", { length: 100 }).notNull(),
    title: varchar("title", { length: 180 }).notNull(),
    description: text("description").notNull(),
    blocking: integer("blocking").notNull().default(1),
    displayOrder: integer("display_order").notNull(),
    dependsOnCodes: text("depends_on_codes").array().notNull(),
    dueOffsetDays: integer("due_offset_days"),
    submissionType: varchar("submission_type", { length: 32 })
      .notNull()
      .default("none"),
    responsibleOffice: varchar("responsible_office", { length: 180 })
      .notNull()
      .default("Enrollment Services"),
    version: integer("version").notNull(),
    ...timestamps,
  },
  (table) => [
    uniqueIndex("requirement_definition_version_uidx").on(
      table.tenantId,
      table.code,
      table.version,
    ),
  ],
);

export const journeyRequirementDefinition = pgTable(
  "journey_requirement_definition",
  {
    journeyDefinitionVersionId: uuid("journey_definition_version_id")
      .notNull()
      .references(() => journeyDefinitionVersion.id),
    requirementDefinitionVersionId: uuid("requirement_definition_version_id")
      .notNull()
      .references(() => requirementDefinitionVersion.id),
  },
  (table) => [
    primaryKey({
      columns: [
        table.journeyDefinitionVersionId,
        table.requirementDefinitionVersionId,
      ],
    }),
  ],
);

export const enrollmentJourney = pgTable(
  "enrollment_journey",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    studentId: uuid("student_id")
      .notNull()
      .references(() => student.id),
    offerId: uuid("offer_id")
      .notNull()
      .references(() => admissionOffer.id),
    journeyDefinitionVersionId: uuid("journey_definition_version_id")
      .notNull()
      .references(() => journeyDefinitionVersion.id),
    status: varchar("status", { length: 32 }).notNull(),
    version: integer("version").notNull().default(1),
    ...timestamps,
  },
  (table) => [
    uniqueIndex("enrollment_journey_offer_uidx").on(
      table.tenantId,
      table.offerId,
    ),
  ],
);

export const studentRequirement = pgTable(
  "student_requirement",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id")
      .notNull()
      .references(() => tenant.id),
    journeyId: uuid("journey_id")
      .notNull()
      .references(() => enrollmentJourney.id),
    requirementDefinitionVersionId: uuid(
      "requirement_definition_version_id",
    )
      .notNull()
      .references(() => requirementDefinitionVersion.id),
    status: varchar("status", { length: 32 }).notNull(),
    dueAt: timestamp("due_at", { withTimezone: true }),
    progressPercent: smallint("progress_percent").notNull().default(0),
    version: integer("version").notNull().default(1),
    ...timestamps,
  },
  (table) => [
    uniqueIndex("student_requirement_definition_uidx").on(
      table.tenantId,
      table.journeyId,
      table.requirementDefinitionVersionId,
    ),
  ],
);

export const auditEvent = pgTable(
  "audit_event",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    actorType: varchar("actor_type", { length: 40 }).notNull(),
    actorId: uuid("actor_id").notNull(),
    studentId: uuid("student_id"),
    action: varchar("action", { length: 120 }).notNull(),
    resourceType: varchar("resource_type", { length: 80 }).notNull(),
    resourceId: uuid("resource_id").notNull(),
    authorizationBasis: varchar("authorization_basis", {
      length: 120,
    }).notNull(),
    requestId: varchar("request_id", { length: 128 }).notNull(),
    correlationId: varchar("correlation_id", { length: 128 }).notNull(),
    metadata: jsonb("metadata").$type<Record<string, unknown>>().notNull(),
    occurredAt: timestamp("occurred_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("audit_event_tenant_resource_idx").on(
      table.tenantId,
      table.resourceType,
      table.resourceId,
      table.occurredAt,
    ),
  ],
);

export interface DomainEventEnvelope {
  eventId: string;
  eventName: string;
  occurredAt: string;
  tenantId: string;
  aggregateType: string;
  aggregateId: string;
  aggregateVersion: number;
  actor: { type: string; id: string };
  correlationId: string;
  causationId: string;
  lineage?: {
    correlationId: string;
    effectRegistryVersion: 1;
    effectId?: string;
    traceId?: string;
    spanId?: string;
  };
  data: Record<string, unknown>;
}

export const outboxEvent = pgTable(
  "outbox_event",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    eventName: varchar("event_name", { length: 120 }).notNull(),
    aggregateType: varchar("aggregate_type", { length: 80 }).notNull(),
    aggregateId: uuid("aggregate_id").notNull(),
    aggregateVersion: integer("aggregate_version").notNull(),
    occurredAt: timestamp("occurred_at", { withTimezone: true }).notNull(),
    actorType: varchar("actor_type", { length: 40 }).notNull(),
    actorId: uuid("actor_id").notNull(),
    correlationId: varchar("correlation_id", { length: 128 }).notNull(),
    causationId: varchar("causation_id", { length: 128 }).notNull(),
    payload: jsonb("payload").$type<DomainEventEnvelope>().notNull(),
    publishedAt: timestamp("published_at", { withTimezone: true }),
    attempts: integer("attempts").notNull().default(0),
    nextAttemptAt: timestamp("next_attempt_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    lockedAt: timestamp("locked_at", { withTimezone: true }),
    lockedBy: varchar("locked_by", { length: 128 }),
    lastError: text("last_error"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("outbox_event_pending_idx").on(
      table.publishedAt,
      table.nextAttemptAt,
      table.createdAt,
    ),
  ],
);

export const activityEvent = pgTable(
  "activity_event",
  {
    tenantId: uuid("tenant_id").notNull(),
    eventId: uuid("event_id").notNull(),
    eventName: varchar("event_name", { length: 120 }).notNull(),
    occurredAt: timestamp("occurred_at", { withTimezone: true }).notNull(),
    receivedAt: timestamp("received_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    actorType: varchar("actor_type", { length: 40 }).notNull(),
    actorId: uuid("actor_id").notNull(),
    studentId: uuid("student_id"),
    sessionId: varchar("session_id", { length: 128 }).notNull(),
    pageInstanceId: varchar("page_instance_id", { length: 128 }).notNull(),
    correlationId: varchar("correlation_id", { length: 128 }),
    trustLevel: varchar("trust_level", { length: 40 })
      .notNull()
      .default("client_signal"),
    applicationVersion: varchar("application_version", {
      length: 80,
    }).notNull(),
    properties: jsonb("properties")
      .$type<Record<string, string | number | boolean | null>>()
      .notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.tenantId, table.eventId] }),
    index("activity_event_student_occurred_idx").on(
      table.tenantId,
      table.studentId,
      table.occurredAt,
    ),
  ],
);

export const idempotencyRecord = pgTable(
  "idempotency_record",
  {
    tenantId: uuid("tenant_id").notNull(),
    actorId: uuid("actor_id").notNull(),
    operation: varchar("operation", { length: 120 }).notNull(),
    idempotencyKey: varchar("idempotency_key", { length: 128 }).notNull(),
    requestHash: varchar("request_hash", { length: 64 }).notNull(),
    responseStatus: integer("response_status").notNull(),
    responseBody: jsonb("response_body")
      .$type<Record<string, unknown>>()
      .notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  },
  (table) => [
    primaryKey({
      columns: [
        table.tenantId,
        table.actorId,
        table.operation,
        table.idempotencyKey,
      ],
    }),
  ],
);

export const studentPortalProjection = pgTable(
  "student_portal_projection",
  {
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    projectionVersion: bigint("projection_version", {
      mode: "number",
    }).notNull(),
    dashboard: jsonb("dashboard").$type<Record<string, unknown>>().notNull(),
    sourceUpdatedAt: timestamp("source_updated_at", {
      withTimezone: true,
    }).notNull(),
    projectedAt: timestamp("projected_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    primaryKey({ columns: [table.tenantId, table.studentId] }),
  ],
);

export const projectionEventReceipt = pgTable(
  "projection_event_receipt",
  {
    eventId: uuid("event_id").notNull(),
    consumerName: varchar("consumer_name", { length: 128 }).notNull(),
    processedAt: timestamp("processed_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    primaryKey({ columns: [table.eventId, table.consumerName] }),
  ],
);

export const studentOnboarding = pgTable(
  "student_onboarding",
  {
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    status: varchar("status", { length: 24 }).notNull(),
    currentStep: varchar("current_step", { length: 32 }).notNull(),
    completedSteps: text("completed_steps").array().notNull(),
    payload: jsonb("payload").$type<Record<string, unknown>>().notNull(),
    version: integer("version").notNull().default(1),
    completedAt: timestamp("completed_at", { withTimezone: true }),
    ...timestamps,
  },
  (table) => [
    primaryKey({ columns: [table.tenantId, table.studentId] }),
  ],
);

export const studentMessage = pgTable(
  "student_message",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    subject: varchar("subject", { length: 240 }).notNull(),
    body: text("body").notNull(),
    senderName: varchar("sender_name", { length: 180 }).notNull(),
    sentAt: timestamp("sent_at", { withTimezone: true }).notNull(),
    readAt: timestamp("read_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("student_message_inbox_idx").on(
      table.tenantId,
      table.studentId,
      table.sentAt,
    ),
  ],
);

export const documentRecord = pgTable(
  "document_record",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    requirementId: uuid("requirement_id").references(
      () => studentRequirement.id,
    ),
    fileName: varchar("file_name", { length: 255 }).notNull(),
    mimeType: varchar("mime_type", { length: 80 }).notNull(),
    sizeBytes: integer("size_bytes").notNull(),
    category: varchar("category", { length: 40 }).notNull(),
    processingMode: varchar("processing_mode", { length: 24 })
      .notNull()
      .default("agentic"),
    status: varchar("status", { length: 40 }).notNull(),
    storageProvider: varchar("storage_provider", { length: 40 }).notNull(),
    storageKey: varchar("storage_key", { length: 512 }),
    sha256: varchar("sha256", { length: 64 }),
    extraction: jsonb("extraction").$type<Record<string, unknown>>(),
    ...timestamps,
  },
  (table) => [
    index("document_record_student_idx").on(
      table.tenantId,
      table.studentId,
      table.createdAt,
    ),
  ],
);

export const aiProviderResponseAttempt = pgTable(
  "ai_provider_response_attempt",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    documentId: uuid("document_id")
      .notNull()
      .references(() => documentRecord.id, { onDelete: "cascade" }),
    requestId: varchar("request_id", { length: 128 }).notNull(),
    attemptNumber: smallint("attempt_number").notNull(),
    operation: varchar("operation", { length: 80 }).notNull(),
    provider: varchar("provider", { length: 40 }).notNull(),
    requestedModel: varchar("requested_model", { length: 200 }),
    responseModel: varchar("response_model", { length: 200 }),
    providerRequestId: varchar("provider_request_id", { length: 200 }),
    httpStatus: integer("http_status"),
    responseOk: boolean("response_ok").notNull(),
    finishReason: varchar("finish_reason", { length: 80 }),
    usage: jsonb("usage").$type<Record<string, unknown>>(),
    rawResponseText: text("raw_response_text"),
    responseBody: jsonb("response_body"),
    transportError: jsonb("transport_error").$type<{
      name: string;
      message: string;
    }>(),
    durationMs: integer("duration_ms").notNull(),
    recordedAt: timestamp("recorded_at", { withTimezone: true }).notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("ai_provider_response_attempt_document_idx").on(
      table.tenantId,
      table.studentId,
      table.documentId,
      table.recordedAt,
    ),
    uniqueIndex("ai_provider_response_attempt_delivery_uidx").on(
      table.tenantId,
      table.documentId,
      table.requestId,
      table.attemptNumber,
    ),
  ],
);

export const studentAppointment = pgTable(
  "student_appointment",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    type: varchar("type", { length: 60 }).notNull(),
    startsAt: timestamp("starts_at", { withTimezone: true }).notNull(),
    notes: text("notes"),
    status: varchar("status", { length: 32 }).notNull(),
    ...timestamps,
  },
  (table) => [
    index("student_appointment_student_idx").on(
      table.tenantId,
      table.studentId,
      table.startsAt,
    ),
  ],
);

export const paymentTransaction = pgTable(
  "payment_transaction",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    offerId: uuid("offer_id").notNull(),
    type: varchar("type", { length: 40 }).notNull(),
    amountCents: integer("amount_cents").notNull(),
    status: varchar("status", { length: 32 }).notNull(),
    processor: varchar("processor", { length: 40 }).notNull(),
    processorReference: varchar("processor_reference", {
      length: 128,
    }).notNull(),
    ...timestamps,
  },
  (table) => [
    index("payment_transaction_student_idx").on(
      table.tenantId,
      table.studentId,
      table.createdAt,
    ),
  ],
);

export const studentProfile = pgTable(
  "student_profile",
  {
    tenantId: uuid("tenant_id").notNull(),
    studentId: uuid("student_id").notNull(),
    preferredName: varchar("preferred_name", { length: 120 }).notNull(),
    pronouns: varchar("pronouns", { length: 80 }),
    mobilePhone: varchar("mobile_phone", { length: 32 }),
    communicationPreference: varchar("communication_preference", {
      length: 16,
    }).notNull(),
    version: integer("version").notNull().default(1),
    ...timestamps,
  },
  (table) => [
    primaryKey({ columns: [table.tenantId, table.studentId] }),
  ],
);

export const helpArticle = pgTable(
  "help_article",
  {
    id: uuid("id").primaryKey(),
    tenantId: uuid("tenant_id").notNull(),
    category: varchar("category", { length: 40 }).notNull(),
    question: varchar("question", { length: 240 }).notNull(),
    answer: text("answer").notNull(),
    sortOrder: integer("sort_order").notNull(),
    active: boolean("active").notNull().default(true),
    ...timestamps,
  },
  (table) => [
    index("help_article_tenant_idx").on(
      table.tenantId,
      table.sortOrder,
    ),
  ],
);
