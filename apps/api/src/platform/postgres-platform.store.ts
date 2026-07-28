import { createHash, randomUUID } from "node:crypto";
import { Injectable } from "@nestjs/common";
import type {
  AcceptOfferResponse,
  ActivityEventInput,
  AdmissionOfferSummary,
  OfferStatus,
  RequirementStatus,
  StudentDashboard,
  StudentRequirementSummary,
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
import type {
  ActivityIngestionResult,
  AiProviderResponseAttempt,
  PlatformStore,
} from "./platform-store";
import { PostgresPortalStore } from "./postgres-portal.store";
import { getRuntimeLineage } from "../observability/runtime-lineage";

type RowResult<T> = { rows: T[] };

function resultRows<T>(result: unknown): T[] {
  return (result as RowResult<T>).rows;
}

function isoTimestamp(value: Date | string): string {
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("Database returned an invalid timestamp");
  }
  return parsed.toISOString();
}

interface DashboardBaseRow {
  student_id: string;
  preferred_name: string | null;
  first_name: string;
  last_name: string;
  class_year: number;
  offer_id: string;
  program_name: string;
  term_name: string;
  campus_name: string;
  response_deadline: string;
  deposit_amount_cents: number;
  offer_status: OfferStatus;
  offer_version: number;
  journey_id: string | null;
  journey_status: StudentDashboard["journey"]["status"] | null;
  projection_version: string | number | null;
  unread_message_count: string | number;
}

interface RequirementRow {
  id: string;
  code: string;
  title: string;
  description: string;
  status: RequirementStatus;
  blocking: number;
  due_at: Date | null;
  progress_percent: number;
}

interface OfferLockRow {
  id: string;
  status: OfferStatus;
  accepted_at: Date | null;
  version: number;
  is_expired: boolean;
}

interface JourneyRow {
  id: string;
  status: "in_progress";
  version: number;
}

interface IdempotencyRow {
  request_hash: string;
  response_body: AcceptOfferResponse;
}

interface DefinitionRow {
  journey_definition_version_id: string;
}

interface RequirementDefinitionRow {
  id: string;
  code: string;
  depends_on_codes: string[];
  due_offset_days: number | null;
}

const propertyAllowlists: Record<
  ActivityEventInput["eventName"],
  ReadonlySet<string>
> = {
  "ui.portal_session_started.v1": new Set(["entry_point"]),
  "ui.dashboard_viewed.v1": new Set([
    "projection_version",
    "journey_status",
  ]),
  "ui.admission_offer_viewed.v1": new Set(["offer_id", "offer_status"]),
  "ui.admission_decision_started.v1": new Set([
    "offer_id",
    "decision",
    "entry_point",
  ]),
  "ui.enrollment_started.v1": new Set(["journey_id", "entry_point"]),
  "ui.enrollment_step_viewed.v1": new Set(["step_code", "entry_point"]),
  "ui.portal_section_viewed.v1": new Set(["section", "entry_point"]),
  "ui.enrollment_task_viewed.v1": new Set([
    "task_code",
    "task_status",
    "entry_point",
  ]),
  "ui.enrollment_task_abandoned.v1": new Set([
    "task_code",
    "task_status",
    "duration_bucket",
    "last_interaction",
  ]),
  "ui.financial_aid_viewed.v1": new Set(["surface", "aid_status"]),
  "ui.course_catalog_searched.v1": new Set([
    "query_length_bucket",
    "result_count",
  ]),
  "ui.course_viewed.v1": new Set(["course_code", "surface"]),
  "ui.exemption_reviewed.v1": new Set([
    "rule_code",
    "recommendation_status",
  ]),
  "ui.campus_event_viewed.v1": new Set(["event_id", "surface"]),
  "ui.club_viewed.v1": new Set(["club_id", "surface"]),
  "ui.edward_context_receipts_received.v1": new Set([
    "source_count",
    "page_context",
  ]),
  "ui.edward_tool_invoked.v1": new Set(["tool_name", "page_context"]),
  "ui.edward_action_widget_viewed.v1": new Set([
    "widget_type",
    "page_context",
  ]),
  "ui.edward_action_completed.v1": new Set([
    "widget_type",
    "outcome",
  ]),
  "ui.help_opened.v1": new Set(["context", "surface", "topic_code"]),
};

