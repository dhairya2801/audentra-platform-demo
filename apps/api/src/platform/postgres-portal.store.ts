import { createHash, randomUUID } from "node:crypto";
import {
  documentProcessingModeForCategory,
  documentCategoryForRequirement,
  studentRequirementCodeFromSlug,
  studentRequirementSlug,
  type AcademicProgram,
  type CampusLifeFeed,
  type CatalogCourse,
  type CompleteStudentOnboardingInput,
  type ConfirmStudentDocumentExtractionInput,
  type CreateDepositPaymentInput,
  type CreateStudentAppointmentInput,
  type CreateStudentDocumentInput,
  type OnboardingStep,
  type StudentAppointment,
  type StudentAcademics,
  type StudentAppointmentList,
  type StudentBootstrap,
  type StudentDocument,
  type StudentDocumentExtraction,
  type StudentDocumentList,
  type StudentHelp,
  type StudentHousingPlan,
  type StudentFinancials,
  type StudentMessage,
  type StudentMessageList,
  type StudentOnboarding,
  type StudentOnboardingData,
  type StudentPayment,
  type StudentPaymentList,
  type StudentProfile,
  type StudentRequirementDetail,
  type StudentRequirementList,
  type StudentRewardSummary,
  type UpdateStudentOnboardingInput,
  type UpdateStudentHousingPlanInput,
  type UpdateStudentProfileInput,
} from "@vv/contracts";
import { sql } from "drizzle-orm";
import type { AuthContext } from "../auth/auth-context";
import {
  ApiError,
  BadRequestError,
  ConflictError,
  NotFoundError,
} from "../common/api-error";
import { DatabaseService } from "../database/database.service";
import {
  ONBOARDING_STEPS,
  isSkippableOnboardingStep,
  validateOnboardingStepData,
} from "../portal/onboarding-policy";
import { getRuntimeLineage } from "../observability/runtime-lineage";
import type {
  CourseExemptionContext,
  ImmunizationPolicyContext,
} from "./platform-store";

type Transaction = Parameters<
  Parameters<DatabaseService["db"]["transaction"]>[0]
>[0];
type RowResult<T> = { rows: T[] };

function rows<T>(result: unknown): T[] {
  return (result as RowResult<T>).rows;
}

function isoTimestamp(value: Date | string): string {
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("Database returned an invalid timestamp");
  }
  return parsed.toISOString();
}

function nullableIsoTimestamp(
  value: Date | string | null,
): string | null {
  return value === null ? null : isoTimestamp(value);
}

interface OnboardingRow {
  student_id: string;
  status: StudentOnboarding["status"];
  current_step: OnboardingStep;
  completed_steps: OnboardingStep[];
  payload: StudentOnboardingData;
  version: number;
  completed_at: Date | null;
  updated_at: Date;
}

interface RequirementRow {
  id: string;
  journey_id: string;
  code: string;
  title: string;
  description: string;
  status: StudentRequirementDetail["status"];
  blocking: number;
  due_at: Date | null;
  progress_percent: number;
  submission_type: StudentRequirementDetail["submissionType"];
  responsible_office: string;
  depends_on_codes: string[];
  reward_points?: string | number;
  reward_earned?: boolean;
}

interface MessageRow {
  id: string;
  subject: string;
  body: string;
  sender_name: string;
  sent_at: Date;
  read_at: Date | null;
}

interface DocumentRow {
  id: string;
  requirement_id: string | null;
  file_name: string;
  mime_type: StudentDocument["mimeType"];
  size_bytes: number;
  category: StudentDocument["category"];
  processing_mode: NonNullable<StudentDocument["processingMode"]>;
  status: StudentDocument["status"];
  storage_key: string | null;
  sha256: string | null;
  extraction: StudentDocumentExtraction | null;
  created_at: Date;
}

interface AppointmentRow {
  id: string;
  type: StudentAppointment["type"];
  starts_at: Date;
  notes: string | null;
  status: StudentAppointment["status"];
  created_at: Date;
}

interface PaymentRow {
  id: string;
  offer_id: string;
  amount_cents: number;
  status: StudentPayment["status"];
  processor_reference: string;
  created_at: Date;
}

interface ProfileRow {
  student_id: string;
  preferred_name: string;
  first_name?: string;
  last_name?: string;
  email?: string | null;
  email_verified?: boolean | null;
  phone_verified?: boolean | null;
  pronouns: string | null;
  mobile_phone: string | null;
  communication_preference: StudentProfile["communicationPreference"];
  version: number;
  updated_at: Date;
}

interface IdempotencyRow<T> {
  request_hash: string;
  response_body: T;
}

const requirementCodeByDocumentCategory: Partial<
  Record<StudentDocument["category"], string>
> = {
  identity: "identity_document",
  transcript: "official_transcript",
  financial_aid: "financial_aid_verification",
  health: "immunization_record",
};

type SafeProfileProjection = Partial<
  Pick<
    StudentProfile,
    "preferredName" | "pronouns" | "mobilePhone" | "communicationPreference"
  >
>;

function safeProfileProjectionFromExtraction(
  fields: StudentDocumentExtraction["fields"],
  acceptedFieldKeys: readonly string[],
): SafeProfileProjection {
  const accepted = new Set(acceptedFieldKeys);
  const projection: SafeProfileProjection = {};
  for (const field of fields) {
    if (!accepted.has(field.key)) continue;
    const value = field.value.trim();
    if (!value) continue;
    switch (field.key) {
      case "preferred_name":
        if (value.length <= 120) projection.preferredName = value;
        break;
      case "pronouns":
        if (value.length <= 80) projection.pronouns = value;
        break;
      case "mobile_phone":
        if (/^\+?[0-9 ()-]{7,32}$/.test(value)) {
          projection.mobilePhone = value;
        }
        break;
      case "communication_preference": {
        const preference = value.toLowerCase();
        if (preference === "email" || preference === "sms") {
          projection.communicationPreference = preference;
        }
        break;
      }
    }
  }
  return projection;
}

function mapOnboarding(row: OnboardingRow): StudentOnboarding {
  return {
    studentId: row.student_id,
    status: row.status,
    currentStep: row.current_step,
    completedSteps: row.completed_steps,
    data: row.payload,
    version: row.version,
    completedAt: nullableIsoTimestamp(row.completed_at),
    updatedAt: isoTimestamp(row.updated_at),
  };
}

function mapHousingPlan(
  row: OnboardingRow,
  residences: StudentHousingPlan["residences"] = [],
): StudentHousingPlan {
  const preference =
    row.payload.housingPreference === "on_campus" ||
    row.payload.housingPreference === "off_campus" ||
    row.payload.housingPreference === "commuting" ||
    row.payload.housingPreference === "undecided" ||
    row.payload.housingPreference === "family"
      ? row.payload.housingPreference
      : null;
  const residenceOption =
    row.payload.housingResidenceOption === "aster_residence_hall" ||
    row.payload.housingResidenceOption === "aster_apartments" ||
    row.payload.housingResidenceOption === "student_village"
      ? row.payload.housingResidenceOption
      : null;
  return {
    preference,
    residenceOption,
    residences,
    version: row.version,
    updatedAt: isoTimestamp(row.updated_at),
  };
}

function mapRequirement(row: RequirementRow): StudentRequirementDetail {
  const rewardPoints = Number(row.reward_points ?? 0);
  return {
    id: row.id,
    slug: studentRequirementSlug(row.code),
    journeyId: row.journey_id,
    code: row.code,
    title: row.title,
    description: row.description,
    status: row.status,
    blocking: row.blocking === 1,
    dueAt: nullableIsoTimestamp(row.due_at),
    progressPercent: row.progress_percent,
    submissionType: row.submission_type,
    documentCategory: documentCategoryForRequirement(row.code),
    responsibleOffice: row.responsible_office,
    dependencyCodes: row.depends_on_codes,
    ...(rewardPoints > 0
      ? {
          reward: {
            points: rewardPoints,
            earned: row.reward_earned === true,
          },
        }
      : {}),
  };
}

function mapMessage(row: MessageRow): StudentMessage {
  return {
    id: row.id,
    subject: row.subject,
    body: row.body,
    senderName: row.sender_name,
    sentAt: isoTimestamp(row.sent_at),
    readAt: nullableIsoTimestamp(row.read_at),
  };
}

function mapDocument(row: DocumentRow): StudentDocument {
  return {
    id: row.id,
    ...(row.requirement_id ? { requirementId: row.requirement_id } : {}),
    fileName: row.file_name,
    mimeType: row.mime_type,
    sizeBytes: row.size_bytes,
    category: row.category,
    processingMode: row.processing_mode,
    status: row.status,
    ...(row.storage_key
      ? { contentUrl: `/v1/student/documents/${row.id}/content` }
      : {}),
    ...(row.sha256 ? { sha256: row.sha256 } : {}),
    ...(row.extraction ? { extraction: row.extraction } : {}),
    createdAt: isoTimestamp(row.created_at),
  };
}

function mapAppointment(row: AppointmentRow): StudentAppointment {
  return {
    id: row.id,
    type: row.type,
    startsAt: isoTimestamp(row.starts_at),
    notes: row.notes,
    status: row.status,
    createdAt: isoTimestamp(row.created_at),
  };
}

function mapPayment(row: PaymentRow): StudentPayment {
  return {
    id: row.id,
    offerId: row.offer_id,
    type: "enrollment_deposit",
    amountCents: row.amount_cents,
    status: row.status,
    processor: "dummy",
    processorReference: row.processor_reference,
    createdAt: isoTimestamp(row.created_at),
  };
}

function mapProfile(row: ProfileRow): StudentProfile {
  return {
    studentId: row.student_id,
    preferredName: row.preferred_name,
    ...(row.first_name ? { firstName: row.first_name } : {}),
    ...(row.last_name ? { lastName: row.last_name } : {}),
    ...(row.email
      ? {
          email: row.email,
          emailVerified: row.email_verified === true,
          phoneVerified: row.phone_verified === true,
        }
      : {}),
    pronouns: row.pronouns,
    mobilePhone: row.mobile_phone,
    communicationPreference: row.communication_preference,
    version: row.version,
    updatedAt: isoTimestamp(row.updated_at),
  };
}

export class PostgresPortalStore {
  constructor(protected readonly database: DatabaseService) {}

  async getStudentBootstrap(auth: AuthContext): Promise<StudentBootstrap> {
    await this.database.db.transaction((transaction) =>
      this.reconcileAuthoritativeRewards(transaction, auth),
    );
    const result = await this.database.db.execute(sql`
      SELECT
        s.id AS student_id,
        COALESCE(sp.preferred_name, p.preferred_name, p.first_name)
          AS preferred_name,
        p.first_name || ' ' || p.last_name AS full_name,
        so.status,
        so.current_step,
        so.payload,
        so.version
      FROM student s
      JOIN person p ON p.id = s.person_id AND p.tenant_id = s.tenant_id
      JOIN student_onboarding so
        ON so.student_id = s.id AND so.tenant_id = s.tenant_id
      LEFT JOIN student_profile sp
        ON sp.student_id = s.id AND sp.tenant_id = s.tenant_id
      WHERE s.tenant_id = ${auth.tenantId}
        AND s.id = ${auth.studentId}
    `);
    const row = rows<{
      student_id: string;
      preferred_name: string;
      full_name: string;
      status: StudentOnboarding["status"];
      current_step: OnboardingStep;
      payload: StudentOnboardingData;
      version: number;
    }>(result)[0];
    if (!row) {
      throw new NotFoundError(
        "STUDENT_NOT_FOUND",
        "The authenticated student was not found",
      );
    }
    const required = row.status !== "completed";
    const rewards = await this.getStudentRewardSummary(auth);
    return {
      authenticated: true,
      student: {
        id: row.student_id,
        preferredName: row.preferred_name,
        fullName: row.full_name,
      },
      onboarding: {
        required,
        status: row.status,
        currentStep: row.current_step,
        version: row.version,
      },
      ...(rewards ? { rewards } : {}),
      initialRoute: required ? "/onboarding" : "/dashboard",
      generatedAt: new Date().toISOString(),
    };
  }

  async getStudentOnboarding(auth: AuthContext): Promise<StudentOnboarding> {
    const result = await this.database.db.execute(sql`
      SELECT
        student_id,
        status,
        current_step,
        completed_steps,
        payload,
        version,
        completed_at,
        updated_at
      FROM student_onboarding
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
    `);
    const row = rows<OnboardingRow>(result)[0];
    if (!row) {
      throw new NotFoundError(
        "STUDENT_ONBOARDING_NOT_FOUND",
        "Student onboarding was not found",
      );
    }
    return mapOnboarding(row);
  }

  async getStudentHousingPlan(auth: AuthContext): Promise<StudentHousingPlan> {
    const [result, residenceResult] = await Promise.all([
      this.database.db.execute(sql`
      SELECT
        student_id,
        status,
        current_step,
        completed_steps,
        payload,
        version,
        completed_at,
        updated_at
      FROM student_onboarding
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
      LIMIT 1
      `),
      this.database.db.execute(sql`
        SELECT
          residence.id,
          residence.code,
          residence.name,
          residence.description,
          residence.amenities,
          media.public_path,
          media.alt_text,
          media.attribution,
          media.source_url
        FROM housing_residence_option residence
        JOIN media_asset media
          ON media.id = residence.media_asset_id
         AND media.tenant_id = residence.tenant_id
         AND media.active = true
        WHERE residence.tenant_id = ${auth.tenantId}
          AND residence.active = true
        ORDER BY residence.display_order, residence.id
      `),
    ]);
    const row = rows<OnboardingRow>(result)[0];
    if (!row) {
      throw new NotFoundError(
        "STUDENT_ONBOARDING_NOT_FOUND",
        "Student onboarding was not found",
      );
    }
    const residences = rows<{
      id: string;
      code: NonNullable<StudentHousingPlan["residenceOption"]>;
      name: string;
      description: string;
      amenities: string[];
      public_path: string;
      alt_text: string;
      attribution: string;
      source_url: string;
    }>(residenceResult).map((residence) => ({
      id: residence.id,
      value: residence.code,
      name: residence.name,
      description: residence.description,
      amenities: residence.amenities,
      imageUrl: residence.public_path,
      imageAlt: residence.alt_text,
      attribution: residence.attribution,
      sourceUrl: residence.source_url,
    }));
    return mapHousingPlan(row, residences);
  }

