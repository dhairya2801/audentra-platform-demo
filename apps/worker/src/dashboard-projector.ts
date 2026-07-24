import type { DatabaseClient, DatabasePool } from "./database.js";
import { inTransaction } from "./database.js";
import { getEventString } from "./event-parser.js";
import type { DomainEventEnvelope, Logger } from "./types.js";

interface DashboardHeaderRow {
  student_id: string;
  preferred_name: string | null;
  first_name: string;
  last_name: string;
  class_year: number;
  offer_id: string;
  program_name: string;
  term_name: string;
  campus_name: string;
  response_deadline: string | Date;
  deposit_amount_cents: number;
  offer_status: string;
  journey_id: string;
  journey_status: string;
}

interface RequirementRow {
  id: string;
  code: string;
  title: string;
  description: string;
  status: string;
  blocking: boolean | number;
  due_at: string | Date | null;
  progress_percent: number;
}

interface ProjectionVersionRow {
  projection_version: string | number;
}

const HEADER_SQL = `
  SELECT
    student.id AS student_id,
    person.preferred_name,
    person.first_name,
    person.last_name,
    student.class_year,
    admission_offer.id AS offer_id,
    program.name AS program_name,
    academic_term.name AS term_name,
    campus.name AS campus_name,
    admission_offer.response_deadline,
    admission_offer.deposit_amount_cents,
    admission_offer.status AS offer_status,
    enrollment_journey.id AS journey_id,
    enrollment_journey.status AS journey_status
  FROM public.student
  JOIN public.person
    ON person.id = student.person_id
   AND person.tenant_id = student.tenant_id
  JOIN public.admission_offer
    ON admission_offer.student_id = student.id
   AND admission_offer.tenant_id = student.tenant_id
  JOIN public.program
    ON program.id = admission_offer.program_id
   AND program.tenant_id = admission_offer.tenant_id
  JOIN public.academic_term
    ON academic_term.id = admission_offer.academic_term_id
   AND academic_term.tenant_id = admission_offer.tenant_id
  JOIN public.campus
    ON campus.id = admission_offer.campus_id
   AND campus.tenant_id = admission_offer.tenant_id
  JOIN public.enrollment_journey
    ON enrollment_journey.offer_id = admission_offer.id
   AND enrollment_journey.student_id = student.id
   AND enrollment_journey.tenant_id = student.tenant_id
  WHERE student.tenant_id = $1
    AND student.id = $2
    AND admission_offer.id = $3
    AND enrollment_journey.id = $4
  LIMIT 1
`;

const REQUIREMENTS_SQL = `
  SELECT
    student_requirement.id,
    definition.code,
    definition.title,
    definition.description,
    student_requirement.status,
    definition.blocking,
    student_requirement.due_at,
    student_requirement.progress_percent
  FROM public.student_requirement
  JOIN public.requirement_definition_version AS definition
    ON definition.id = student_requirement.requirement_definition_version_id
   AND definition.tenant_id = student_requirement.tenant_id
  WHERE student_requirement.tenant_id = $1
    AND student_requirement.journey_id = $2
  ORDER BY definition.display_order ASC, student_requirement.id ASC
`;

const terminalRequirementStatuses = new Set([
  "completed",
  "waived",
  "not_applicable",
]);

export class StudentDashboardProjector {
  public constructor(
    private readonly pool: DatabasePool,
    private readonly consumerName: string,
    private readonly logger: Logger,
  ) {}

  public async handle(event: DomainEventEnvelope): Promise<void> {
    const studentId = getEventString(event.data, "studentId", "student_id");
    const journeyId = getEventString(event.data, "journeyId", "journey_id");
    const offerId = getEventString(event.data, "offerId", "offer_id");

    if (!studentId || !journeyId || !offerId) {
      throw new Error(
        `${event.eventName} requires studentId, journeyId, and offerId`,
      );
    }

    const projected = await inTransaction(this.pool, async (client) => {
      const receipt = await client.query(
        `
          INSERT INTO public.projection_event_receipt (
            event_id,
            consumer_name,
            processed_at
          )
          VALUES ($1, $2, NOW())
          ON CONFLICT (event_id, consumer_name) DO NOTHING
          RETURNING event_id
        `,
        [event.eventId, this.consumerName],
      );

      if (receipt.rowCount === 0) {
        return null;
      }

      return this.rebuild(
        client,
        event.tenantId,
        studentId,
        offerId,
        journeyId,
      );
    });

    if (projected === null) {
      this.logger.info("projection_event_already_consumed", {
        eventId: event.eventId,
        eventName: event.eventName,
        consumerName: this.consumerName,
      });
      return;
    }

    this.logger.info("student_dashboard_projected", {
      eventId: event.eventId,
      tenantId: event.tenantId,
      studentId,
      journeyId,
      projectionVersion: projected,
    });
  }