const prohibitedPropertyPattern =
  /(password|token|secret|email|phone|address|government|payment|card|ssn)/i;

export function validateActivityEventProperties(
  event: ActivityEventInput,
): void {
  const allowlist = propertyAllowlists[event.eventName];
  for (const [key, value] of Object.entries(event.properties)) {
    if (prohibitedPropertyPattern.test(key)) {
      throw new BadRequestError(
        "PROHIBITED_ACTIVITY_PROPERTY",
        `Property "${key}" may not be captured`,
      );
    }
    if (!allowlist.has(key)) {
      throw new BadRequestError(
        "UNKNOWN_ACTIVITY_PROPERTY",
        `Property "${key}" is not allowed for ${event.eventName}`,
      );
    }
    if (
      value !== null &&
      typeof value !== "string" &&
      typeof value !== "number" &&
      typeof value !== "boolean"
    ) {
      throw new BadRequestError(
        "INVALID_ACTIVITY_PROPERTY",
        `Property "${key}" must be a primitive value`,
      );
    }
  }
}

@Injectable()
export class PostgresPlatformStore
  extends PostgresPortalStore
  implements PlatformStore
{
  constructor(database: DatabaseService) {
    super(database);
  }

  async recordAiProviderResponse(
    input: AiProviderResponseAttempt,
  ): Promise<void> {
    await this.database.db.execute(sql`
      INSERT INTO ai_provider_response_attempt (
        id,
        tenant_id,
        student_id,
        document_id,
        request_id,
        attempt_number,
        operation,
        provider,
        requested_model,
        response_model,
        provider_request_id,
        http_status,
        response_ok,
        finish_reason,
        usage,
        raw_response_text,
        response_body,
        transport_error,
        duration_ms,
        recorded_at,
        prompt_template_version_id,
        context_policy_version_id,
        output_schema_version_id,
        config_revision,
        context_sha256,
        prompt_cache_status
      )
      VALUES (
        ${input.id},
        ${input.tenantId},
        ${input.studentId},
        ${input.documentId},
        ${input.requestId},
        ${input.attempt},
        ${input.operation},
        ${input.provider},
        ${input.requestedModel},
        ${input.responseModel},
        ${input.providerRequestId},
        ${input.httpStatus},
        ${input.responseOk},
        ${input.finishReason},
        ${JSON.stringify(input.usage)}::jsonb,
        ${input.rawResponseText},
        ${JSON.stringify(input.responseBody ?? null)}::jsonb,
        ${JSON.stringify(input.transportError)}::jsonb,
        ${input.durationMs},
        ${new Date(input.recordedAt)},
        ${input.promptTemplateVersionId},
        ${input.contextPolicyVersionId},
        ${input.outputSchemaVersionId},
        ${input.configRevision},
        ${input.contextSha256},
        ${input.promptCacheStatus}
      )
      ON CONFLICT (
        tenant_id,
        document_id,
        request_id,
        attempt_number
      ) DO NOTHING
    `);
  }

  async getStudentDashboard(auth: AuthContext): Promise<StudentDashboard> {
    const baseResult = await this.database.db.execute(sql`
      SELECT
        s.id AS student_id,
        p.preferred_name,
        p.first_name,
        p.last_name,
        s.class_year,
        o.id AS offer_id,
        pr.name AS program_name,
        at.name AS term_name,
        c.name AS campus_name,
        o.response_deadline::text AS response_deadline,
        o.deposit_amount_cents,
        o.status AS offer_status,
        o.version AS offer_version,
        j.id AS journey_id,
        j.status AS journey_status,
        spp.projection_version,
        (
          SELECT COUNT(*)
          FROM student_message sm
          WHERE sm.tenant_id = s.tenant_id
            AND sm.student_id = s.id
            AND sm.read_at IS NULL
        ) AS unread_message_count
      FROM student s
      JOIN person p
        ON p.id = s.person_id
       AND p.tenant_id = s.tenant_id
      JOIN admission_offer o
        ON o.student_id = s.id
       AND o.tenant_id = s.tenant_id
      JOIN program pr
        ON pr.id = o.program_id
       AND pr.tenant_id = o.tenant_id
      JOIN academic_term at
        ON at.id = o.academic_term_id
       AND at.tenant_id = o.tenant_id
      JOIN campus c
        ON c.id = o.campus_id
       AND c.tenant_id = o.tenant_id
      LEFT JOIN enrollment_journey j
        ON j.offer_id = o.id
       AND j.tenant_id = o.tenant_id
      LEFT JOIN student_portal_projection spp
        ON spp.student_id = s.id
       AND spp.tenant_id = s.tenant_id
      WHERE s.tenant_id = ${auth.tenantId}
        AND s.id = ${auth.studentId}
      ORDER BY o.created_at DESC
      LIMIT 1
    `);
    const base = resultRows<DashboardBaseRow>(baseResult)[0];
    if (!base) {
      throw new NotFoundError(
        "STUDENT_DASHBOARD_NOT_FOUND",
        "No dashboard is available for this student",
      );
    }

    let requirements: StudentRequirementSummary[] = [];
    if (base.journey_id) {
      const requirementResult = await this.database.db.execute(sql`
        SELECT
          sr.id,
          rdv.code,
          rdv.title,
          rdv.description,
          sr.status,
          rdv.blocking,
          sr.due_at,
          sr.progress_percent
        FROM student_requirement sr
        JOIN requirement_definition_version rdv
          ON rdv.id = sr.requirement_definition_version_id
         AND rdv.tenant_id = sr.tenant_id
        WHERE sr.tenant_id = ${auth.tenantId}
          AND sr.journey_id = ${base.journey_id}
        ORDER BY rdv.display_order, sr.id
      `);
      requirements = resultRows<RequirementRow>(requirementResult).map(
        (requirement) => ({
          id: requirement.id,
          code: requirement.code,
          title: requirement.title,
          description: requirement.description,
          status: requirement.status,
          blocking: requirement.blocking === 1,
          dueAt:
            requirement.due_at === null
              ? null
              : isoTimestamp(requirement.due_at),
          progressPercent: requirement.progress_percent,
        }),
      );
    }

    const completionPercent =
      requirements.length === 0
        ? 0
        : Math.round(
            requirements.reduce(
              (total, requirement) => total + requirement.progressPercent,
              0,
            ) / requirements.length,
          );
    const nextRequirement =
      requirements.find((requirement) =>
        ["rejected", "ready", "in_progress"].includes(requirement.status),
      ) ??
      requirements.find(
        (requirement) =>
          !["completed", "waived", "not_applicable"].includes(
            requirement.status,
          ),
      );

    const offer: AdmissionOfferSummary = {
      id: base.offer_id,
      programName: base.program_name,
      termName: base.term_name,
      campusName: base.campus_name,
      responseDeadline: base.response_deadline,
      depositAmountCents: base.deposit_amount_cents,
      status: base.offer_status,
    };
    const storedProjectionVersion =
      base.projection_version === null
        ? 0
        : Number(base.projection_version);

    return {
      student: {
        id: base.student_id,
        preferredName: base.preferred_name ?? base.first_name,
        fullName: `${base.first_name} ${base.last_name}`,
        classYear: base.class_year,
      },
      offer,
      journey: {
        id: base.journey_id,
        status: base.journey_status ?? "not_started",
        completionPercent,
        nextAction:
          base.journey_id && nextRequirement
            ? {
                code: nextRequirement.code,
                label: nextRequirement.title,
                href: `/enrollment?requirement=${encodeURIComponent(nextRequirement.code)}`,
              }
            : base.journey_id
              ? {
                  code: "review_enrollment",
                  label: "Review your enrollment",
                  href: "/enrollment",
                }
              : {
                  code: "accept_offer",
                  label: "Review and accept your offer",
                  href: "/offer",
                },
        requirements,
      },
      unreadMessageCount: Number(base.unread_message_count),
      projectionVersion: Math.max(
        storedProjectionVersion,
        base.offer_version,
      ),
      generatedAt: new Date().toISOString(),
    };
  }

  async acceptAdmissionOffer(input: {
    auth: AuthContext;
    offerId: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<AcceptOfferResponse> {
    const operation = "admission_offer.accept";
    const requestHash = createHash("sha256")
      .update(
        JSON.stringify({
          tenantId: input.auth.tenantId,
          studentId: input.auth.studentId,
          offerId: input.offerId,
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
      const existing = resultRows<IdempotencyRow>(existingResult)[0];
      if (existing) {
        if (existing.request_hash !== requestHash) {
          throw new ConflictError(
            "IDEMPOTENCY_KEY_REUSED",
            "This idempotency key was already used for a different request",
          );
        }
        return existing.response_body;
      }

      const offerResult = await transaction.execute(sql`
        SELECT
          id,
          status,
          accepted_at,
          version,
          response_deadline < CURRENT_DATE AS is_expired
        FROM admission_offer
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.auth.studentId}
          AND id = ${input.offerId}
        FOR UPDATE
      `);
      const offer = resultRows<OfferLockRow>(offerResult)[0];
      if (!offer) {
        throw new NotFoundError(
          "ADMISSION_OFFER_NOT_FOUND",
          "The admission offer was not found",
        );
      }

      if (offer.status === "accepted") {
        const journeyResult = await transaction.execute(sql`
          SELECT id, status, version
          FROM enrollment_journey
          WHERE tenant_id = ${input.auth.tenantId}
            AND offer_id = ${input.offerId}
        `);
        const journey = resultRows<JourneyRow>(journeyResult)[0];
        if (!journey || !offer.accepted_at) {
          throw new ApiError(
            500,
            "INCONSISTENT_OFFER_STATE",
            "The accepted offer has no enrollment journey",
          );
        }
        const response: AcceptOfferResponse = {
          offerId: offer.id,
          offerStatus: "accepted",
          journeyId: journey.id,
          journeyStatus: "in_progress",
          projectionVersion: Math.max(offer.version, 2),
          acceptedAt: isoTimestamp(offer.accepted_at),
        };
        await this.storeIdempotentResponse(
          transaction,
          input.auth,
          operation,
          input.idempotencyKey,
          requestHash,
          response,
        );
        return response;
      }

      if (offer.status !== "offered" || offer.is_expired) {
        throw new ConflictError(
          "ADMISSION_OFFER_NOT_ACTIVE",
          "Only an active admission offer can be accepted",
        );
      }

      const definitionResult = await transaction.execute(sql`
        SELECT id AS journey_definition_version_id
        FROM journey_definition_version
        WHERE tenant_id = ${input.auth.tenantId}
          AND active = 1
        ORDER BY version DESC
        LIMIT 1
      `);
      const definition = resultRows<DefinitionRow>(definitionResult)[0];
      if (!definition) {
        throw new ApiError(
          500,
          "WORKFLOW_CONFIGURATION_MISSING",
          "No active enrollment journey definition is configured",
        );
      }

      const acceptedAt = new Date();
      const updateResult = await transaction.execute(sql`
        UPDATE admission_offer
        SET status = 'accepted',
            accepted_at = ${acceptedAt},
            version = version + 1,
            updated_at = ${acceptedAt}
        WHERE id = ${input.offerId}
          AND tenant_id = ${input.auth.tenantId}
        RETURNING version
      `);
      const offerVersion = resultRows<{ version: number }>(updateResult)[0]
        ?.version;
      if (!offerVersion) {
        throw new ApiError(
          500,
          "OFFER_UPDATE_FAILED",
          "The admission offer could not be updated",
        );
      }

      const journeyId = randomUUID();
      await transaction.execute(sql`
        INSERT INTO enrollment_journey (
          id,
          tenant_id,
          student_id,
          offer_id,
          journey_definition_version_id,
          status,
          version,
          created_at,
          updated_at
        )
        VALUES (
          ${journeyId},
          ${input.auth.tenantId},
          ${input.auth.studentId},
          ${input.offerId},
          ${definition.journey_definition_version_id},
          'in_progress',
          1,
          ${acceptedAt},
          ${acceptedAt}
        )
      `);

      const requirementDefinitionsResult = await transaction.execute(sql`
        SELECT
          rdv.id,
          rdv.code,
          rdv.depends_on_codes,
          rdv.due_offset_days
        FROM journey_requirement_definition jrd
        JOIN requirement_definition_version rdv
          ON rdv.id = jrd.requirement_definition_version_id
        WHERE jrd.journey_definition_version_id =
          ${definition.journey_definition_version_id}
          AND rdv.tenant_id = ${input.auth.tenantId}
        ORDER BY rdv.display_order
      `);
      const requirementDefinitions =
        resultRows<RequirementDefinitionRow>(requirementDefinitionsResult);
      const requirementIds: string[] = [];
      for (const definitionRow of requirementDefinitions) {
        const requirementId = randomUUID();
        requirementIds.push(requirementId);
        const dueAt =
          definitionRow.due_offset_days === null
            ? null
            : new Date(
                acceptedAt.getTime() +
                  definitionRow.due_offset_days * 86_400_000,
              );
        const initialStatus =
          definitionRow.depends_on_codes.length === 0 ? "ready" : "blocked";
        await transaction.execute(sql`
          INSERT INTO student_requirement (
            id,
            tenant_id,
            journey_id,
            requirement_definition_version_id,
            status,
            due_at,
            progress_percent,
            version,
            created_at,
            updated_at
          )
          VALUES (
            ${requirementId},
            ${input.auth.tenantId},
            ${journeyId},
            ${definitionRow.id},
            ${initialStatus},
            ${dueAt},
            0,
            1,
            ${acceptedAt},
            ${acceptedAt}
          )
        `);
      }

      const auditId = randomUUID();
      const auditLineage = getRuntimeLineage({
        correlationId: input.requestId,
        auditAction: "admission_offer.accepted",
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
          metadata,
          occurred_at,
          created_at
        )
        VALUES (
          ${auditId},
          ${input.auth.tenantId},
          ${input.auth.actorType},
          ${input.auth.actorId},
          ${input.auth.studentId},
          'admission_offer.accepted',
          'admission_offer',
          ${input.offerId},
          'student_self_service',
          ${input.requestId},
          ${input.requestId},
          ${JSON.stringify({
            changedFields: ["status", "accepted_at"],
            lineage: auditLineage,
          })}::jsonb,
          ${acceptedAt},
          ${acceptedAt}
        )
      `);

      const causationId = randomUUID();
      await this.insertOutboxEvent(transaction, {
        eventName: "admission.offer_accepted.v1",
        aggregateType: "admission_offer",
        aggregateId: input.offerId,
        aggregateVersion: offerVersion,
        auth: input.auth,
        occurredAt: acceptedAt,
        correlationId: input.requestId,
        causationId,
        data: {
          offerId: input.offerId,
          studentId: input.auth.studentId,
          journeyId,
          acceptedAt: acceptedAt.toISOString(),
        },
      });
      await this.insertOutboxEvent(transaction, {
        eventName: "enrollment.journey_created.v1",
        aggregateType: "enrollment_journey",
        aggregateId: journeyId,
        aggregateVersion: 1,
        auth: input.auth,
        occurredAt: acceptedAt,
        correlationId: input.requestId,
        causationId,
        data: {
          journeyId,
          studentId: input.auth.studentId,
          offerId: input.offerId,
          requirementIds,
        },
      });

      const response: AcceptOfferResponse = {
        offerId: input.offerId,
        offerStatus: "accepted",
        journeyId,
        journeyStatus: "in_progress",
        projectionVersion: offerVersion,
        acceptedAt: acceptedAt.toISOString(),
      };
      await this.storeIdempotentResponse(
        transaction,
        input.auth,
        operation,
        input.idempotencyKey,
        requestHash,
        response,
      );
      return response;
    });
  }

  async ingestActivityEvents(input: {
    auth: AuthContext;
    events: ActivityEventInput[];
    requestId: string;
  }): Promise<ActivityIngestionResult> {
    for (const event of input.events) validateActivityEventProperties(event);

    return this.database.db.transaction(async (transaction) => {
      let accepted = 0;
      for (const event of input.events) {
        const insertResult = await transaction.execute(sql`
          INSERT INTO activity_event (
            tenant_id,
            event_id,
            event_name,
            occurred_at,
            received_at,
            actor_type,
            actor_id,
            student_id,
            session_id,
            page_instance_id,
            correlation_id,
            trust_level,
            application_version,
            properties
          )
          VALUES (
            ${input.auth.tenantId},
            ${event.eventId},
            ${event.eventName},
            ${new Date(event.occurredAt)},
            NOW(),
            ${input.auth.actorType},
            ${input.auth.actorId},
            ${input.auth.studentId},
            ${event.sessionId},
            ${event.pageInstanceId},
            ${event.correlationId ?? input.requestId},
            'client_signal',
            '0.1.0',
            ${JSON.stringify(event.properties)}::jsonb
          )
          ON CONFLICT (tenant_id, event_id) DO NOTHING
          RETURNING event_id
        `);
        if (resultRows<{ event_id: string }>(insertResult).length === 1) {
          accepted += 1;
        }
      }
      return {
        accepted,
        duplicates: input.events.length - accepted,
      };
    });
  }

  private async storeIdempotentResponse(
    transaction: Parameters<
      Parameters<DatabaseService["db"]["transaction"]>[0]
    >[0],
    auth: AuthContext,
    operation: string,
    idempotencyKey: string,
    requestHash: string,
    response: AcceptOfferResponse,
  ): Promise<void> {
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
        ${auth.tenantId},
        ${auth.actorId},
        ${operation},
        ${idempotencyKey},
        ${requestHash},
        200,
        ${JSON.stringify(response)}::jsonb,
        NOW(),
        NOW() + INTERVAL '24 hours'
      )
    `);
  }

  private async insertOutboxEvent(
    transaction: Parameters<
      Parameters<DatabaseService["db"]["transaction"]>[0]
    >[0],
    input: {
      eventName: string;
      aggregateType: string;
      aggregateId: string;
      aggregateVersion: number;
      auth: AuthContext;
      occurredAt: Date;
      correlationId: string;
      causationId: string;
      data: Record<string, unknown>;
    },
  ): Promise<void> {
    const eventId = randomUUID();
    const lineage = getRuntimeLineage({
      correlationId: input.correlationId,
      eventName: input.eventName,
    });
    const envelope = {
      eventId,
      eventName: input.eventName,
      occurredAt: input.occurredAt.toISOString(),
      tenantId: input.auth.tenantId,
      aggregateType: input.aggregateType,
      aggregateId: input.aggregateId,
      aggregateVersion: input.aggregateVersion,
      actor: {
        type: input.auth.actorType,
        id: input.auth.actorId,
      },
      correlationId: input.correlationId,
      causationId: input.causationId,
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
        payload,
        created_at
      )
      VALUES (
        ${eventId},
        ${input.auth.tenantId},
        ${input.eventName},
        ${input.aggregateType},
        ${input.aggregateId},
        ${input.aggregateVersion},
        ${input.occurredAt},
        ${input.auth.actorType},
        ${input.auth.actorId},
        ${input.correlationId},
        ${input.causationId},
        ${JSON.stringify(envelope)}::jsonb,
        ${input.occurredAt}
      )
    `);
  }
}