  async updateStudentHousingPlan(input: {
    auth: AuthContext;
    update: UpdateStudentHousingPlanInput;
    requestId: string;
  }): Promise<StudentHousingPlan> {
    return this.database.db.transaction(async (transaction) => {
      const currentResult = await transaction.execute(sql`
        SELECT
          student_id,
          status,
          current_step,
          completed_steps,
          payload,
          version,
          completed_at,
          updated_at
        FROM student_onboarding
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
        FOR UPDATE
      `);
      const current = rows<OnboardingRow>(currentResult)[0];
      if (!current) {
        throw new NotFoundError(
          "STUDENT_ONBOARDING_NOT_FOUND",
          "Student onboarding was not found",
        );
      }
      if (current.version !== input.update.expectedVersion) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "Your housing plan changed in another session",
        );
      }

      const residenceOption =
        input.update.preference === "on_campus"
          ? input.update.residenceOption ?? null
          : null;
      const payload: StudentOnboardingData = {
        ...current.payload,
        housingPreference: input.update.preference,
        housingResidenceOption: residenceOption,
      };
      const now = new Date();
      const updatedResult = await transaction.execute(sql`
        UPDATE student_onboarding
        SET payload = ${JSON.stringify(payload)}::jsonb,
            version = version + 1,
            updated_at = ${now}
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND version = ${input.update.expectedVersion}
        RETURNING
          student_id,
          status,
          current_step,
          completed_steps,
          payload,
          version,
          completed_at,
          updated_at
      `);
      const updated = rows<OnboardingRow>(updatedResult)[0];
      if (!updated) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "Your housing plan changed in another session",
        );
      }
      await this.completeRequirementAndRefreshDependencies(
        transaction,
        input.auth,
        "housing_preference",
      );
      await this.insertAudit(transaction, {
        auth: input.auth,
        action: "student_housing_plan.updated",
        resourceType: "student_onboarding",
        resourceId: input.auth.studentId,
        requestId: input.requestId,
        metadata: {
          preference: input.update.preference,
          residenceOption,
          version: updated.version,
        },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        eventName: "student.housing_plan_updated.v1",
        aggregateType: "student_onboarding",
        aggregateId: input.auth.studentId,
        aggregateVersion: updated.version,
        requestId: input.requestId,
        data: {
          studentId: input.auth.studentId,
          preference: input.update.preference,
          residenceOption,
        },
      });
      return mapHousingPlan(updated);
    });
  }

  async updateStudentOnboarding(input: {
    auth: AuthContext;
    update: UpdateStudentOnboardingInput;
    requestId: string;
  }): Promise<StudentOnboarding> {
    return this.database.db.transaction(async (transaction) => {
      const currentResult = await transaction.execute(sql`
        SELECT
          student_id,
          status,
          current_step,
          completed_steps,
          payload,
          version,
          completed_at,
          updated_at
        FROM student_onboarding
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
        FOR UPDATE
      `);
      const current = rows<OnboardingRow>(currentResult)[0];
      if (!current) {
        throw new NotFoundError(
          "STUDENT_ONBOARDING_NOT_FOUND",
          "Student onboarding was not found",
        );
      }
      if (current.status === "completed") {
        throw new ConflictError(
          "ONBOARDING_ALREADY_COMPLETED",
          "Completed onboarding cannot be changed",
        );
      }
      if (current.version !== input.update.expectedVersion) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "Onboarding changed in another session",
        );
      }
      const targetStep = input.update.currentStep;
      const targetStepIndex = ONBOARDING_STEPS.indexOf(targetStep);
      const currentStepIndex = ONBOARDING_STEPS.indexOf(current.current_step);
      const editingCompletedStep = current.completed_steps.includes(targetStep);
      if (
        targetStepIndex < 0 ||
        currentStepIndex < 0 ||
        (targetStep !== current.current_step && !editingCompletedStep) ||
        targetStepIndex > currentStepIndex
      ) {
        throw new ConflictError(
          "ONBOARDING_STEP_OUT_OF_ORDER",
          `The next required onboarding step is ${current.current_step}`,
        );
      }
      const expectedPriorSteps = ONBOARDING_STEPS.slice(0, currentStepIndex);
      if (
        current.completed_steps.length !== expectedPriorSteps.length ||
        current.completed_steps.some(
          (step, index) => step !== expectedPriorSteps[index],
        )
      ) {
        throw new ApiError(
          500,
          "ONBOARDING_STATE_INVALID",
          "The stored onboarding sequence is inconsistent",
        );
      }
      const skip = input.update.skip === true;
      if (skip && !isSkippableOnboardingStep(targetStep)) {
        throw new BadRequestError(
          "ONBOARDING_STEP_REQUIRED",
          "This onboarding step is required before you can continue",
        );
      }
      const { skippedSteps: _submittedSkippedSteps, ...submittedData } =
        input.update.data;
      const mergedData: StudentOnboardingData = {
        ...current.payload,
        ...submittedData,
        skippedSteps: skip
          ? [
              ...new Set([
                ...(current.payload.skippedSteps ?? []),
                targetStep,
              ]),
            ]
          : (current.payload.skippedSteps ?? []).filter(
              (step) => step !== targetStep,
            ),
      };
      if (!skip) {
        await this.validateOnboardingStep(
          transaction,
          input.auth,
          targetStep,
          mergedData,
        );
      }
      let synchronizedProfileVersion: number | null = null;
      if (
        targetStep === "about_you" &&
        mergedData.firstName &&
        mergedData.lastName &&
        mergedData.preferredName &&
        mergedData.mobilePhone &&
        mergedData.communicationPreference
      ) {
        await transaction.execute(sql`
          UPDATE person p
          SET first_name = ${mergedData.firstName},
              last_name = ${mergedData.lastName},
              preferred_name = ${mergedData.preferredName},
              updated_at = now()
          FROM student s
          WHERE s.tenant_id = ${input.auth.tenantId}
            AND s.id = ${input.auth.studentId}
            AND p.tenant_id = s.tenant_id
            AND p.id = s.person_id
        `);
        const profileUpdateResult = await transaction.execute(sql`
          UPDATE student_profile
          SET preferred_name = ${mergedData.preferredName},
              mobile_phone = ${mergedData.mobilePhone},
              communication_preference = ${mergedData.communicationPreference},
              version = version + 1,
              updated_at = now()
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
          RETURNING version
        `);
        synchronizedProfileVersion =
          rows<{ version: number }>(profileUpdateResult)[0]?.version ?? null;
        await this.completeRequirementAndRefreshDependencies(
          transaction,
          input.auth,
          "profile_verification",
        );
      }
      const advancingCurrentStep = targetStep === current.current_step;
      const completedSteps = advancingCurrentStep
        ? [...current.completed_steps, current.current_step]
        : current.completed_steps;
      const nextStep = advancingCurrentStep
        ? (ONBOARDING_STEPS[currentStepIndex + 1] ??
          ONBOARDING_STEPS[currentStepIndex])
        : current.current_step;
      if (!nextStep) {
        throw new ApiError(
          500,
          "ONBOARDING_CONFIGURATION_INVALID",
          "The onboarding sequence is empty",
        );
      }
      const now = new Date();
      const updateResult = await transaction.execute(sql`
        UPDATE student_onboarding
        SET status = 'in_progress',
            current_step = ${nextStep},
            completed_steps = ARRAY(
              SELECT jsonb_array_elements_text(
                ${JSON.stringify(completedSteps)}::jsonb
              )
            ),
            payload = ${JSON.stringify(mergedData)}::jsonb,
            version = version + 1,
            updated_at = ${now}
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
        RETURNING
          student_id,
          status,
          current_step,
          completed_steps,
          payload,
          version,
          completed_at,
          updated_at
      `);
      const updated = rows<OnboardingRow>(updateResult)[0];
      if (!updated) {
        throw new ApiError(
          500,
          "ONBOARDING_UPDATE_FAILED",
          "Onboarding could not be saved",
        );
      }
      await this.insertAudit(transaction, {
        auth: input.auth,
        action: advancingCurrentStep
          ? "student_onboarding.step_completed"
          : "student_onboarding.step_updated",
        resourceType: "student_onboarding",
        resourceId: input.auth.studentId,
        requestId: input.requestId,
        metadata: {
          step: targetStep,
          skipped: skip,
          version: updated.version,
          profileSynchronized: synchronizedProfileVersion !== null,
        },
      });
      if (synchronizedProfileVersion !== null) {
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "student.profile_updated.v1",
          aggregateType: "student_profile",
          aggregateId: input.auth.studentId,
          aggregateVersion: synchronizedProfileVersion,
          requestId: input.requestId,
          data: {
            studentId: input.auth.studentId,
            source: "onboarding.about_you",
            changedFields: [
              "firstName",
              "lastName",
              "preferredName",
              "mobilePhone",
              "communicationPreference",
            ],
          },
        });
      }
      return mapOnboarding(updated);
    });
  }

  async completeStudentOnboarding(input: {
    auth: AuthContext;
    update: CompleteStudentOnboardingInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentOnboarding> {
    return this.runIdempotent(
      input,
      "student_onboarding.complete",
      input.update,
      200,
      async (transaction) => {
        const result = await transaction.execute(sql`
          SELECT
            student_id,
            status,
            current_step,
            completed_steps,
            payload,
            version,
            completed_at,
            updated_at
          FROM student_onboarding
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
          FOR UPDATE
        `);
        const current = rows<OnboardingRow>(result)[0];
        if (!current) {
          throw new NotFoundError(
            "STUDENT_ONBOARDING_NOT_FOUND",
            "Student onboarding was not found",
          );
        }
        if (current.status === "completed") return mapOnboarding(current);
        if (current.version !== input.update.expectedVersion) {
          throw new ConflictError(
            "VERSION_CONFLICT",
            "Onboarding changed in another session",
          );
        }
        if (
          current.completed_steps.length !== ONBOARDING_STEPS.length ||
          current.completed_steps.some(
            (step, index) => step !== ONBOARDING_STEPS[index],
          ) ||
          (current.payload.skippedSteps ?? []).some(
            (step) => !isSkippableOnboardingStep(step),
          )
        ) {
          throw new ConflictError(
            "ONBOARDING_INCOMPLETE",
            "Every required onboarding step must be completed in order",
          );
        }
        const now = new Date();
        const updateResult = await transaction.execute(sql`
          UPDATE student_onboarding
          SET status = 'completed',
              completed_at = ${now},
              version = version + 1,
              updated_at = ${now}
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
          RETURNING
            student_id,
            status,
            current_step,
            completed_steps,
            payload,
            version,
            completed_at,
            updated_at
        `);
        const updated = rows<OnboardingRow>(updateResult)[0];
        if (!updated) {
          throw new ApiError(
            500,
            "ONBOARDING_COMPLETION_FAILED",
            "Onboarding could not be completed",
          );
        }
        await this.awardMatchingRewards(
          transaction,
          input.auth,
          "onboarding_completed",
          "onboarding",
          input.auth.studentId,
          {},
        );
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "student_onboarding.completed",
          resourceType: "student_onboarding",
          resourceId: input.auth.studentId,
          requestId: input.requestId,
          metadata: { version: updated.version },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "student.onboarding_completed.v1",
          aggregateType: "student_onboarding",
          aggregateId: input.auth.studentId,
          aggregateVersion: updated.version,
          requestId: input.requestId,
          data: { studentId: input.auth.studentId },
        });
        return mapOnboarding(updated);
      },
    );
  }

  async getStudentRequirements(
    auth: AuthContext,
  ): Promise<StudentRequirementList> {
    const result = await this.database.db.execute(sql`
      SELECT
        sr.id,
        sr.journey_id,
        rdv.code,
        rdv.title,
        rdv.description,
        sr.status,
        rdv.blocking,
        sr.due_at,
        sr.progress_percent,
        rdv.submission_type,
        rdv.responsible_office,
        rdv.depends_on_codes,
        reward.reward_points,
        reward.reward_earned
      FROM student_requirement sr
      JOIN enrollment_journey j
        ON j.id = sr.journey_id AND j.tenant_id = sr.tenant_id
      JOIN requirement_definition_version rdv
        ON rdv.id = sr.requirement_definition_version_id
       AND rdv.tenant_id = sr.tenant_id
      LEFT JOIN LATERAL (
        SELECT
          COALESCE(SUM(rr.points), 0)::integer AS reward_points,
          CASE
            WHEN COUNT(rr.id) = 0 THEN false
            ELSE BOOL_AND(
              EXISTS (
                SELECT 1
                FROM student_reward_ledger ledger
                WHERE ledger.tenant_id = sr.tenant_id
                  AND ledger.student_id = ${auth.studentId}
                  AND ledger.reward_rule_id = rr.id
                  AND ledger.source_key = sr.id::text
              )
            )
          END AS reward_earned
        FROM tenant_reward_rule rr
        WHERE rr.tenant_id = sr.tenant_id
          AND rr.trigger_type = 'requirement_completed'
          AND rr.trigger_key = rdv.code
          AND rr.enabled = true
          AND (rr.starts_at IS NULL OR rr.starts_at <= NOW())
          AND (rr.ends_at IS NULL OR rr.ends_at > NOW())
      ) reward ON true
      WHERE sr.tenant_id = ${auth.tenantId}
        AND j.student_id = ${auth.studentId}
      ORDER BY rdv.display_order, sr.created_at
    `);
    const items = rows<RequirementRow>(result).map(mapRequirement);
    return { items, total: items.length };
  }

  async getStudentRequirement(
    auth: AuthContext,
    requirementIdentifier: string,
  ): Promise<StudentRequirementDetail> {
    const requirementCode = studentRequirementCodeFromSlug(
      requirementIdentifier,
    );
    const result = await this.database.db.execute(sql`
      SELECT
        sr.id,
        sr.journey_id,
        rdv.code,
        rdv.title,
        rdv.description,
        sr.status,
        rdv.blocking,
        sr.due_at,
        sr.progress_percent,
        rdv.submission_type,
        rdv.responsible_office,
        rdv.depends_on_codes,
        reward.reward_points,
        reward.reward_earned
      FROM student_requirement sr
      JOIN enrollment_journey j
        ON j.id = sr.journey_id AND j.tenant_id = sr.tenant_id
      JOIN requirement_definition_version rdv
        ON rdv.id = sr.requirement_definition_version_id
       AND rdv.tenant_id = sr.tenant_id
      LEFT JOIN LATERAL (
        SELECT
          COALESCE(SUM(rr.points), 0)::integer AS reward_points,
          CASE
            WHEN COUNT(rr.id) = 0 THEN false
            ELSE BOOL_AND(
              EXISTS (
                SELECT 1
                FROM student_reward_ledger ledger
                WHERE ledger.tenant_id = sr.tenant_id
                  AND ledger.student_id = ${auth.studentId}
                  AND ledger.reward_rule_id = rr.id
                  AND ledger.source_key = sr.id::text
              )
            )
          END AS reward_earned
        FROM tenant_reward_rule rr
        WHERE rr.tenant_id = sr.tenant_id
          AND rr.trigger_type = 'requirement_completed'
          AND rr.trigger_key = rdv.code
          AND rr.enabled = true
          AND (rr.starts_at IS NULL OR rr.starts_at <= NOW())
          AND (rr.ends_at IS NULL OR rr.ends_at > NOW())
      ) reward ON true
      WHERE sr.tenant_id = ${auth.tenantId}
        AND j.student_id = ${auth.studentId}
        AND (
          sr.id::text = ${requirementIdentifier}
          OR rdv.code = ${requirementCode}
        )
    `);
    const requirement = rows<RequirementRow>(result)[0];
    if (!requirement) {
      throw new NotFoundError(
        "STUDENT_REQUIREMENT_NOT_FOUND",
        "The requirement was not found",
      );
    }
    const mapped = mapRequirement(requirement);
    if (requirement.code !== "immunization_record") return mapped;
    const policy = await this.getImmunizationPolicyContext(auth);
    if (!policy) return mapped;
    return {
      ...mapped,
      immunizationPolicy: {
        id: policy.policyVersion.id,
        code: policy.policyVersion.code,
        version: policy.policyVersion.version,
        name: policy.policyVersion.name,
        effectiveFrom: policy.policyVersion.effectiveFrom,
        effectiveUntil: policy.policyVersion.effectiveUntil,
        requirements: policy.requirements.map((requirement) => ({
          id: requirement.id,
          code: requirement.code,
          name: requirement.name,
          description: requirement.description,
          required: requirement.required,
          doseCount: requirement.doseCount,
          validityDays: requirement.validityDays,
        })),
      },
    };
  }

  async getStudentMessages(auth: AuthContext): Promise<StudentMessageList> {
    const result = await this.database.db.execute(sql`
      SELECT id, subject, body, sender_name, sent_at, read_at
      FROM student_message
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
      ORDER BY sent_at DESC, id
    `);
    const items = rows<MessageRow>(result).map(mapMessage);
    return {
      items,
      unreadCount: items.filter((message) => message.readAt === null).length,
    };
  }

  async markStudentMessageRead(input: {
    auth: AuthContext;
    messageId: string;
    requestId: string;
  }): Promise<StudentMessage> {
    return this.database.db.transaction(async (transaction) => {
      const result = await transaction.execute(sql`
        SELECT id, subject, body, sender_name, sent_at, read_at
        FROM student_message
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND id = ${input.messageId}
        FOR UPDATE
      `);
      const current = rows<MessageRow>(result)[0];
      if (!current) {
        throw new NotFoundError(
          "STUDENT_MESSAGE_NOT_FOUND",
          "The message was not found",
        );
      }
      if (current.read_at) return mapMessage(current);
      const updateResult = await transaction.execute(sql`
        UPDATE student_message
        SET read_at = NOW()
        WHERE id = ${input.messageId}
        RETURNING id, subject, body, sender_name, sent_at, read_at
      `);
      const updated = rows<MessageRow>(updateResult)[0];
      if (!updated) {
        throw new ApiError(
          500,
          "MESSAGE_UPDATE_FAILED",
          "The message could not be marked as read",
        );
      }
      await this.insertAudit(transaction, {
        auth: input.auth,
        action: "student_message.read",
        resourceType: "student_message",
        resourceId: input.messageId,
        requestId: input.requestId,
        metadata: {},
      });
      return mapMessage(updated);
    });
  }

  async getStudentDocuments(auth: AuthContext): Promise<StudentDocumentList> {
    const result = await this.database.db.execute(sql`
      SELECT
        id, requirement_id, file_name, mime_type, size_bytes, category, processing_mode, status,
        storage_key, sha256, extraction, created_at
      FROM document_record
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
      ORDER BY created_at DESC, id
    `);
    const items = rows<DocumentRow>(result).map(mapDocument);
    return { items, total: items.length };
  }

  async getCourseExemptionContext(input: {
    auth: AuthContext;
    courses: NonNullable<StudentDocumentExtraction["courses"]>;
  }): Promise<CourseExemptionContext | null> {
    const baseResult = await this.database.db.execute(sql`
      SELECT
        p.id AS program_id,
        p.code AS program_code,
        p.name AS program_name,
        ccv.id AS catalog_version_id,
        ccv.code AS catalog_code,
        ccv.effective_from,
        ccv.updated_at
      FROM admission_offer ao
      JOIN program p
        ON p.id = ao.program_id
       AND p.tenant_id = ao.tenant_id
      JOIN course_catalog_version ccv
        ON ccv.tenant_id = ao.tenant_id
       AND ccv.status = 'active'
      WHERE ao.tenant_id = ${input.auth.tenantId}
        AND ao.student_id = ${input.auth.studentId}
      ORDER BY ccv.effective_from DESC, ccv.updated_at DESC
      LIMIT 1
    `);
    const base = rows<{
      program_id: string;
      program_code: string;
      program_name: string;
      catalog_version_id: string;
      catalog_code: string;
      effective_from: string;
      updated_at: Date;
    }>(baseResult)[0];
    if (!base) return null;

    const [
      courseResult,
      requirementResult,
      prerequisiteResult,
      ruleResult,
    ] = await Promise.all([
      this.database.db.execute(sql`
        SELECT id, code, title, credits
        FROM catalog_course
        WHERE tenant_id = ${input.auth.tenantId}
          AND catalog_version_id = ${base.catalog_version_id}
          AND active = true
        ORDER BY code
      `),
      this.database.db.execute(sql`
        SELECT id, course_id, category, required, recommended_term
        FROM program_requirement
        WHERE tenant_id = ${input.auth.tenantId}
          AND program_id = ${base.program_id}
          AND catalog_version_id = ${base.catalog_version_id}
        ORDER BY recommended_term, id
      `),
      this.database.db.execute(sql`
        SELECT course_id, prerequisite_course_id, minimum_grade
        FROM course_prerequisite
        WHERE tenant_id = ${input.auth.tenantId}
          AND catalog_version_id = ${base.catalog_version_id}
        ORDER BY course_id, prerequisite_course_id
      `),
      this.database.db.execute(sql`
        SELECT
          id, code, version, source_type, source_code, minimum_score,
          minimum_grade, minimum_credits, target_course_id, confidence
        FROM course_equivalency_rule
        WHERE tenant_id = ${input.auth.tenantId}
          AND catalog_version_id = ${base.catalog_version_id}
          AND active = true
        ORDER BY code, version DESC
      `),
    ]);
    const ruleRows = rows<{
      id: string;
      code: string;
      version: number;
      source_type: string;
      source_code: string;
      minimum_score: string | number | null;
      minimum_grade: string | null;
      minimum_credits: string | number | null;
      target_course_id: string;
      confidence: string | number;
    }>(ruleResult);
    const highestRuleVersion = Math.max(
      0,
      ...ruleRows.map((rule) => Number(rule.version)),
    );
    return {
      program: {
        id: base.program_id,
        code: base.program_code,
        name: base.program_name,
      },
      catalogVersion: {
        id: base.catalog_version_id,
        code: base.catalog_code,
        effectiveFrom: String(base.effective_from),
        updatedAt: isoTimestamp(base.updated_at),
      },
      policyVersion: `${base.catalog_code}:rules-v${highestRuleVersion}`,
      catalogCourses: rows<{
        id: string;
        code: string;
        title: string;
        credits: string | number;
      }>(courseResult).map((course) => ({
        id: course.id,
        code: course.code,
        title: course.title,
        credits: Number(course.credits),
      })),
      programRequirements: rows<{
        id: string;
        course_id: string;
        category: string;
        required: boolean;
        recommended_term: number;
      }>(requirementResult).map((requirement) => ({
        id: requirement.id,
        courseId: requirement.course_id,
        category: requirement.category,
        required: requirement.required,
        recommendedTerm: requirement.recommended_term,
      })),
      prerequisites: rows<{
        course_id: string;
        prerequisite_course_id: string;
        minimum_grade: string | null;
      }>(prerequisiteResult).map((prerequisite) => ({
        courseId: prerequisite.course_id,
        prerequisiteCourseId: prerequisite.prerequisite_course_id,
        minimumGrade: prerequisite.minimum_grade,
      })),
      equivalencyRules: ruleRows.map((rule) => ({
        id: rule.id,
        code: rule.code,
        version: rule.version,
        sourceType: rule.source_type,
        sourceCode: rule.source_code,
        minimumScore:
          rule.minimum_score === null ? null : Number(rule.minimum_score),
        minimumGrade: rule.minimum_grade,
        minimumCredits:
          rule.minimum_credits === null ? null : Number(rule.minimum_credits),
        targetCourseId: rule.target_course_id,
        confidence: Number(rule.confidence),
      })),
    };
  }

  async getImmunizationPolicyContext(
    auth: AuthContext,
  ): Promise<ImmunizationPolicyContext | null> {
    const policyResult = await this.database.db.execute(sql`
      SELECT
        id, code, version, name, effective_from, effective_until, updated_at
      FROM immunization_policy_version
      WHERE tenant_id = ${auth.tenantId}
        AND status = 'published'
        AND effective_from <= CURRENT_DATE
        AND (effective_until IS NULL OR effective_until >= CURRENT_DATE)
      ORDER BY version DESC
      LIMIT 1
    `);
    const policy = rows<{
      id: string;
      code: string;
      version: number;
      name: string;
      effective_from: string;
      effective_until: string | null;
      updated_at: Date;
    }>(policyResult)[0];
    if (!policy) return null;
    const requirementResult = await this.database.db.execute(sql`
      SELECT
        id, code, name, description, required, dose_count, validity_days,
        applies_when, evidence_criteria
      FROM immunization_requirement_rule
      WHERE tenant_id = ${auth.tenantId}
        AND policy_version_id = ${policy.id}
        AND active = true
      ORDER BY display_order, code
    `);
    return {
      policyVersion: {
        id: policy.id,
        code: policy.code,
        version: policy.version,
        name: policy.name,
        effectiveFrom: String(policy.effective_from),
        effectiveUntil:
          policy.effective_until === null
            ? null
            : String(policy.effective_until),
        updatedAt: isoTimestamp(policy.updated_at),
      },
      requirements: rows<{
        id: string;
        code: string;
        name: string;
        description: string;
        required: boolean;
        dose_count: number | null;
        validity_days: number | null;
        applies_when: Record<string, unknown>;
        evidence_criteria: Record<string, unknown>;
      }>(requirementResult).map((requirement) => ({
        id: requirement.id,
        code: requirement.code,
        name: requirement.name,
        description: requirement.description,
        required: requirement.required,
        doseCount: requirement.dose_count,
        validityDays: requirement.validity_days,
        appliesWhen: requirement.applies_when,
        evidenceCriteria: requirement.evidence_criteria,
      })),
    };
  }

  async createStudentDocument(input: {
    auth: AuthContext;
    document: CreateStudentDocumentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    return this.runIdempotent(
      input,
      "student_document.create",
      input.document,
      201,
      async (transaction) => {
        const id = randomUUID();
        const result = await transaction.execute(sql`
          INSERT INTO document_record (
            id,
            tenant_id,
            student_id,
            file_name,
            mime_type,
            size_bytes,
            category,
            processing_mode,
            status,
            storage_provider
          )
          SELECT
            ${id},
            s.tenant_id,
            s.id,
            ${input.document.fileName},
            ${input.document.mimeType},
            ${input.document.sizeBytes},
            ${input.document.category},
            ${documentProcessingModeForCategory(input.document.category)},
            'placeholder',
            'local_placeholder'
          FROM student s
          WHERE s.tenant_id = ${input.auth.tenantId}
            AND s.id = ${input.auth.studentId}
          RETURNING
            id, requirement_id, file_name, mime_type, size_bytes, category, processing_mode, status,
            storage_key, sha256, extraction, created_at
        `);
        const created = rows<DocumentRow>(result)[0];
        if (!created) {
          throw new NotFoundError(
            "STUDENT_NOT_FOUND",
            "The authenticated student was not found",
          );
        }
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "document.placeholder_created",
          resourceType: "document_record",
          resourceId: id,
          requestId: input.requestId,
          metadata: {
            category: input.document.category,
            mimeType: input.document.mimeType,
            sizeBytes: input.document.sizeBytes,
          },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "document.placeholder_created.v1",
          aggregateType: "document_record",
          aggregateId: id,
          aggregateVersion: 1,
          requestId: input.requestId,
          data: {
            studentId: input.auth.studentId,
            category: input.document.category,
          },
        });
        return mapDocument(created);
      },
    );
  }

  async reserveStudentDocumentUpload(input: {
    auth: AuthContext;
    document: CreateStudentDocumentInput & { sha256: string };
    requirementId?: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    return this.runIdempotent(
      input,
      "student_document.upload.reserve",
      { document: input.document, requirementId: input.requirementId ?? null },
      201,
      async (transaction) => {
        let category = input.document.category;
        if (input.requirementId) {
          const requirementResult = await transaction.execute(sql`
            SELECT rdv.code, sr.status
            FROM student_requirement sr
            JOIN enrollment_journey j
              ON j.id = sr.journey_id AND j.tenant_id = sr.tenant_id
            JOIN requirement_definition_version rdv
              ON rdv.id = sr.requirement_definition_version_id
             AND rdv.tenant_id = sr.tenant_id
            WHERE sr.tenant_id = ${input.auth.tenantId}
              AND j.student_id = ${input.auth.studentId}
              AND sr.id = ${input.requirementId}
              AND rdv.submission_type = 'document'
            LIMIT 1
          `);
          const requirement = rows<{ code: string; status: string }>(
            requirementResult,
          )[0];
          if (!requirement) {
            throw new NotFoundError(
              "DOCUMENT_REQUIREMENT_NOT_FOUND",
              "The document requirement was not found",
            );
          }
          if (requirement.status === "blocked") {
            throw new ConflictError(
              "DOCUMENT_REQUIREMENT_BLOCKED",
              "Complete the prerequisite enrollment tasks before uploading this document.",
            );
          }
          category =
            documentCategoryForRequirement(requirement.code) ?? category;
        }
        const id = randomUUID();
        const storageKey = [
          input.auth.tenantId,
          input.auth.studentId,
          `${id}${documentExtension(input.document.mimeType)}`,
        ].join("/");
        const result = await transaction.execute(sql`
          INSERT INTO document_record (
            id,
            tenant_id,
            student_id,
            requirement_id,
            file_name,
            mime_type,
            size_bytes,
            category,
            processing_mode,
            status,
            storage_provider,
            storage_key,
            sha256
          )
          SELECT
            ${id},
            s.tenant_id,
            s.id,
            ${input.requirementId ?? null},
            ${input.document.fileName},
            ${input.document.mimeType},
            ${input.document.sizeBytes},
            ${category},
            ${documentProcessingModeForCategory(category)},
            'uploaded',
            's3',
            ${storageKey},
            ${input.document.sha256}
          FROM student s
          WHERE s.tenant_id = ${input.auth.tenantId}
            AND s.id = ${input.auth.studentId}
          RETURNING
            id, requirement_id, file_name, mime_type, size_bytes, category, processing_mode, status,
            storage_key, sha256, extraction, created_at
        `);
        const created = rows<DocumentRow>(result)[0];
        if (!created) {
          throw new NotFoundError(
            "STUDENT_NOT_FOUND",
            "The authenticated student was not found",
          );
        }
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "document.upload_reserved",
          resourceType: "document_record",
          resourceId: id,
          requestId: input.requestId,
          metadata: {
            category,
            requirementId: input.requirementId ?? null,
            mimeType: input.document.mimeType,
            sizeBytes: input.document.sizeBytes,
            sha256: input.document.sha256,
          },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "document.upload_reserved.v1",
          aggregateType: "document_record",
          aggregateId: id,
          aggregateVersion: 1,
          requestId: input.requestId,
          data: {
            studentId: input.auth.studentId,
            category,
            requirementId: input.requirementId ?? null,
          },
        });
        return mapDocument(created);
      },
    );
  }

  async claimStudentDocumentProcessing(input: {
    auth: AuthContext;
    documentId: string;
    retry?: boolean;
    requestId?: string;
    retryIdempotencyKey?: string;
  }): Promise<boolean> {
    const processingExtraction = JSON.stringify(
      documentProcessingExtraction(),
    );
    if (input.retry) {
      return this.database.db.transaction(async (transaction) => {
        if (!input.retryIdempotencyKey) {
          throw new BadRequestError(
            "IDEMPOTENCY_KEY_REQUIRED",
            "The Idempotency-Key header is required",
          );
        }
        const operation = `student_document.extraction.retry:${input.documentId}`;
        const requestHash = createHash("sha256")
          .update(
            JSON.stringify({
              tenantId: input.auth.tenantId,
              studentId: input.auth.studentId,
              documentId: input.documentId,
            }),
          )
          .digest("hex");
        const lockKey = [
          input.auth.tenantId,
          input.auth.actorId,
          operation,
          input.retryIdempotencyKey,
        ].join(":");
        await transaction.execute(
          sql`SELECT pg_advisory_xact_lock(hashtextextended(${lockKey}, 0))`,
        );
        const existingResult = await transaction.execute(sql`
          SELECT request_hash, response_body
          FROM idempotency_record
          WHERE tenant_id = ${input.auth.tenantId}
            AND actor_id = ${input.auth.actorId}
            AND operation = ${operation}
            AND idempotency_key = ${input.retryIdempotencyKey}
        `);
        const existing = rows<IdempotencyRow<Record<string, unknown>>>(
          existingResult,
        )[0];
        if (existing) {
          if (existing.request_hash !== requestHash) {
            throw new ConflictError(
              "IDEMPOTENCY_KEY_REUSED",
              "This idempotency key was already used for a different request",
            );
          }
          // The caller reads the current document after a false claim. This
          // handles both an in-flight replay and a completed failed retry
          // without issuing another parser request.
          return false;
        }
        const result = await transaction.execute(sql`
          UPDATE document_record
          SET
            status = 'processing',
            extraction = ${processingExtraction}::jsonb,
            updated_at = NOW()
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND id = ${input.documentId}
            AND status = 'uploaded'
            AND extraction IS NOT NULL
            AND (
              extraction ->> 'status' = 'pending_configuration'
              OR (
                extraction ->> 'status' = 'failed'
                AND COALESCE(extraction -> 'retryable', 'true'::jsonb) <> 'false'::jsonb
              )
            )
          RETURNING extraction
        `);
        const claimed = rows<{ extraction: StudentDocumentExtraction }>(
          result,
        )[0];
        if (!claimed) return false;
        await transaction.execute(sql`
          INSERT INTO idempotency_record (
            tenant_id,
            actor_id,
            operation,
            idempotency_key,
            request_hash,
            response_status,
            response_body,
            created_at,
            expires_at
          )
          VALUES (
            ${input.auth.tenantId},
            ${input.auth.actorId},
            ${operation},
            ${input.retryIdempotencyKey},
            ${requestHash},
            202,
            ${JSON.stringify({
              documentId: input.documentId,
              status: "processing",
            })}::jsonb,
            NOW(),
            NOW() + INTERVAL '24 hours'
          )
        `);
        const requestId = input.requestId ?? "document-extraction-retry";
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "document.extraction_retry_started",
          resourceType: "document_record",
          resourceId: input.documentId,
          requestId,
          metadata: {
            previousExtractionStatus: claimed.extraction.status,
          },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "document.extraction_retry_started.v1",
          aggregateType: "document_record",
          aggregateId: input.documentId,
          aggregateVersion: 2,
          requestId,
          data: {
            studentId: input.auth.studentId,
            previousExtractionStatus: claimed.extraction.status,
          },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "document.extraction_requested.v1",
          aggregateType: "document_record",
          aggregateId: input.documentId,
          aggregateVersion: 3,
          requestId,
          data: {
            studentId: input.auth.studentId,
            retry: true,
          },
        });
        return true;
      });
    }
    return this.database.db.transaction(async (transaction) => {
      const requestId = input.requestId ?? "document-extraction-request";
      const manualResult = await transaction.execute(sql`
        UPDATE document_record
        SET status = 'under_review', updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND id = ${input.documentId}
          AND status = 'uploaded'
          AND extraction IS NULL
          AND processing_mode = 'manual_review'
        RETURNING requirement_id, category
      `);
      const manual = rows<{
        requirement_id: string | null;
        category: StudentDocument["category"];
      }>(manualResult)[0];
      if (manual) {
        if (manual.requirement_id) {
          await transaction.execute(sql`
            UPDATE student_requirement
            SET status = 'under_review', progress_percent = 80, updated_at = NOW()
            WHERE tenant_id = ${input.auth.tenantId}
              AND id = ${manual.requirement_id}
              AND status <> 'completed'
          `);
        }
        if (manual.category === "financial_aid") {
          await transaction.execute(sql`
            UPDATE financial_document_requirement
            SET status = 'under_review',
                document_id = ${input.documentId},
                version = version + 1,
                updated_at = NOW()
            WHERE tenant_id = ${input.auth.tenantId}
              AND student_id = ${input.auth.studentId}
              AND code = 'verification_worksheet'
          `);
        }
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "document.stored_for_review",
          resourceType: "document_record",
          resourceId: input.documentId,
          requestId,
          metadata: {
            storageConfirmed: true,
            processingMode: "manual_review",
            category: manual.category,
          },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "document.stored_for_review.v1",
          aggregateType: "document_record",
          aggregateId: input.documentId,
          aggregateVersion: 2,
          requestId,
          data: {
            studentId: input.auth.studentId,
            category: manual.category,
            requirementId: manual.requirement_id,
          },
        });
        return false;
      }
      const result = await transaction.execute(sql`
        UPDATE document_record
        SET
          status = 'processing',
          extraction = ${processingExtraction}::jsonb,
          updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND id = ${input.documentId}
          AND status = 'uploaded'
          AND extraction IS NULL
          AND processing_mode IN ('agentic', 'classification_only')
        RETURNING id
      `);
      if (rows(result).length !== 1) return false;
      await this.insertAudit(transaction, {
        auth: input.auth,
        action: "document.extraction_queued",
        resourceType: "document_record",
        resourceId: input.documentId,
        requestId,
        metadata: { storageConfirmed: true },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        eventName: "document.extraction_requested.v1",
        aggregateType: "document_record",
        aggregateId: input.documentId,
        aggregateVersion: 2,
        requestId,
        data: {
          studentId: input.auth.studentId,
          retry: false,
        },
      });
      return true;
    });
  }

  async releaseStudentDocumentProcessing(input: {
    auth: AuthContext;
    documentId: string;
    requestId: string;
  }): Promise<void> {
    await this.database.db.transaction(async (transaction) => {
      const result = await transaction.execute(sql`
        UPDATE document_record
        SET status = 'uploaded', extraction = NULL, updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND id = ${input.documentId}
          AND status = 'processing'
        RETURNING id
      `);
      if (rows(result).length !== 1) {
        throw new ConflictError(
          "DOCUMENT_PROCESSING_STATE_CHANGED",
          "The document processing state changed before the upload could be retried",
        );
      }
      await this.insertAudit(transaction, {
        auth: input.auth,
        action: "document.storage_failed",
        resourceType: "document_record",
        resourceId: input.documentId,
        requestId: input.requestId,
        metadata: { retryable: true },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        eventName: "document.storage_failed.v1",
        aggregateType: "document_record",
        aggregateId: input.documentId,
        aggregateVersion: 2,
        requestId: input.requestId,
        data: {
          studentId: input.auth.studentId,
          retryable: true,
        },
      });
    });
  }

  async completeStudentDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    extraction: StudentDocumentExtraction;
    requestId: string;
    retryIdempotencyKey?: string;
  }): Promise<StudentDocument> {
    return this.database.db.transaction(async (transaction) => {
      const status =
        input.extraction.status === "completed" ? "needs_review" : "uploaded";
      const extractionCompleted = input.extraction.status === "completed";
      const inferredCategory = extractionCompleted
        ? documentCategoryForExtractionType(input.extraction.documentType)
        : "other";
      const result = await transaction.execute(sql`
        UPDATE document_record
        SET
          status = ${status},
          category = CASE
            WHEN category = 'other' THEN ${inferredCategory}
            ELSE category
          END,
          extraction = ${JSON.stringify(input.extraction)}::jsonb,
          updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND id = ${input.documentId}
          AND status = 'processing'
        RETURNING
          id, requirement_id, file_name, mime_type, size_bytes, category, processing_mode, status,
          storage_key, sha256, extraction, created_at
      `);
      const updated = rows<DocumentRow>(result)[0];
      if (!updated) {
        throw new ConflictError(
          "DOCUMENT_PROCESSING_STATE_CHANGED",
          "The document processing state changed before completion",
        );
      }
      const requirementCode =
        requirementCodeByDocumentCategory[updated.category];
      const classificationMatchesRequirement =
        !updated.requirement_id ||
        documentCategoryForExtractionType(input.extraction.documentType) ===
          updated.category;
      const automaticallyProjectedTranscript =
        updated.category === "transcript" &&
        extractionCompleted &&
        classificationMatchesRequirement &&
        input.extraction.documentType === "transcript";
      if (
        requirementCode &&
        extractionCompleted &&
        classificationMatchesRequirement
      ) {
        if (updated.requirement_id) {
          await transaction.execute(sql`
            UPDATE student_requirement sr
            SET
              status = 'under_review',
              progress_percent = 80,
              version = sr.version + 1,
              updated_at = NOW()
            FROM enrollment_journey j
            WHERE sr.tenant_id = ${input.auth.tenantId}
              AND sr.journey_id = j.id
              AND j.student_id = ${input.auth.studentId}
              AND sr.id = ${updated.requirement_id}
          `);
        } else {
          await transaction.execute(sql`
            UPDATE student_requirement sr
            SET
              status = 'under_review',
              progress_percent = 80,
              version = sr.version + 1,
              updated_at = NOW()
            FROM
              requirement_definition_version rdv,
              enrollment_journey j
            WHERE sr.tenant_id = ${input.auth.tenantId}
              AND sr.requirement_definition_version_id = rdv.id
              AND sr.journey_id = j.id
              AND j.student_id = ${input.auth.studentId}
              AND rdv.code = ${requirementCode}
          `);
        }
      }
      if (automaticallyProjectedTranscript) {
        await transaction.execute(sql`
          UPDATE document_record
          SET status = 'under_review',
              updated_at = NOW()
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND id = ${input.documentId}
        `);
        updated.status = "under_review";
        for (const course of input.extraction.courses ?? []) {
          const sourceLabel =
            `${course.sourceCode ?? ""} ${course.title}`.toLowerCase();
          const sourceType = sourceLabel.includes("ap ")
            ? "ap"
            : sourceLabel.includes("ib ")
              ? "ib"
              : "transcript";
          await transaction.execute(sql`
            INSERT INTO student_transcript_credit (
              id,
              tenant_id,
              student_id,
              source_document_id,
              source_type,
              source_code,
              title,
              grade_or_score,
              credits,
              institution_name,
              evidence,
              reviewed_at
            )
            SELECT
              ${randomUUID()},
              ${input.auth.tenantId},
              ${input.auth.studentId},
              ${input.documentId},
              ${sourceType},
              ${course.sourceCode},
              ${course.title},
              ${course.score ?? course.grade},
              ${course.credits},
              ${input.extraction.institutionName},
              ${JSON.stringify({
                term: course.term,
                confidence: course.confidence,
                projection: "automatic_transcript_extraction",
              })}::jsonb,
              NOW()
            WHERE NOT EXISTS (
              SELECT 1
              FROM student_transcript_credit stc
              WHERE stc.tenant_id = ${input.auth.tenantId}
                AND stc.student_id = ${input.auth.studentId}
                AND stc.source_document_id = ${input.documentId}
                AND COALESCE(stc.source_code, '') = COALESCE(${course.sourceCode}, '')
                AND stc.title = ${course.title}
            )
          `);
        }
        await this.projectCourseExemptionEvaluation(transaction, {
          auth: input.auth,
          documentId: input.documentId,
          extraction: input.extraction,
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "student.transcript_credits_imported.v1",
          aggregateType: "document_record",
          aggregateId: input.documentId,
          aggregateVersion: 3,
          requestId: input.requestId,
          data: {
            studentId: input.auth.studentId,
            courseCount: input.extraction.courses?.length ?? 0,
            projection: "automatic",
          },
        });
      }
      if (input.extraction.immunizationCompliance) {
        await this.persistImmunizationEvaluation(transaction, {
          auth: input.auth,
          documentId: input.documentId,
          extraction: input.extraction,
        });
      }
      if (
        updated.category === "financial_aid" &&
        extractionCompleted &&
        classificationMatchesRequirement
      ) {
        await transaction.execute(sql`
          UPDATE financial_document_requirement
          SET status = 'under_review',
              document_id = ${updated.id},
              version = version + 1,
              updated_at = NOW()
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND code = 'verification_worksheet'
        `);
      }
      await this.insertAudit(transaction, {
        auth: input.auth,
        action: "document.extraction_completed",
        resourceType: "document_record",
        resourceId: input.documentId,
        requestId: input.requestId,
        metadata: {
          status: input.extraction.status,
          provider: input.extraction.provider,
          model: input.extraction.model,
          extractedFieldCount: input.extraction.fields.length,
          failureCode: input.extraction.failureCode ?? null,
          retryable: input.extraction.retryable ?? null,
          automaticallyProjectedTranscript,
        },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        eventName: "document.extraction_completed.v1",
        aggregateType: "document_record",
        aggregateId: input.documentId,
        aggregateVersion: 2,
        requestId: input.requestId,
        data: {
          studentId: input.auth.studentId,
          category: updated.category,
          extractionStatus: input.extraction.status,
          failureCode: input.extraction.failureCode ?? null,
          retryable: input.extraction.retryable ?? null,
        },
      });
      const document = mapDocument(updated);
      if (input.retryIdempotencyKey) {
        const operation = `student_document.extraction.retry:${input.documentId}`;
        await transaction.execute(sql`
          UPDATE idempotency_record
          SET response_status = 200,
              response_body = ${JSON.stringify(document)}::jsonb
          WHERE tenant_id = ${input.auth.tenantId}
            AND actor_id = ${input.auth.actorId}
            AND operation = ${operation}
            AND idempotency_key = ${input.retryIdempotencyKey}
        `);
      }
      return document;
    });
  }

  async getStudentDocument(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<StudentDocument> {
    const result = await this.database.db.execute(sql`
      SELECT
        id, requirement_id, file_name, mime_type, size_bytes, category, processing_mode, status,
        storage_key, sha256, extraction, created_at
      FROM document_record
      WHERE tenant_id = ${input.auth.tenantId}
        AND student_id = ${input.auth.studentId}
        AND id = ${input.documentId}
      LIMIT 1
    `);
    const document = rows<DocumentRow>(result)[0];
    if (!document) {
      throw new NotFoundError(
        "STUDENT_DOCUMENT_NOT_FOUND",
        "The document was not found",
      );
    }
    return mapDocument(document);
  }

  async getStudentDocumentContentReference(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<{
    storageKey: string;
    fileName: string;
    mimeType: StudentDocument["mimeType"];
  }> {
    const result = await this.database.db.execute(sql`
      SELECT storage_key, file_name, mime_type
      FROM document_record
      WHERE tenant_id = ${input.auth.tenantId}
        AND student_id = ${input.auth.studentId}
        AND id = ${input.documentId}
        AND storage_key IS NOT NULL
      LIMIT 1
    `);
    const reference = rows<{
      storage_key: string;
      file_name: string;
      mime_type: StudentDocument["mimeType"];
    }>(result)[0];
    if (!reference) {
      throw new NotFoundError(
        "STUDENT_DOCUMENT_CONTENT_NOT_FOUND",
        "The uploaded document content was not found",
      );
    }
    return {
      storageKey: reference.storage_key,
      fileName: reference.file_name,
      mimeType: reference.mime_type,
    };
  }

  async confirmStudentDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    confirmation: ConfirmStudentDocumentExtractionInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    return this.runIdempotent(
      input,
      `student_document.extraction.confirm:${input.documentId}`,
      input.confirmation,
      200,
      async (transaction) => {
        const currentResult = await transaction.execute(sql`
          SELECT
            id, requirement_id, file_name, mime_type, size_bytes, category, processing_mode, status,
            storage_key, sha256, extraction, created_at
          FROM document_record
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND id = ${input.documentId}
          FOR UPDATE
        `);
        const current = rows<DocumentRow>(currentResult)[0];
        if (!current) {
          throw new NotFoundError(
            "STUDENT_DOCUMENT_NOT_FOUND",
            "The document was not found",
          );
        }
        if (!current.extraction || current.extraction.status !== "completed") {
          throw new ConflictError(
            "DOCUMENT_EXTRACTION_NOT_READY",
            "Document extraction is not ready for review",
          );
        }
        const availableKeys = new Set(
          current.extraction.fields.map((field) => field.key),
        );
        const acceptedFieldKeys = [
          ...new Set(input.confirmation.acceptedFieldKeys),
        ];
        if (acceptedFieldKeys.some((key) => !availableKeys.has(key))) {
          throw new BadRequestError(
            "UNKNOWN_EXTRACTED_FIELD",
            "One or more extracted fields do not belong to this document",
          );
        }
        const extraction: StudentDocumentExtraction = {
          ...current.extraction,
          acceptedFieldKeys,
          verifiedAt: new Date().toISOString(),
        };
        const updatedResult = await transaction.execute(sql`
          UPDATE document_record
          SET
            status = 'under_review',
            extraction = ${JSON.stringify(extraction)}::jsonb,
            updated_at = NOW()
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND id = ${input.documentId}
          RETURNING
            id, requirement_id, file_name, mime_type, size_bytes, category, processing_mode, status,
            storage_key, sha256, extraction, created_at
        `);
        const updated = rows<DocumentRow>(updatedResult)[0];
        if (!updated) {
          throw new ConflictError(
            "DOCUMENT_PROCESSING_STATE_CHANGED",
            "The document review state changed before confirmation",
          );
        }
        const profileProjection = safeProfileProjectionFromExtraction(
          extraction.fields,
          acceptedFieldKeys,
        );
        const projectedProfileFields = Object.keys(profileProjection);
        if (projectedProfileFields.length > 0) {
          const now = new Date();
          const profileResult = await transaction.execute(sql`
            UPDATE student_profile
            SET preferred_name = CASE
                  WHEN ${profileProjection.preferredName !== undefined}
                    THEN ${profileProjection.preferredName ?? ""}
                  ELSE preferred_name
                END,
                pronouns = CASE
                  WHEN ${profileProjection.pronouns !== undefined}
                    THEN ${profileProjection.pronouns ?? null}
                  ELSE pronouns
                END,
                mobile_phone = CASE
                  WHEN ${profileProjection.mobilePhone !== undefined}
                    THEN ${profileProjection.mobilePhone ?? null}
                  ELSE mobile_phone
                END,
                communication_preference = CASE
                  WHEN ${profileProjection.communicationPreference !== undefined}
                    THEN ${profileProjection.communicationPreference ?? "email"}
                  ELSE communication_preference
                END,
                version = version + 1,
                updated_at = ${now}
            WHERE tenant_id = ${input.auth.tenantId}
              AND student_id = ${input.auth.studentId}
            RETURNING
              student_id,
              preferred_name,
              pronouns,
              mobile_phone,
              communication_preference,
              version,
              updated_at
          `);
          const profile = rows<ProfileRow>(profileResult)[0];
          if (profile && profileProjection.preferredName !== undefined) {
            await transaction.execute(sql`
              UPDATE person p
              SET preferred_name = ${profile.preferred_name},
                  updated_at = ${now}
              FROM student s
              WHERE s.person_id = p.id
                AND s.tenant_id = p.tenant_id
                AND s.tenant_id = ${input.auth.tenantId}
                AND s.id = ${input.auth.studentId}
            `);
          }
          if (profile) {
            await this.insertOutbox(transaction, {
              auth: input.auth,
              eventName: "student.profile_updated.v1",
              aggregateType: "student_profile",
              aggregateId: input.auth.studentId,
              aggregateVersion: profile.version,
              requestId: input.requestId,
              data: {
                studentId: input.auth.studentId,
                changedFields: projectedProfileFields,
                source: "confirmed_document_extraction",
              },
            });
          }
        }
        if (
          extraction.documentType === "transcript" &&
          extraction.courses?.length
        ) {
          for (const course of extraction.courses) {
            const sourceLabel =
              `${course.sourceCode ?? ""} ${course.title}`.toLowerCase();
            const sourceType = sourceLabel.includes("ap ")
              ? "ap"
              : sourceLabel.includes("ib ")
                ? "ib"
                : "transcript";
            await transaction.execute(sql`
              INSERT INTO student_transcript_credit (
                id,
                tenant_id,
                student_id,
                source_document_id,
                source_type,
                source_code,
                title,
                grade_or_score,
                credits,
                institution_name,
                evidence,
                reviewed_at
              )
              SELECT
                ${randomUUID()},
                ${input.auth.tenantId},
                ${input.auth.studentId},
                ${input.documentId},
                ${sourceType},
                ${course.sourceCode},
                ${course.title},
                ${course.score ?? course.grade},
                ${course.credits},
                ${extraction.institutionName},
                ${JSON.stringify({
                  term: course.term,
                  confidence: course.confidence,
                })}::jsonb,
                NOW()
              WHERE NOT EXISTS (
                SELECT 1
                FROM student_transcript_credit stc
                WHERE stc.tenant_id = ${input.auth.tenantId}
                  AND stc.student_id = ${input.auth.studentId}
                  AND stc.source_document_id = ${input.documentId}
                  AND COALESCE(stc.source_code, '') = COALESCE(${course.sourceCode}, '')
                  AND stc.title = ${course.title}
              )
            `);
          }
          await this.projectCourseExemptionEvaluation(transaction, {
            auth: input.auth,
            documentId: input.documentId,
            extraction,
          });
          await this.insertOutbox(transaction, {
            auth: input.auth,
            eventName: "student.transcript_credits_imported.v1",
            aggregateType: "document_record",
            aggregateId: input.documentId,
            aggregateVersion: 3,
            requestId: input.requestId,
            data: {
              studentId: input.auth.studentId,
              courseCount: extraction.courses.length,
            },
          });
        }
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "document.extraction_confirmed",
          resourceType: "document_record",
          resourceId: input.documentId,
          requestId: input.requestId,
          metadata: { acceptedFieldKeys, projectedProfileFields },
        });
        return mapDocument(updated);
      },
    );
  }

  async getStudentAppointments(
    auth: AuthContext,
  ): Promise<StudentAppointmentList> {
    const result = await this.database.db.execute(sql`
      SELECT id, type, starts_at, notes, status, created_at
      FROM student_appointment
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
      ORDER BY starts_at, id
    `);
    const items = rows<AppointmentRow>(result).map(mapAppointment);
    return { items, total: items.length };
  }

  async createStudentAppointment(input: {
    auth: AuthContext;
    appointment: CreateStudentAppointmentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentAppointment> {
    const startsAt = new Date(input.appointment.startsAt);
    if (startsAt.getTime() <= Date.now()) {
      throw new BadRequestError(
        "APPOINTMENT_MUST_BE_FUTURE",
        "Appointment time must be in the future",
      );
    }
    return this.runIdempotent(
      input,
      "student_appointment.create",
      input.appointment,
      201,
      async (transaction) => {
        const id = randomUUID();
        const result = await transaction.execute(sql`
          INSERT INTO student_appointment (
            id, tenant_id, student_id, type, starts_at, notes, status
          )
          SELECT
            ${id},
            s.tenant_id,
            s.id,
            ${input.appointment.type},
            ${startsAt},
            ${input.appointment.notes ?? null},
            'scheduled'
          FROM student s
          WHERE s.tenant_id = ${input.auth.tenantId}
            AND s.id = ${input.auth.studentId}
          RETURNING id, type, starts_at, notes, status, created_at
        `);
        const created = rows<AppointmentRow>(result)[0];
        if (!created) {
          throw new NotFoundError(
            "STUDENT_NOT_FOUND",
            "The authenticated student was not found",
          );
        }
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "student_appointment.scheduled",
          resourceType: "student_appointment",
          resourceId: id,
          requestId: input.requestId,
          metadata: { type: input.appointment.type },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "student.appointment_scheduled.v1",
          aggregateType: "student_appointment",
          aggregateId: id,
          aggregateVersion: 1,
          requestId: input.requestId,
          data: { studentId: input.auth.studentId, startsAt: startsAt.toISOString() },
        });
        return mapAppointment(created);
      },
    );
  }

  async getStudentPayments(auth: AuthContext): Promise<StudentPaymentList> {
    const result = await this.database.db.execute(sql`
      SELECT
        id, offer_id, amount_cents, status, processor_reference, created_at
      FROM payment_transaction
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
      ORDER BY created_at DESC, id
    `);
    const items = rows<PaymentRow>(result).map(mapPayment);
    return { items, total: items.length };
  }

  async createDepositPayment(input: {
    auth: AuthContext;
    payment: CreateDepositPaymentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentPayment> {
    return this.runIdempotent(
      input,
      "student_payment.deposit",
      input.payment,
      200,
      async (transaction) => {
        const offerResult = await transaction.execute(sql`
          SELECT deposit_amount_cents
          FROM admission_offer
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND id = ${input.payment.offerId}
            AND status = 'accepted'
          FOR UPDATE
        `);
        const offer = rows<{ deposit_amount_cents: number }>(offerResult)[0];
        if (!offer) {
          throw new ConflictError(
            "ACCEPTED_OFFER_REQUIRED",
            "An accepted admission offer is required before paying a deposit",
          );
        }
        const existingResult = await transaction.execute(sql`
          SELECT
            id, offer_id, amount_cents, status, processor_reference, created_at
          FROM payment_transaction
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND offer_id = ${input.payment.offerId}
            AND type = 'enrollment_deposit'
            AND status = 'succeeded'
        `);
        const existing = rows<PaymentRow>(existingResult)[0];
        if (existing) return mapPayment(existing);
        const id = randomUUID();
        const processorReference = `dummy_${id.replaceAll("-", "")}`;
        const result = await transaction.execute(sql`
          INSERT INTO payment_transaction (
            id,
            tenant_id,
            student_id,
            offer_id,
            type,
            amount_cents,
            status,
            processor,
            processor_reference
          )
          VALUES (
            ${id},
            ${input.auth.tenantId},
            ${input.auth.studentId},
            ${input.payment.offerId},
            'enrollment_deposit',
            ${offer.deposit_amount_cents},
            'succeeded',
            'dummy',
            ${processorReference}
          )
          RETURNING
            id, offer_id, amount_cents, status, processor_reference, created_at
        `);
        const created = rows<PaymentRow>(result)[0];
        if (!created) {
          throw new ApiError(
            500,
            "PAYMENT_CREATE_FAILED",
            "The deposit could not be recorded",
          );
        }
        await this.completeRequirementAndRefreshDependencies(
          transaction,
          input.auth,
          "enrollment_deposit",
        );
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "payment.deposit_succeeded",
          resourceType: "payment_transaction",
          resourceId: id,
          requestId: input.requestId,
          metadata: {
            offerId: input.payment.offerId,
            amountCents: offer.deposit_amount_cents,
            processor: "dummy",
          },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "payment.deposit_succeeded.v1",
          aggregateType: "payment_transaction",
          aggregateId: id,
          aggregateVersion: 1,
          requestId: input.requestId,
          data: {
            studentId: input.auth.studentId,
            offerId: input.payment.offerId,
            amountCents: offer.deposit_amount_cents,
          },
        });
        return mapPayment(created);
      },
    );
  }

  async getStudentProfile(auth: AuthContext): Promise<StudentProfile> {
    const result = await this.database.db.execute(sql`
      SELECT
        sp.student_id,
        sp.preferred_name,
        p.first_name,
        p.last_name,
        ca.email_normalized AS email,
        ca.email_verified_at IS NOT NULL AS email_verified,
        ca.phone_verified_at IS NOT NULL AS phone_verified,
        sp.pronouns,
        sp.mobile_phone,
        sp.communication_preference,
        sp.version,
        sp.updated_at
      FROM student_profile sp
      JOIN student s
        ON s.id = sp.student_id
       AND s.tenant_id = sp.tenant_id
      JOIN person p
        ON p.id = s.person_id
       AND p.tenant_id = s.tenant_id
      LEFT JOIN credential_account ca
        ON ca.student_id = sp.student_id
       AND ca.tenant_id = sp.tenant_id
       AND ca.status = 'active'
      WHERE sp.tenant_id = ${auth.tenantId}
        AND sp.student_id = ${auth.studentId}
    `);
    const profile = rows<ProfileRow>(result)[0];
    if (!profile) {
      throw new NotFoundError(
        "STUDENT_PROFILE_NOT_FOUND",
        "The student profile was not found",
      );
    }
    return mapProfile(profile);
  }

  async getStudentAcademics(auth: AuthContext): Promise<StudentAcademics> {
    const programResult = await this.database.db.execute(sql`
      SELECT
        p.id,
        p.code,
        p.name,
        p.degree,
        p.total_credits,
        p.description,
        p.source_label,
        p.source_url,
        p.source_status,
        ccv.id AS catalog_id,
        ccv.code AS catalog_code,
        ccv.source_label AS catalog_source_label,
        ccv.source_url AS catalog_source_url,
        ccv.source_status AS catalog_source_status
      FROM admission_offer ao
      JOIN program p
        ON p.id = ao.program_id
       AND p.tenant_id = ao.tenant_id
      JOIN course_catalog_version ccv
        ON ccv.tenant_id = ao.tenant_id
       AND ccv.status = 'active'
      WHERE ao.tenant_id = ${auth.tenantId}
        AND ao.student_id = ${auth.studentId}
      ORDER BY ao.created_at DESC, ccv.effective_from DESC
      LIMIT 1
    `);
    const selected = rows<{
      id: string;
      code: string;
      name: string;
      degree: string;
      total_credits: number;
      description: string;
      source_label: string | null;
      source_url: string | null;
      source_status: "official_source" | "synthetic_preview" | "tenant_authored" | null;
      catalog_id: string;
      catalog_code: string;
      catalog_source_label: string | null;
      catalog_source_url: string | null;
      catalog_source_status: "official_source" | "synthetic_preview" | "tenant_authored" | null;
    }>(programResult)[0];
    if (!selected) {
      throw new NotFoundError(
        "STUDENT_ACADEMICS_NOT_FOUND",
        "No academic plan is available for this student",
      );
    }
    const availableResult = await this.database.db.execute(sql`
      SELECT
        id, code, name, degree, total_credits, description,
        source_label, source_url, source_status
      FROM program
      WHERE tenant_id = ${auth.tenantId}
      ORDER BY name
    `);
    const availablePrograms = rows<{
      id: string;
      code: string;
      name: string;
      degree: string;
      total_credits: number;
      description: string;
      source_label: string | null;
      source_url: string | null;
      source_status: "official_source" | "synthetic_preview" | "tenant_authored" | null;
    }>(availableResult).map(mapProgram);
    const courseResult = await this.database.db.execute(sql`
      SELECT
        cc.id,
        cc.code,
        cc.title,
        cc.description,
        cc.credits,
        cc.level,
        cc.availability_label,
        cc.instructor_names,
        cc.meeting_pattern,
        cc.resources,
        COALESCE(cc.source_url, ccv.source_url) AS source_url,
        ccv.source_label,
        ccv.source_status,
        COALESCE(
          json_agg(
            json_build_object(
              'courseCode', prerequisite.code,
              'minimumGrade', cp.minimum_grade
            )
          ) FILTER (WHERE prerequisite.id IS NOT NULL),
          '[]'::json
        ) AS prerequisites
      FROM catalog_course cc
      JOIN course_catalog_version ccv
        ON ccv.id = cc.catalog_version_id
       AND ccv.tenant_id = cc.tenant_id
      LEFT JOIN course_prerequisite cp
        ON cp.course_id = cc.id
       AND cp.tenant_id = cc.tenant_id
       AND cp.catalog_version_id = cc.catalog_version_id
      LEFT JOIN catalog_course prerequisite
        ON prerequisite.id = cp.prerequisite_course_id
      WHERE cc.tenant_id = ${auth.tenantId}
        AND cc.catalog_version_id = ${selected.catalog_id}
        AND cc.active = true
      GROUP BY cc.id, ccv.id
      ORDER BY cc.code
    `);
    const courses = rows<{
      id: string;
      code: string;
      title: string;
      description: string;
      credits: string | number;
      level: number;
      availability_label: string | null;
      instructor_names: string[];
      meeting_pattern: string | null;
      resources: CatalogCourse["resources"];
      source_label: string | null;
      source_url: string | null;
      source_status: "official_source" | "synthetic_preview" | "tenant_authored" | null;
      prerequisites: CatalogCourse["prerequisites"];
    }>(courseResult).map(mapCatalogCourse);
    const courseById = new Map(courses.map((course) => [course.id, course]));
    const requirementResult = await this.database.db.execute(sql`
      SELECT course_id, category, recommended_term
      FROM program_requirement
      WHERE tenant_id = ${auth.tenantId}
        AND program_id = ${selected.id}
        AND catalog_version_id = ${selected.catalog_id}
        AND required = true
      ORDER BY recommended_term, id
    `);
    const creditResult = await this.database.db.execute(sql`
      SELECT
        id,
        source_type,
        source_code,
        title,
        grade_or_score,
        credits,
        institution_name,
        source_document_id
      FROM student_transcript_credit
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
      ORDER BY created_at, id
    `);
    const transcriptCredits = rows<{
      id: string;
      source_type: StudentAcademics["transcriptCredits"][number]["sourceType"];
      source_code: string | null;
      title: string;
      grade_or_score: string | null;
      credits: string | number | null;
      institution_name: string | null;
      source_document_id: string | null;
    }>(creditResult).map((credit) => ({
      id: credit.id,
      sourceType: credit.source_type,
      sourceCode: credit.source_code,
      title: credit.title,
      gradeOrScore: credit.grade_or_score,
      credits: credit.credits === null ? null : Number(credit.credits),
      institutionName: credit.institution_name,
      sourceDocumentId: credit.source_document_id,
    }));
    const recommendationResult = await this.database.db.execute(sql`
      SELECT
        cer.id,
        cer.transcript_credit_id,
        target.code AS target_course_code,
        target.title AS target_course_title,
        rule.code AS rule_code,
        cer.rationale,
        cer.confidence,
        cer.status
      FROM course_exemption_recommendation cer
      JOIN catalog_course target ON target.id = cer.target_course_id
      JOIN course_equivalency_rule rule ON rule.id = cer.equivalency_rule_id
      WHERE cer.tenant_id = ${auth.tenantId}
        AND cer.student_id = ${auth.studentId}
        AND cer.program_id = ${selected.id}
        AND cer.status <> 'superseded'
      ORDER BY target.code, cer.created_at
    `);
    const exemptionRecommendations = rows<{
      id: string;
      transcript_credit_id: string;
      target_course_code: string;
      target_course_title: string;
      rule_code: string;
      rationale: string;
      confidence: string | number;
      status: StudentAcademics["exemptionRecommendations"][number]["status"];
    }>(recommendationResult).map((recommendation) => ({
      id: recommendation.id,
      transcriptCreditId: recommendation.transcript_credit_id,
      targetCourseCode: recommendation.target_course_code,
      targetCourseTitle: recommendation.target_course_title,
      ruleCode: recommendation.rule_code,
      rationale: recommendation.rationale,
      confidence: Number(recommendation.confidence),
      status: recommendation.status,
      requiresStaffReview: true,
    }));
    const recommendationByCode = new Map(
      exemptionRecommendations.map((recommendation) => [
        recommendation.targetCourseCode,
        recommendation,
      ]),
    );
    const approvedCodes = new Set(
      exemptionRecommendations
        .filter((recommendation) => recommendation.status === "approved")
        .map((recommendation) => recommendation.targetCourseCode),
    );
    const plan = rows<{
      course_id: string;
      category: StudentAcademics["plan"][number]["category"];
      recommended_term: number;
    }>(requirementResult)
      .map((requirement) => {
        const course = courseById.get(requirement.course_id);
        if (!course) return null;
        const recommendation = recommendationByCode.get(course.code);
        const missingPrerequisiteCodes = course.prerequisites
          .map((prerequisite) => prerequisite.courseCode)
          .filter((code) => !approvedCodes.has(code));
        return {
          course,
          category: requirement.category,
          recommendedTerm: requirement.recommended_term,
          status:
            recommendation?.status === "approved"
              ? ("exempted" as const)
              : recommendation
                ? ("exemption_suggested" as const)
                : missingPrerequisiteCodes.length
                  ? ("blocked" as const)
                  : ("eligible" as const),
          satisfiedPrerequisiteCodes: course.prerequisites
            .map((prerequisite) => prerequisite.courseCode)
            .filter((code) => approvedCodes.has(code)),
          missingPrerequisiteCodes,
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null);
    const exemptedCredits = plan
      .filter((item) => item.status === "exempted")
      .reduce((total, item) => total + item.course.credits, 0);
    return {
      selectedProgram: mapProgram(selected),
      availablePrograms,
      transcriptCredits,
      exemptionRecommendations,
      plan,
      progress: {
        completedCredits: 0,
        exemptedCredits,
        requiredCredits: selected.total_credits,
        percent: Math.round((exemptedCredits / selected.total_credits) * 100),
      },
      catalogVersion: selected.catalog_code,
      generatedAt: new Date().toISOString(),
    };
  }

  async searchCatalogCourses(
    auth: AuthContext,
    query: string,
  ): Promise<{ items: CatalogCourse[]; total: number; catalogVersion: string }> {
    const normalized = query.trim().slice(0, 120);
    const pattern = `%${normalized}%`;
    const result = await this.database.db.execute(sql`
      SELECT
        cc.id,
        cc.code,
        cc.title,
        cc.description,
        cc.credits,
        cc.level,
        cc.availability_label,
        cc.instructor_names,
        cc.meeting_pattern,
        cc.resources,
        COALESCE(cc.source_url, ccv.source_url) AS source_url,
        ccv.source_label,
        ccv.source_status,
        ccv.code AS catalog_code,
        COALESCE(
          json_agg(
            json_build_object(
              'courseCode', prerequisite.code,
              'minimumGrade', cp.minimum_grade
            )
          ) FILTER (WHERE prerequisite.id IS NOT NULL),
          '[]'::json
        ) AS prerequisites
      FROM catalog_course cc
      JOIN course_catalog_version ccv
        ON ccv.id = cc.catalog_version_id
       AND ccv.tenant_id = cc.tenant_id
       AND ccv.status = 'active'
      LEFT JOIN course_prerequisite cp ON cp.course_id = cc.id
      LEFT JOIN catalog_course prerequisite
        ON prerequisite.id = cp.prerequisite_course_id
      WHERE cc.tenant_id = ${auth.tenantId}
        AND cc.active = true
        AND (
          ${normalized} = ''
          OR cc.code ILIKE ${pattern}
          OR cc.title ILIKE ${pattern}
          OR cc.description ILIKE ${pattern}
        )
      GROUP BY cc.id, ccv.id
      ORDER BY cc.code
      LIMIT 60
    `);
    const courseRows = rows<{
      id: string;
      code: string;
      title: string;
      description: string;
      credits: string | number;
      level: number;
      availability_label: string | null;
      instructor_names: string[];
      meeting_pattern: string | null;
      resources: CatalogCourse["resources"];
      source_label: string | null;
      source_url: string | null;
      source_status: "official_source" | "synthetic_preview" | "tenant_authored" | null;
      catalog_code: string;
      prerequisites: CatalogCourse["prerequisites"];
    }>(result);
    return {
      items: courseRows.map(mapCatalogCourse),
      total: courseRows.length,
      catalogVersion: courseRows[0]?.catalog_code ?? "unavailable",
    };
  }

  async getStudentFinancials(auth: AuthContext): Promise<StudentFinancials> {
    const summaryResult = await this.database.db.execute(sql`
      SELECT
        sfs.academic_year,
        sfs.cost_of_attendance_cents,
        sfs.external_payments_cents,
        COALESCE((
          SELECT SUM(pt.amount_cents)
          FROM payment_transaction pt
          WHERE pt.tenant_id = sfs.tenant_id
            AND pt.student_id = sfs.student_id
            AND pt.status = 'succeeded'
        ), 0) AS portal_payments_cents
      FROM student_financial_summary sfs
      WHERE sfs.tenant_id = ${auth.tenantId}
        AND sfs.student_id = ${auth.studentId}
      ORDER BY sfs.academic_year DESC
      LIMIT 1
    `);
    const summary = rows<{
      academic_year: string;
      cost_of_attendance_cents: number;
      external_payments_cents: number;
      portal_payments_cents: string | number;
    }>(summaryResult)[0];
    if (!summary) {
      throw new NotFoundError(
        "STUDENT_FINANCIALS_NOT_FOUND",
        "No financial record is available for this student",
      );
    }
    const [awardResult, documentResult, planResult, sapResult] =
      await Promise.all([
        this.database.db.execute(sql`
          SELECT
            id, source, name, type, offered_amount_cents,
            accepted_amount_cents, status, requires_action
          FROM student_financial_award
          WHERE tenant_id = ${auth.tenantId}
            AND student_id = ${auth.studentId}
            AND academic_year = ${summary.academic_year}
          ORDER BY type, name
        `),
        this.database.db.execute(sql`
          SELECT id, code, title, description, status, due_at
          FROM financial_document_requirement
          WHERE tenant_id = ${auth.tenantId}
            AND student_id = ${auth.studentId}
          ORDER BY due_at NULLS LAST, code
        `),
        this.database.db.execute(sql`
          SELECT
            id, name, installment_count, enrollment_fee_cents, status
          FROM student_payment_plan
          WHERE tenant_id = ${auth.tenantId}
            AND student_id = ${auth.studentId}
            AND academic_year = ${summary.academic_year}
            AND status <> 'cancelled'
          ORDER BY installment_count
        `),
        this.database.db.execute(sql`
          SELECT
            status,
            cumulative_gpa,
            minimum_gpa,
            completion_rate_percent,
            minimum_completion_rate_percent,
            attempted_credits,
            maximum_attempted_credits
          FROM student_sap_status
          WHERE tenant_id = ${auth.tenantId}
            AND student_id = ${auth.studentId}
            AND academic_year = ${summary.academic_year}
        `),
      ]);
    const awards = rows<{
      id: string;
      source: StudentFinancials["awards"][number]["source"];
      name: string;
      type: StudentFinancials["awards"][number]["type"];
      offered_amount_cents: number;
      accepted_amount_cents: number;
      status: StudentFinancials["awards"][number]["status"];
      requires_action: boolean;
    }>(awardResult).map((award) => ({
      id: award.id,
      source: award.source,
      name: award.name,
      type: award.type,
      offeredAmountCents: award.offered_amount_cents,
      acceptedAmountCents: award.accepted_amount_cents,
      status: award.status,
      requiresAction: award.requires_action,
    }));
    const acceptedAidCents = awards.reduce(
      (total, award) => total + award.acceptedAmountCents,
      0,
    );
    const pendingAidCents = awards.reduce(
      (total, award) =>
        total +
        (["offered", "pending"].includes(award.status)
          ? award.offeredAmountCents
          : 0),
      0,
    );
    const paymentsCents =
      summary.external_payments_cents + Number(summary.portal_payments_cents);
    const remainingBalanceCents = Math.max(
      0,
      summary.cost_of_attendance_cents - acceptedAidCents - paymentsCents,
    );
    const sap = rows<{
      status: StudentFinancials["sap"]["status"];
      cumulative_gpa: string | number;
      minimum_gpa: string | number;
      completion_rate_percent: string | number;
      minimum_completion_rate_percent: string | number;
      attempted_credits: string | number;
      maximum_attempted_credits: string | number;
    }>(sapResult)[0];
    if (!sap) {
      throw new NotFoundError(
        "STUDENT_SAP_NOT_FOUND",
        "No satisfactory academic progress record is available",
      );
    }
    return {
      academicYear: summary.academic_year.replace("-", "–"),
      costOfAttendanceCents: summary.cost_of_attendance_cents,
      acceptedAidCents,
      pendingAidCents,
      paymentsCents,
      remainingBalanceCents,
      awards,
      requiredDocuments: rows<{
        id: string;
        code: string;
        title: string;
        description: string;
        status: StudentFinancials["requiredDocuments"][number]["status"];
        due_at: Date | null;
      }>(documentResult).map((document) => ({
        id: document.id,
        code: document.code,
        title: document.title,
        description: document.description,
        status: document.status,
        dueAt: nullableIsoTimestamp(document.due_at),
        href: document.code === "award_acceptance" ? "/financials" : "/documents",
      })),
      paymentPlans: rows<{
        id: string;
        name: string;
        installment_count: number;
        enrollment_fee_cents: number;
        status: "available" | "enrolled";
      }>(planResult).map((plan) => ({
        id: plan.id,
        name: plan.name,
        installmentCount: plan.installment_count,
        installmentAmountCents: Math.ceil(
          remainingBalanceCents / plan.installment_count,
        ),
        enrollmentFeeCents: plan.enrollment_fee_cents,
        status: plan.status,
      })),
      sap: {
        status: sap.status,
        cumulativeGpa: Number(sap.cumulative_gpa),
        minimumGpa: Number(sap.minimum_gpa),
        completionRatePercent: Number(sap.completion_rate_percent),
        minimumCompletionRatePercent: Number(
          sap.minimum_completion_rate_percent,
        ),
        attemptedCredits: Number(sap.attempted_credits),
        maximumAttemptedCredits: Number(sap.maximum_attempted_credits),
      },
      generatedAt: new Date().toISOString(),
    };
  }

  async selectFinancialPaymentPlan(input: {
    auth: AuthContext;
    planId: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<{ planId: string; status: "enrolled" }> {
    return this.runIdempotent(
      input,
      "student_financial.payment_plan.select",
      { planId: input.planId },
      200,
      async (transaction) => {
        const found = await transaction.execute(sql`
          SELECT id, academic_year
          FROM student_payment_plan
          WHERE id = ${input.planId}
            AND tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND status <> 'cancelled'
          FOR UPDATE
        `);
        const plan = rows<{ id: string; academic_year: string }>(found)[0];
        if (!plan) {
          throw new NotFoundError(
            "PAYMENT_PLAN_NOT_FOUND",
            "The selected payment plan was not found",
          );
        }
        await transaction.execute(sql`
          UPDATE student_payment_plan
          SET status = CASE WHEN id = ${plan.id} THEN 'enrolled' ELSE 'available' END,
              enrolled_at = CASE WHEN id = ${plan.id} THEN NOW() ELSE NULL END,
              version = version + 1,
              updated_at = NOW()
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
            AND academic_year = ${plan.academic_year}
            AND status <> 'cancelled'
        `);
        await this.insertAudit(transaction, {
          auth: input.auth,
          action: "student_financial.payment_plan_selected",
          resourceType: "student_payment_plan",
          resourceId: plan.id,
          requestId: input.requestId,
          metadata: { academicYear: plan.academic_year },
        });
        await this.insertOutbox(transaction, {
          auth: input.auth,
          eventName: "student_financial.payment_plan_selected.v1",
          aggregateType: "student_payment_plan",
          aggregateId: plan.id,
          aggregateVersion: 1,
          requestId: input.requestId,
          data: { studentId: input.auth.studentId },
        });
        return { planId: plan.id, status: "enrolled" as const };
      },
    );
  }

  async getCampusLife(auth: AuthContext): Promise<CampusLifeFeed> {
    const [eventResult, clubResult, clubEventResult] = await Promise.all([
      this.database.db.execute(sql`
        SELECT
          id, title, description, starts_at, ends_at, location,
          category, featured, accent, source_label, source_url,
          source_status, registration_url, visual_theme, image_url,
          image_alt, image_attribution, image_source_url
        FROM campus_event
        WHERE tenant_id = ${auth.tenantId}
          AND active = true
        ORDER BY featured DESC, starts_at, id
        LIMIT 30
      `),
      this.database.db.execute(sql`
        SELECT
          club.id, club.name, club.category, club.description,
          club.contact_name, club.contact_role, club.contact_channel,
          club.latest_update, club.next_activity, club.source_label,
          club.source_url, club.source_status, club.social_links,
          club.long_description, club.meeting_schedule, club.membership_open,
          COALESCE(media.public_path, '/media/clubs/code-collective.jpg')
            AS image_url,
          COALESCE(media.alt_text, 'Students collaborating in a campus club')
            AS image_alt,
          COALESCE(media.attribution, 'Default Aster club image')
            AS image_attribution,
          COALESCE(media.source_url, '') AS image_source_url
        FROM student_club club
        LEFT JOIN media_asset media
          ON media.id = club.media_asset_id
         AND media.tenant_id = club.tenant_id
         AND media.active = true
        WHERE club.tenant_id = ${auth.tenantId}
          AND club.active = true
        ORDER BY club.name
        LIMIT 100
      `),
      this.database.db.execute(sql`
        SELECT
          id, club_id, title, description, starts_at, ends_at, location,
          category, registration_url
        FROM student_club_event
        WHERE tenant_id = ${auth.tenantId}
          AND active = true
        ORDER BY starts_at, id
        LIMIT 500
      `),
    ]);
    const clubEvents = rows<{
      id: string;
      club_id: string;
      title: string;
      description: string;
      starts_at: Date;
      ends_at: Date;
      location: string;
      category: NonNullable<
        CampusLifeFeed["clubs"][number]["events"]
      >[number]["category"];
      registration_url: string | null;
    }>(clubEventResult);
    return {
      events: rows<{
        id: string;
        title: string;
        description: string;
        starts_at: Date;
        ends_at: Date;
        location: string;
        category: CampusLifeFeed["events"][number]["category"];
        featured: boolean;
        accent: CampusLifeFeed["events"][number]["accent"];
        visual_theme: CampusLifeFeed["events"][number]["visualTheme"] | null;
        image_url: string | null;
        image_alt: string | null;
        image_attribution: string | null;
        image_source_url: string | null;
        source_label: string | null;
        source_url: string | null;
        source_status: "official_source" | "synthetic_preview" | "tenant_authored" | null;
        registration_url: string | null;
      }>(eventResult).map((event) => ({
        id: event.id,
        title: event.title,
        description: event.description,
        startsAt: isoTimestamp(event.starts_at),
        endsAt: isoTimestamp(event.ends_at),
        location: event.location,
        category: event.category,
        featured: event.featured,
        accent: event.accent,
        ...(event.visual_theme
          ? { visualTheme: event.visual_theme }
          : {}),
        imageUrl: event.image_url,
        imageAlt: event.image_alt,
        imageAttribution: event.image_attribution,
        imageSourceUrl: event.image_source_url,
        source: mapContentSource(event),
        registrationUrl: event.registration_url,
      })),
      clubs: rows<{
        id: string;
        name: string;
        category: string;
        description: string;
        contact_name: string;
        contact_role: string;
        contact_channel: string;
        latest_update: string;
        next_activity: string | null;
        image_url: string;
        image_alt: string;
        image_attribution: string;
        image_source_url: string;
        source_label: string | null;
        source_url: string | null;
        source_status: "official_source" | "synthetic_preview" | "tenant_authored" | null;
        social_links: CampusLifeFeed["clubs"][number]["socialLinks"];
        long_description: string | null;
        meeting_schedule: string | null;
        membership_open: boolean;
      }>(clubResult).map((club) => ({
        id: club.id,
        name: club.name,
        category: club.category,
        description: club.description,
        contactName: club.contact_name,
        contactRole: club.contact_role,
        contactChannel: club.contact_channel,
        latestUpdate: club.latest_update,
        nextActivity: club.next_activity,
        imageUrl: club.image_url,
        imageAlt: club.image_alt,
        imageAttribution: club.image_attribution,
        imageSourceUrl: club.image_source_url,
        source: mapContentSource(club),
        socialLinks: Array.isArray(club.social_links) ? club.social_links : [],
        longDescription: club.long_description,
        meetingSchedule: club.meeting_schedule,
        membershipOpen: club.membership_open,
        events: clubEvents
          .filter((event) => event.club_id === club.id)
          .map((event) => ({
            id: event.id,
            title: event.title,
            description: event.description,
            startsAt: isoTimestamp(event.starts_at),
            endsAt: isoTimestamp(event.ends_at),
            location: event.location,
            category: event.category,
            registrationUrl: event.registration_url,
          })),
      })),
      generatedAt: new Date().toISOString(),
    };
  }

  async updateStudentProfile(input: {
    auth: AuthContext;
    update: UpdateStudentProfileInput;
    requestId: string;
  }): Promise<StudentProfile> {
    const fields = [
      "preferredName",
      "pronouns",
      "mobilePhone",
      "communicationPreference",
    ] as const;
    const changedFields = fields.filter((field) =>
      Object.prototype.hasOwnProperty.call(input.update, field),
    );
    if (changedFields.length === 0) {
      throw new BadRequestError(
        "PROFILE_UPDATE_EMPTY",
        "At least one profile field must be supplied",
      );
    }
    return this.database.db.transaction(async (transaction) => {
      const hasPreferredName = changedFields.includes("preferredName");
      const hasPronouns = changedFields.includes("pronouns");
      const hasMobilePhone = changedFields.includes("mobilePhone");
      const hasPreference = changedFields.includes("communicationPreference");
      const now = new Date();
      const result = await transaction.execute(sql`
        UPDATE student_profile
        SET preferred_name = CASE
              WHEN ${hasPreferredName} THEN ${input.update.preferredName ?? ""}
              ELSE preferred_name
            END,
            pronouns = CASE
              WHEN ${hasPronouns} THEN ${input.update.pronouns ?? null}
              ELSE pronouns
            END,
            mobile_phone = CASE
              WHEN ${hasMobilePhone} THEN ${input.update.mobilePhone ?? null}
              ELSE mobile_phone
            END,
            communication_preference = CASE
              WHEN ${hasPreference}
                THEN ${input.update.communicationPreference ?? "email"}
              ELSE communication_preference
            END,
            version = version + 1,
            updated_at = ${now}
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND version = ${input.update.expectedVersion}
        RETURNING
          student_id,
          preferred_name,
          pronouns,
          mobile_phone,
          communication_preference,
          version,
          updated_at
      `);
      const updated = rows<ProfileRow>(result)[0];
      if (!updated) {
        const current = await transaction.execute(sql`
          SELECT version
          FROM student_profile
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.auth.studentId}
        `);
        if (rows<{ version: number }>(current).length === 0) {
          throw new NotFoundError(
            "STUDENT_PROFILE_NOT_FOUND",
            "The student profile was not found",
          );
        }
        throw new ConflictError(
          "VERSION_CONFLICT",
          "The profile changed in another session",
        );
      }
      if (hasPreferredName) {
        await transaction.execute(sql`
          UPDATE person p
          SET preferred_name = ${updated.preferred_name},
              updated_at = ${now}
          FROM student s
          WHERE s.person_id = p.id
            AND s.tenant_id = p.tenant_id
            AND s.tenant_id = ${input.auth.tenantId}
            AND s.id = ${input.auth.studentId}
        `);
      }
      await this.completeRequirementAndRefreshDependencies(
        transaction,
        input.auth,
        "profile_verification",
      );
      await this.insertAudit(transaction, {
        auth: input.auth,
        action: "student_profile.updated",
        resourceType: "student_profile",
        resourceId: input.auth.studentId,
        requestId: input.requestId,
        metadata: { changedFields, version: updated.version },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        eventName: "student.profile_updated.v1",
        aggregateType: "student_profile",
        aggregateId: input.auth.studentId,
        aggregateVersion: updated.version,
        requestId: input.requestId,
        data: {
          studentId: input.auth.studentId,
          changedFields,
        },
      });
      return mapProfile(updated);
    });
  }

  async getStudentHelp(auth: AuthContext): Promise<StudentHelp> {
    const result = await this.database.db.execute(sql`
      SELECT id, category, question, answer
      FROM help_article
      WHERE tenant_id = ${auth.tenantId}
        AND active = true
      ORDER BY sort_order, id
    `);
    return {
      articles: rows<{
        id: string;
        category: StudentHelp["articles"][number]["category"];
        question: string;
        answer: string;
      }>(result),
      support: {
        email: "enrollment-support@vv.example",
        phone: "+1 555 010 2027",
        hours: "Monday-Friday, 09:00-17:00",
      },
    };
  }

  private async runIdempotent<T>(
    input: {
      auth: AuthContext;
      idempotencyKey: string;
      requestId: string;
    },
    operation: string,
    requestPayload: unknown,
    responseStatus: number,
    handler: (transaction: Transaction) => Promise<T>,
  ): Promise<T> {
    const requestHash = createHash("sha256")
      .update(
        JSON.stringify({
          tenantId: input.auth.tenantId,
          studentId: input.auth.studentId,
          requestPayload,
        }),
      )
      .digest("hex");
    const lockKey = [
      input.auth.tenantId,
      input.auth.actorId,
      operation,
      input.idempotencyKey,
    ].join(":");
    return this.database.db.transaction(async (transaction) => {
      await transaction.execute(
        sql`SELECT pg_advisory_xact_lock(hashtextextended(${lockKey}, 0))`,
      );
      const existingResult = await transaction.execute(sql`
        SELECT request_hash, response_body
        FROM idempotency_record
        WHERE tenant_id = ${input.auth.tenantId}
          AND actor_id = ${input.auth.actorId}
          AND operation = ${operation}
          AND idempotency_key = ${input.idempotencyKey}
      `);
      const existing = rows<IdempotencyRow<T>>(existingResult)[0];
      if (existing) {
        if (existing.request_hash !== requestHash) {
          throw new ConflictError(
            "IDEMPOTENCY_KEY_REUSED",
            "This idempotency key was already used for a different request",
          );
        }
        return existing.response_body;
      }
      const response = await handler(transaction);
      await transaction.execute(sql`
        INSERT INTO idempotency_record (
          tenant_id,
          actor_id,
          operation,
          idempotency_key,
          request_hash,
          response_status,
          response_body,
          created_at,
          expires_at
        )
        VALUES (
          ${input.auth.tenantId},
          ${input.auth.actorId},
          ${operation},
          ${input.idempotencyKey},
          ${requestHash},
          ${responseStatus},
          ${JSON.stringify(response)}::jsonb,
          NOW(),
          NOW() + INTERVAL '24 hours'
        )
      `);
      return response;
    });
  }

  private async projectCourseExemptionEvaluation(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      documentId: string;
      extraction: StudentDocumentExtraction;
    },
  ): Promise<void> {
    const evaluation = input.extraction.courseExemptionEvaluation;
    if (!evaluation) return;
    for (const decision of evaluation.decisions) {
      if (
        (decision.status !== "matched" &&
          decision.status !== "needs_review") ||
        !decision.targetCourseId ||
        !decision.equivalencyRuleId
      ) {
        continue;
      }
      const recommendationStatus =
        decision.status === "matched" ? "suggested" : "needs_review";
      await transaction.execute(sql`
        INSERT INTO course_exemption_recommendation (
          id,
          tenant_id,
          student_id,
          program_id,
          catalog_version_id,
          transcript_credit_id,
          target_course_id,
          equivalency_rule_id,
          status,
          confidence,
          rationale
        )
        SELECT
          ${randomUUID()},
          stc.tenant_id,
          stc.student_id,
          ao.program_id,
          rule.catalog_version_id,
          stc.id,
          rule.target_course_id,
          rule.id,
          ${recommendationStatus},
          ${decision.confidence},
          ${decision.rationale}
        FROM student_transcript_credit stc
        JOIN admission_offer ao
          ON ao.tenant_id = stc.tenant_id
         AND ao.student_id = stc.student_id
        JOIN course_equivalency_rule rule
          ON rule.tenant_id = stc.tenant_id
         AND rule.id = ${decision.equivalencyRuleId}
         AND rule.target_course_id = ${decision.targetCourseId}
         AND rule.catalog_version_id = ${evaluation.catalogVersionId}
         AND rule.active = true
        WHERE stc.tenant_id = ${input.auth.tenantId}
          AND stc.student_id = ${input.auth.studentId}
          AND stc.source_document_id = ${input.documentId}
          AND COALESCE(stc.source_code, '') =
              COALESCE(${decision.sourceCode}, '')
          AND stc.title = ${decision.sourceTitle}
        ON CONFLICT (
          tenant_id,
          student_id,
          transcript_credit_id,
          target_course_id,
          equivalency_rule_id
        ) DO UPDATE SET
          status = CASE
            WHEN course_exemption_recommendation.status IN (
              'approved',
              'denied'
            )
              THEN course_exemption_recommendation.status
            ELSE EXCLUDED.status
          END,
          confidence = EXCLUDED.confidence,
          rationale = EXCLUDED.rationale,
          version = course_exemption_recommendation.version + 1,
          updated_at = NOW()
      `);
    }
  }

  private async persistImmunizationEvaluation(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      documentId: string;
      extraction: StudentDocumentExtraction;
    },
  ): Promise<void> {
    const evaluation = input.extraction.immunizationCompliance;
    if (!evaluation) return;
    await transaction.execute(sql`
      INSERT INTO student_immunization_evaluation (
        id,
        tenant_id,
        student_id,
        source_document_id,
        policy_version_id,
        result,
        generated_at
      )
      SELECT
        ${randomUUID()},
        ${input.auth.tenantId},
        ${input.auth.studentId},
        ${input.documentId},
        policy.id,
        ${JSON.stringify(evaluation)}::jsonb,
        ${new Date(evaluation.generatedAt)}
      FROM immunization_policy_version policy
      WHERE policy.id = ${evaluation.policyVersionId}
        AND policy.tenant_id = ${input.auth.tenantId}
        AND policy.status = 'published'
      ON CONFLICT (
        tenant_id,
        student_id,
        source_document_id,
        policy_version_id
      ) DO UPDATE SET
        result = EXCLUDED.result,
        generated_at = EXCLUDED.generated_at
    `);
  }

  private async validateOnboardingStep(
    transaction: Transaction,
    auth: AuthContext,
    step: OnboardingStep,
    data: StudentOnboardingData,
  ): Promise<void> {
    validateOnboardingStepData(step, data);
    switch (step) {
      case "offer": {
        const result = await transaction.execute(sql`
          SELECT 1
          FROM admission_offer
          WHERE tenant_id = ${auth.tenantId}
            AND student_id = ${auth.studentId}
            AND status = 'accepted'
          LIMIT 1
        `);
        if (rows(result).length === 0) {
          throw new ConflictError(
            "ACCEPTED_OFFER_REQUIRED",
            "Accept the admission offer before completing this step",
          );
        }
        return;
      }
      case "deposit": {
        if (data.depositChoice !== "pay_now") {
          return;
        }
        const result = await transaction.execute(sql`
          SELECT 1
          FROM payment_transaction
          WHERE tenant_id = ${auth.tenantId}
            AND student_id = ${auth.studentId}
            AND type = 'enrollment_deposit'
            AND status = 'succeeded'
          LIMIT 1
        `);
        if (rows(result).length === 0) {
          throw new ConflictError(
            "DEPOSIT_REQUIRED",
            "Complete the enrollment deposit before saving this step",
          );
        }
        return;
      }
      default:
        return;
    }
  }

  private async insertAudit(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      action: string;
      resourceType: string;
      resourceId: string;
      requestId: string;
      metadata: Record<string, unknown>;
    },
  ): Promise<void> {
    const lineage = getRuntimeLineage({
      correlationId: input.requestId,
      auditAction: input.action,
    });
    await transaction.execute(sql`
      INSERT INTO audit_event (
        id,
        tenant_id,
        actor_type,
        actor_id,
        student_id,
        action,
        resource_type,
        resource_id,
        authorization_basis,
        request_id,
        correlation_id,
        metadata
      )
      VALUES (
        ${randomUUID()},
        ${input.auth.tenantId},
        ${input.auth.actorType},
        ${input.auth.actorId},
        ${input.auth.studentId},
        ${input.action},
        ${input.resourceType},
        ${input.resourceId},
        'student_self_service',
        ${input.requestId},
        ${input.requestId},
        ${JSON.stringify({ ...input.metadata, lineage })}::jsonb
      )
    `);
  }

  private async completeRequirementAndRefreshDependencies(
    transaction: Transaction,
    auth: AuthContext,
    requirementCode: string,
  ): Promise<void> {
    const completionResult = await transaction.execute(sql`
      UPDATE student_requirement sr
      SET status = 'completed',
          progress_percent = 100,
          version = sr.version + 1,
          updated_at = NOW()
      FROM requirement_definition_version rdv, enrollment_journey j
      WHERE sr.tenant_id = ${auth.tenantId}
        AND sr.journey_id = j.id
        AND j.student_id = ${auth.studentId}
        AND sr.requirement_definition_version_id = rdv.id
        AND rdv.code = ${requirementCode}
        AND sr.status NOT IN ('completed', 'waived', 'not_applicable')
      RETURNING sr.id
    `);
    for (const completed of rows<{ id: string }>(completionResult)) {
      await this.awardMatchingRewards(
        transaction,
        auth,
        "requirement_completed",
        requirementCode,
        completed.id,
        {},
      );
    }
    await transaction.execute(sql`
      UPDATE student_requirement candidate
      SET status = 'ready',
          version = candidate.version + 1,
          updated_at = NOW()
      FROM requirement_definition_version candidate_definition,
           enrollment_journey candidate_journey
      WHERE candidate.tenant_id = ${auth.tenantId}
        AND candidate.journey_id = candidate_journey.id
        AND candidate_journey.student_id = ${auth.studentId}
        AND candidate.requirement_definition_version_id =
            candidate_definition.id
        AND candidate.status = 'blocked'
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(candidate_definition.depends_on_codes) dependency(code)
          WHERE NOT EXISTS (
            SELECT 1
            FROM student_requirement dependency_requirement
            JOIN requirement_definition_version dependency_definition
              ON dependency_definition.id =
                 dependency_requirement.requirement_definition_version_id
             AND dependency_definition.tenant_id =
                 dependency_requirement.tenant_id
            WHERE dependency_requirement.tenant_id = ${auth.tenantId}
              AND dependency_requirement.journey_id = candidate.journey_id
              AND dependency_definition.code = dependency.code
              AND dependency_requirement.status IN (
                'completed',
                'waived',
                'not_applicable'
              )
          )
        )
    `);
    await transaction.execute(sql`
      UPDATE student_portal_projection
      SET projection_version = projection_version + 1,
          source_updated_at = NOW()
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
    `);
  }

  protected async awardMatchingRewards(
    transaction: Transaction,
    auth: AuthContext,
    triggerType:
      | "onboarding_completed"
      | "requirement_completed"
      | "activity_event",
    triggerKey: string,
    sourceKey: string,
    properties: Record<string, string | number | boolean | null>,
  ): Promise<number> {
    const ruleResult = await transaction.execute(sql`
      SELECT id, points, max_awards_per_student
      FROM tenant_reward_rule
      WHERE tenant_id = ${auth.tenantId}
        AND trigger_type = ${triggerType}
        AND trigger_key = ${triggerKey}
        AND enabled = true
        AND (starts_at IS NULL OR starts_at <= NOW())
        AND (ends_at IS NULL OR ends_at > NOW())
        AND ${JSON.stringify(properties)}::jsonb @> trigger_properties
      ORDER BY display_order, id
      FOR UPDATE
    `);
    let awarded = 0;
    for (const rule of rows<{
      id: string;
      points: number;
      max_awards_per_student: number;
    }>(ruleResult)) {
      const insertResult = await transaction.execute(sql`
        INSERT INTO student_reward_ledger (
          id,
          tenant_id,
          student_id,
          reward_rule_id,
          source_type,
          source_key,
          points,
          metadata,
          awarded_at
        )
        SELECT
          ${randomUUID()},
          ${auth.tenantId},
          ${auth.studentId},
          ${rule.id},
          ${triggerType},
          ${sourceKey},
          ${rule.points},
          ${JSON.stringify({ triggerKey, properties })}::jsonb,
          NOW()
        WHERE (
          SELECT COUNT(*)
          FROM student_reward_ledger existing
          WHERE existing.tenant_id = ${auth.tenantId}
            AND existing.student_id = ${auth.studentId}
            AND existing.reward_rule_id = ${rule.id}
        ) < ${rule.max_awards_per_student}
        ON CONFLICT (
          tenant_id,
          student_id,
          reward_rule_id,
          source_key
        ) DO NOTHING
        RETURNING points
      `);
      awarded += rows<{ points: number }>(insertResult).reduce(
        (total, item) => total + Number(item.points),
        0,
      );
    }
    return awarded;
  }

  private async reconcileAuthoritativeRewards(
    transaction: Transaction,
    auth: AuthContext,
  ): Promise<void> {
    const onboardingResult = await transaction.execute(sql`
      SELECT status
      FROM student_onboarding
      WHERE tenant_id = ${auth.tenantId}
        AND student_id = ${auth.studentId}
    `);
    if (
      rows<{ status: StudentOnboarding["status"] }>(onboardingResult)[0]
        ?.status === "completed"
    ) {
      await this.awardMatchingRewards(
        transaction,
        auth,
        "onboarding_completed",
        "onboarding",
        auth.studentId,
        {},
      );
    }

    const requirementResult = await transaction.execute(sql`
      SELECT sr.id, rdv.code
      FROM student_requirement sr
      JOIN enrollment_journey journey
        ON journey.id = sr.journey_id
       AND journey.tenant_id = sr.tenant_id
      JOIN requirement_definition_version rdv
        ON rdv.id = sr.requirement_definition_version_id
       AND rdv.tenant_id = sr.tenant_id
      WHERE sr.tenant_id = ${auth.tenantId}
        AND journey.student_id = ${auth.studentId}
        AND sr.status = 'completed'
    `);
    for (const requirement of rows<{ id: string; code: string }>(
      requirementResult,
    )) {
      await this.awardMatchingRewards(
        transaction,
        auth,
        "requirement_completed",
        requirement.code,
        requirement.id,
        {},
      );
    }
  }

  private async getStudentRewardSummary(
    auth: AuthContext,
  ): Promise<StudentRewardSummary | null> {
    const result = await this.database.db.execute(sql`
      SELECT
        program.point_name,
        program.points_per_usd,
        COALESCE(SUM(ledger.points), 0)::bigint AS lifetime_points
      FROM tenant_reward_program program
      LEFT JOIN student_reward_ledger ledger
        ON ledger.tenant_id = program.tenant_id
       AND ledger.student_id = ${auth.studentId}
      WHERE program.tenant_id = ${auth.tenantId}
        AND program.enabled = true
      GROUP BY program.point_name, program.points_per_usd
    `);
    const reward = rows<{
      point_name: string;
      points_per_usd: number;
      lifetime_points: string | number;
    }>(result)[0];
    if (!reward) return null;
    const lifetimePoints = Number(reward.lifetime_points);
    const pointsPerUsd = Number(reward.points_per_usd);
    return {
      pointName: reward.point_name,
      pointsPerUsd,
      lifetimePoints,
      bookstoreCreditCents: Math.floor(
        (lifetimePoints * 100) / pointsPerUsd,
      ),
    };
  }

  private async insertOutbox(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      eventName: string;
      aggregateType: string;
      aggregateId: string;
      aggregateVersion: number;
      requestId: string;
      data: Record<string, unknown>;
    },
  ): Promise<void> {
    const eventId = randomUUID();
    const occurredAt = new Date();
    const causationId = randomUUID();
    const lineage = getRuntimeLineage({
      correlationId: input.requestId,
      eventName: input.eventName,
    });
    const payload = {
      eventId,
      eventName: input.eventName,
      occurredAt: occurredAt.toISOString(),
      tenantId: input.auth.tenantId,
      aggregateType: input.aggregateType,
      aggregateId: input.aggregateId,
      aggregateVersion: input.aggregateVersion,
      actor: { type: input.auth.actorType, id: input.auth.actorId },
      correlationId: input.requestId,
      causationId,
      lineage,
      data: input.data,
    };
    await transaction.execute(sql`
      INSERT INTO outbox_event (
        id,
        tenant_id,
        event_name,
        aggregate_type,
        aggregate_id,
        aggregate_version,
        occurred_at,
        actor_type,
        actor_id,
        correlation_id,
        causation_id,
        payload
      )
      VALUES (
        ${eventId},
        ${input.auth.tenantId},
        ${input.eventName},
        ${input.aggregateType},
        ${input.aggregateId},
        ${input.aggregateVersion},
        ${occurredAt},
        ${input.auth.actorType},
        ${input.auth.actorId},
        ${input.requestId},
        ${causationId},
        ${JSON.stringify(payload)}::jsonb
      )
    `);
  }
}