  private async rebuild(
    client: DatabaseClient,
    tenantId: string,
    studentId: string,
    offerId: string,
    journeyId: string,
  ): Promise<number> {
    await client.query(
      "SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0))",
      [tenantId, studentId],
    );
    const existing = await client.query<ProjectionVersionRow>(
      `
        SELECT projection_version
        FROM public.student_portal_projection
        WHERE tenant_id = $1
          AND student_id = $2
        FOR UPDATE
      `,
      [tenantId, studentId],
    );
    const previousVersion = Number(existing.rows[0]?.projection_version ?? 0);
    if (!Number.isSafeInteger(previousVersion) || previousVersion < 0) {
      throw new Error("Existing projection_version is invalid");
    }
    const projectionVersion = previousVersion + 1;

    const [headerResult, requirementsResult] = await Promise.all([
      client.query<DashboardHeaderRow>(HEADER_SQL, [
        tenantId,
        studentId,
        offerId,
        journeyId,
      ]),
      client.query<RequirementRow>(REQUIREMENTS_SQL, [tenantId, journeyId]),
    ]);
    const header = headerResult.rows[0];
    if (!header) {
      throw new Error(
        `Cannot project dashboard: authoritative enrollment data is missing for student ${studentId}`,
      );
    }

    const requirements = requirementsResult.rows.map((requirement) => ({
      id: requirement.id,
      code: requirement.code,
      title: requirement.title,
      description: requirement.description,
      status: requirement.status,
      blocking: Boolean(requirement.blocking),
      dueAt: isoTimestampOrNull(requirement.due_at),
      progressPercent: Number(requirement.progress_percent),
    }));
    const firstBlockingRequirement = requirements.find(
      (requirement) =>
        requirement.blocking &&
        !terminalRequirementStatuses.has(requirement.status),
    );
    const completionPercent =
      requirements.length === 0
        ? 0
        : Math.round(
            requirements.reduce(
              (total, requirement) => total + requirement.progressPercent,
              0,
            ) / requirements.length,
          );
    const generatedAt = new Date().toISOString();
    const nextAction = firstBlockingRequirement
      ? {
          code: firstBlockingRequirement.code,
          label: firstBlockingRequirement.title,
          href: `/enrollment?requirement=${encodeURIComponent(firstBlockingRequirement.code)}`,
        }
      : {
          code: "review_enrollment",
          label: "Review your enrollment",
          href: "/enrollment",
        };
    const preferredName =
      header.preferred_name?.trim() || header.first_name.trim();
    const dashboard = {
      student: {
        id: header.student_id,
        preferredName,
        fullName: `${header.first_name} ${header.last_name}`.trim(),
        classYear: Number(header.class_year),
      },
      offer: {
        id: header.offer_id,
        programName: header.program_name,
        termName: header.term_name,
        campusName: header.campus_name,
        responseDeadline: isoDate(header.response_deadline),
        depositAmountCents: Number(header.deposit_amount_cents),
        status: header.offer_status,
      },
      journey: {
        id: header.journey_id,
        status: header.journey_status,
        completionPercent,
        nextAction,
        requirements,
      },
      unreadMessageCount: 0,
      projectionVersion,
      generatedAt,
    };

    await client.query(
      `
        INSERT INTO public.student_portal_projection (
          tenant_id,
          student_id,
          projection_version,
          dashboard,
          source_updated_at,
          projected_at
        )
        VALUES ($1, $2, $3, $4::jsonb, NOW(), NOW())
        ON CONFLICT (tenant_id, student_id)
        DO UPDATE SET
          projection_version = EXCLUDED.projection_version,
          dashboard = EXCLUDED.dashboard,
          source_updated_at = EXCLUDED.source_updated_at,
          projected_at = EXCLUDED.projected_at
      `,
      [tenantId, studentId, projectionVersion, JSON.stringify(dashboard)],
    );

    return projectionVersion;
  }
}

function isoDate(value: string | Date): string {
  if (value instanceof Date) {
    return value.toISOString().slice(0, 10);
  }
  const match = /^\d{4}-\d{2}-\d{2}/.exec(value);
  if (!match) {
    throw new Error(`Invalid response deadline: ${value}`);
  }
  return match[0];
}

function isoTimestampOrNull(value: string | Date | null): string | null {
  if (value === null) {
    return null;
  }
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`Invalid requirement due date: ${String(value)}`);
  }
  return date.toISOString();
}