function documentExtension(
  mimeType: StudentDocument["mimeType"],
): ".pdf" | ".jpg" | ".png" {
  if (mimeType === "application/pdf") return ".pdf";
  if (mimeType === "image/jpeg") return ".jpg";
  return ".png";
}

function documentCategoryForExtractionType(
  documentType: StudentDocumentExtraction["documentType"],
): StudentDocument["category"] {
  return {
    transcript: "transcript",
    identity: "identity",
    financial_aid: "financial_aid",
    ferpa: "consent",
    immunization: "health",
    residency: "residency",
    other: "other",
  }[documentType] as StudentDocument["category"];
}

function documentProcessingExtraction(): StudentDocumentExtraction {
  return {
    status: "processing",
    documentType: "other",
    summary:
      "The original file is safely stored. Edward is preparing a reviewable record.",
    studentName: null,
    institutionName: null,
    issueDate: null,
    academicTerm: null,
    fields: [],
    courses: [],
    warnings: [],
    model: null,
    provider: "local",
    processedAt: null,
    verifiedAt: null,
  };
}

function mapProgram(row: {
  id: string;
  code: string;
  name: string;
  degree: string;
  total_credits: number;
  description: string;
  source_label?: string | null;
  source_url?: string | null;
  source_status?: "official_source" | "synthetic_preview" | "tenant_authored" | null;
}): AcademicProgram {
  return {
    id: row.id,
    code: row.code,
    name: row.name,
    degree: row.degree,
    totalCredits: row.total_credits,
    description: row.description,
    source: mapContentSource(row),
  };
}

function mapCatalogCourse(row: {
  id: string;
  code: string;
  title: string;
  description: string;
  credits: string | number;
  level: number;
  availability_label?: string | null;
  instructor_names?: string[] | null;
  meeting_pattern?: string | null;
  resources?: CatalogCourse["resources"] | null;
  source_label?: string | null;
  source_url?: string | null;
  source_status?: "official_source" | "synthetic_preview" | "tenant_authored" | null;
  prerequisites: CatalogCourse["prerequisites"];
}): CatalogCourse {
  return {
    id: row.id,
    code: row.code,
    title: row.title,
    description: row.description,
    credits: Number(row.credits),
    level: row.level,
    prerequisites: Array.isArray(row.prerequisites) ? row.prerequisites : [],
    availabilityLabel: row.availability_label ?? null,
    instructorNames: Array.isArray(row.instructor_names)
      ? row.instructor_names
      : [],
    meetingPattern: row.meeting_pattern ?? null,
    resources: Array.isArray(row.resources) ? row.resources : [],
    source: mapContentSource(row),
  };
}

function mapContentSource(row: {
  source_label?: string | null;
  source_url?: string | null;
  source_status?: "official_source" | "synthetic_preview" | "tenant_authored" | null;
}) {
  if (!row.source_label || !row.source_url || !row.source_status) {
    return null;
  }
  return {
    label: row.source_label,
    url: row.source_url,
    dataStatus: row.source_status,
  };
}
