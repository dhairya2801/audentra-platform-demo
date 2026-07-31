import { randomUUID } from "node:crypto";
import {
  type ReviewStaffDocumentInput,
  type StaffActionCenter,
  type StaffDocumentDecisionResult,
  type StaffStudentRecord,
  type StaffWorkItem,
  type StaffWorkItemLog,
  type StudentMessage,
  type StudentOnboardingData,
  type UpdateStaffStudentPreferencesInput,
  type UpdateStaffWorkItemInput,
} from "@vv/contracts";
import { Inject, Injectable } from "@nestjs/common";
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
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";

type Transaction = Parameters<
  Parameters<DatabaseService["db"]["transaction"]>[0]
>[0];
type RowResult<T> = { rows: T[] };

function rows<T>(result: unknown): T[] {
  return (result as RowResult<T>).rows;
}

function isoTimestamp(value: Date | string): string {
  const timestamp = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    throw new Error("Database returned an invalid timestamp");
  }
  return timestamp.toISOString();
}

interface StaffMemberRow {
  id: string;
  display_name: string;
  email_normalized: string;
  component: string;
}

interface WorkItemRow {
  id: string;
  key: string;
  student_id: string;
  title: string;
  description: string;
  status: StaffWorkItem["status"];
  priority: StaffWorkItem["priority"];
  work_type: StaffWorkItem["type"];
  component: string;
  due_at: Date | null;
  escalated: boolean;
  version: number;
  created_at: Date;
  updated_at: Date;
  assignee_id: string | null;
  assignee_name: string | null;
  assignee_email: string | null;
  assignee_component: string | null;
  first_name: string;
  last_name: string;
  preferred_name: string;
  program_name: string;
  class_year: number;
  source_type: "onboarding" | "requirement" | "document" | "message" | null;
  source_id: string | null;
}

interface WorkLogRow {
  id: string;
  work_item_id: string;
  action: StaffWorkItemLog["action"];
  message: string;
  actor_name: string;
  occurred_at: Date;
}

interface LockedWorkItemRow {
  id: string;
  student_id: string;
  status: StaffWorkItem["status"];
  assignee_id: string | null;
  escalated: boolean;
  version: number;
  source_type: string | null;
  source_id: string | null;
}

function memberFromRow(row: StaffMemberRow) {
  return {
    id: row.id,
    name: row.display_name,
    email: row.email_normalized,
    component: row.component,
  };
}

@Injectable()
export class PostgresStaffActionStore {
  constructor(
    private readonly database: DatabaseService,
    @Inject(PLATFORM_STORE) private readonly platform: PlatformStore,
  ) {}

  async getActionCenter(auth: AuthContext): Promise<StaffActionCenter> {
    this.requireStaff(auth);
    await this.ensureDocumentWorkItems(auth);
    const [memberResult, itemResult, logResult] = await Promise.all([
      this.database.db.execute(sql`
        SELECT id, display_name, email_normalized, component
        FROM staff_member
        WHERE tenant_id = ${auth.tenantId}
          AND active = true
        ORDER BY display_name, id
      `),
      this.database.db.execute(sql`
        SELECT
          item.id,
          item.key,
          item.student_id,
          item.title,
          item.description,
          item.status,
          item.priority,
          item.work_type,
          item.component,
          item.due_at,
          item.escalated,
          item.version,
          item.created_at,
          item.updated_at,
          item.assignee_id,
          assignee.display_name AS assignee_name,
          assignee.email_normalized AS assignee_email,
          assignee.component AS assignee_component,
          person.first_name,
          person.last_name,
          COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
            AS preferred_name,
          COALESCE(offer_program.name, 'Program not assigned') AS program_name,
          student.class_year,
          item.source_type,
          item.source_id
        FROM staff_work_item item
        JOIN student
          ON student.id = item.student_id
         AND student.tenant_id = item.tenant_id
        JOIN person
          ON person.id = student.person_id
         AND person.tenant_id = student.tenant_id
        LEFT JOIN student_profile profile
          ON profile.student_id = student.id
         AND profile.tenant_id = student.tenant_id
        LEFT JOIN staff_member assignee
          ON assignee.id = item.assignee_id
         AND assignee.tenant_id = item.tenant_id
        LEFT JOIN LATERAL (
          SELECT program.name
          FROM admission_offer
          JOIN program
            ON program.id = admission_offer.program_id
           AND program.tenant_id = admission_offer.tenant_id
          WHERE admission_offer.tenant_id = item.tenant_id
            AND admission_offer.student_id = item.student_id
          ORDER BY admission_offer.created_at DESC
          LIMIT 1
        ) offer_program ON true
        WHERE item.tenant_id = ${auth.tenantId}
        ORDER BY
          CASE item.priority
            WHEN 'urgent' THEN 1
            WHEN 'high' THEN 2
            WHEN 'medium' THEN 3
            ELSE 4
          END,
          item.due_at NULLS LAST,
          item.updated_at DESC,
          item.id
      `),
      this.database.db.execute(sql`
        SELECT
          id,
          work_item_id,
          action,
          message,
          actor_name,
          occurred_at
        FROM staff_work_log
        WHERE tenant_id = ${auth.tenantId}
        ORDER BY occurred_at DESC, id
      `),
    ]);
    const members = rows<StaffMemberRow>(memberResult).map(memberFromRow);
    const logs = rows<WorkLogRow>(logResult);
    const items = rows<WorkItemRow>(itemResult).map((item) =>
      this.mapWorkItem(item, logs),
    );
    return {
      items,
      staff: members,
      counts: {
        todo: items.filter((item) => item.status === "todo").length,
        inProgress: items.filter((item) => item.status === "in_progress")
          .length,
        done: items.filter((item) => item.status === "done").length,
        urgent: items.filter((item) => item.priority === "urgent").length,
        escalated: items.filter((item) => item.escalated).length,
      },
      generatedAt: new Date().toISOString(),
    };
  }

  async getStudentRecord(
    auth: AuthContext,
    studentId: string,
  ): Promise<StaffStudentRecord> {
    this.requireStaff(auth);
    const studentAuth = { ...auth, studentId };
    const [onboarding, profile, requirements, documents, summaryResult] =
      await Promise.all([
        this.platform.getStudentOnboarding(studentAuth),
        this.platform.getStudentProfile(studentAuth),
        this.platform.getStudentRequirements(studentAuth),
        this.platform.getStudentDocuments(studentAuth),
        this.database.db.execute(sql`
          SELECT
            student.id,
            person.first_name,
            person.last_name,
            COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
              AS preferred_name,
            COALESCE(offer_program.name, 'Program not assigned') AS program_name,
            student.class_year
          FROM student
          JOIN person
            ON person.id = student.person_id
           AND person.tenant_id = student.tenant_id
          LEFT JOIN student_profile profile
            ON profile.student_id = student.id
           AND profile.tenant_id = student.tenant_id
          LEFT JOIN LATERAL (
            SELECT program.name
            FROM admission_offer
            JOIN program
              ON program.id = admission_offer.program_id
             AND program.tenant_id = admission_offer.tenant_id
            WHERE admission_offer.tenant_id = student.tenant_id
              AND admission_offer.student_id = student.id
            ORDER BY admission_offer.created_at DESC
            LIMIT 1
          ) offer_program ON true
          WHERE student.tenant_id = ${auth.tenantId}
            AND student.id = ${studentId}
        `),
      ]);
    const summary = rows<{
      id: string;
      first_name: string;
      last_name: string;
      preferred_name: string;
      program_name: string;
      class_year: number;
    }>(summaryResult)[0];
    if (!summary) {
      throw new NotFoundError(
        "STAFF_STUDENT_NOT_FOUND",
        "The student was not found",
      );
    }
    return {
      student: {
        id: summary.id,
        name: `${summary.first_name} ${summary.last_name}`,
        preferredName: summary.preferred_name,
        programName: summary.program_name,
        classYear: summary.class_year,
      },
      onboarding,
      profile,
      requirements,
      documents,
    };
  }

  async updateWorkItem(input: {
    auth: AuthContext;
    workItemId: string;
    update: UpdateStaffWorkItemInput;
    requestId: string;
  }): Promise<StaffWorkItem> {
    this.requireStaff(input.auth);
    await this.database.db.transaction(async (transaction) => {
      const current = await this.lockWorkItem(
        transaction,
        input.auth,
        input.workItemId,
      );
      if (current.version !== input.update.expectedVersion) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "This work item changed in another staff session",
        );
      }
      const nextStatus = input.update.status ?? current.status;
      const nextAssignee =
        input.update.assigneeId === undefined
          ? current.assignee_id
          : input.update.assigneeId;
      const nextEscalated =
        input.update.escalated ?? current.escalated;
      if (nextAssignee) {
        const assigneeResult = await transaction.execute(sql`
          SELECT id
          FROM staff_member
          WHERE tenant_id = ${input.auth.tenantId}
            AND id = ${nextAssignee}
            AND active = true
        `);
        if (rows<{ id: string }>(assigneeResult).length === 0) {
          throw new NotFoundError(
            "STAFF_MEMBER_NOT_FOUND",
            "The assignee was not found",
          );
        }
      }
      const changes: Array<{
        action: StaffWorkItemLog["action"];
        message: string;
      }> = [];
      if (nextStatus !== current.status) {
        changes.push({
          action: "status_changed",
          message: `Moved from ${current.status.replaceAll("_", " ")} to ${nextStatus.replaceAll("_", " ")}.`,
        });
      }
      if (nextAssignee !== current.assignee_id) {
        const assigneeName = nextAssignee
          ? await this.staffName(transaction, input.auth, nextAssignee)
          : null;
        changes.push({
          action: "assigned",
          message: assigneeName
            ? `Assigned to ${assigneeName}.`
            : "Removed the assignee.",
        });
      }
      if (nextEscalated !== current.escalated) {
        changes.push({
          action: "escalated",
          message: nextEscalated
            ? "Marked as escalated."
            : "Cleared the escalation flag.",
        });
      }
      if (input.update.note?.trim()) {
        changes.push({
          action: "commented",
          message: input.update.note.trim(),
        });
      }
      if (changes.length === 0) {
        throw new BadRequestError(
          "STAFF_WORK_ITEM_NO_CHANGES",
          "Choose a status, assignee, escalation state, or note to update",
        );
      }
      const updatedResult = await transaction.execute(sql`
        UPDATE staff_work_item
        SET status = ${nextStatus},
            assignee_id = ${nextAssignee},
            escalated = ${nextEscalated},
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND id = ${input.workItemId}
          AND version = ${input.update.expectedVersion}
        RETURNING version
      `);
      const version = rows<{ version: number }>(updatedResult)[0]?.version;
      if (!version) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "This work item changed in another staff session",
        );
      }
      const actorName = await this.staffName(
        transaction,
        input.auth,
        input.auth.actorId,
      );
      for (const change of changes) {
        await this.insertWorkLog(transaction, {
          auth: input.auth,
          workItemId: input.workItemId,
          actorName,
          ...change,
        });
      }
      await this.insertAudit(transaction, {
        auth: input.auth,
        requestId: input.requestId,
        action: "staff_work_item.updated",
        resourceType: "staff_work_item",
        resourceId: input.workItemId,
        metadata: { version, changes: changes.map((change) => change.action) },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        requestId: input.requestId,
        eventName: "staff.work_item_updated.v1",
        aggregateType: "staff_work_item",
        aggregateId: input.workItemId,
        aggregateVersion: version,
        data: {
          workItemId: input.workItemId,
          studentId: current.student_id,
          status: nextStatus,
          assigneeId: nextAssignee,
          escalated: nextEscalated,
        },
      });
    });
    return this.requireWorkItem(input.auth, input.workItemId);
  }

  async updateStudentPreferences(input: {
    auth: AuthContext;
    studentId: string;
    update: UpdateStaffStudentPreferencesInput;
    requestId: string;
  }): Promise<StaffStudentRecord> {
    this.requireStaff(input.auth);
    await this.database.db.transaction(async (transaction) => {
      const [onboardingResult, profileResult] = await Promise.all([
        transaction.execute(sql`
          SELECT payload, version
          FROM student_onboarding
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.studentId}
          FOR UPDATE
        `),
        transaction.execute(sql`
          SELECT version
          FROM student_profile
          WHERE tenant_id = ${input.auth.tenantId}
            AND student_id = ${input.studentId}
          FOR UPDATE
        `),
      ]);
      const onboarding = rows<{
        payload: StudentOnboardingData;
        version: number;
      }>(onboardingResult)[0];
      const profile = rows<{ version: number }>(profileResult)[0];
      if (!onboarding || !profile) {
        throw new NotFoundError(
          "STAFF_STUDENT_NOT_FOUND",
          "The student was not found",
        );
      }
      if (
        onboarding.version !== input.update.expectedOnboardingVersion ||
        profile.version !== input.update.expectedProfileVersion
      ) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "The student record changed in another session",
        );
      }
      const payload: StudentOnboardingData = {
        ...onboarding.payload,
        communicationPreference: input.update.communicationPreference,
        housingPreference: input.update.housingPreference,
        accommodationInterest: input.update.accommodationInterest,
        residencyVerificationPath: input.update.residencyVerificationPath,
      };
      if (input.update.housingPreference !== "on_campus") {
        delete payload.housingResidenceOption;
        delete payload.housingResidencePreferences;
      }
      const onboardingUpdate = await transaction.execute(sql`
        UPDATE student_onboarding
        SET payload = ${JSON.stringify(payload)}::jsonb,
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.studentId}
          AND version = ${input.update.expectedOnboardingVersion}
        RETURNING version
      `);
      const onboardingVersion = rows<{ version: number }>(onboardingUpdate)[0]
        ?.version;
      const profileUpdate = await transaction.execute(sql`
        UPDATE student_profile
        SET communication_preference = ${input.update.communicationPreference},
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.studentId}
          AND version = ${input.update.expectedProfileVersion}
        RETURNING version
      `);
      const profileVersion = rows<{ version: number }>(profileUpdate)[0]
        ?.version;
      if (!onboardingVersion || !profileVersion) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "The student record changed in another session",
        );
      }
      const actorName = await this.staffName(
        transaction,
        input.auth,
        input.auth.actorId,
      );
      const onboardingItemResult = await transaction.execute(sql`
        UPDATE staff_work_item
        SET version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND student_id = ${input.studentId}
          AND source_type = 'onboarding'
        RETURNING id
      `);
      for (const item of rows<{ id: string }>(onboardingItemResult)) {
        await this.insertWorkLog(transaction, {
          auth: input.auth,
          workItemId: item.id,
          actorName,
          action: "student_preferences_updated",
          message:
            input.update.note?.trim() ??
            "Updated the student's operational onboarding preferences.",
        });
      }
      if (input.update.notifyStudent) {
        await this.insertStudentMessage(transaction, {
          auth: input.auth,
          studentId: input.studentId,
          subject: "Your enrollment preferences were updated",
          body:
            input.update.note?.trim() ??
            "Your enrollment team updated your communication, housing, and support follow-up preferences. Review your enrollment page for the latest details.",
        });
      }
      await this.insertAudit(transaction, {
        auth: input.auth,
        requestId: input.requestId,
        action: "student_preferences.updated_by_staff",
        resourceType: "student_onboarding",
        resourceId: input.studentId,
        metadata: {
          onboardingVersion,
          profileVersion,
          notifiedStudent: input.update.notifyStudent,
        },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        requestId: input.requestId,
        eventName: "student.preferences_updated_by_staff.v1",
        aggregateType: "student_onboarding",
        aggregateId: input.studentId,
        aggregateVersion: onboardingVersion,
        data: {
          studentId: input.studentId,
          communicationPreference: input.update.communicationPreference,
          housingPreference: input.update.housingPreference,
          notifiedStudent: input.update.notifyStudent,
        },
      });
    });
    return this.getStudentRecord(input.auth, input.studentId);
  }

  async reviewDocument(input: {
    auth: AuthContext;
    documentId: string;
    review: ReviewStaffDocumentInput;
    requestId: string;
  }): Promise<StaffDocumentDecisionResult> {
    this.requireStaff(input.auth);
    let notification: StudentMessage | null = null;
    await this.database.db.transaction(async (transaction) => {
      const workItem = await this.lockWorkItem(
        transaction,
        input.auth,
        input.review.workItemId,
      );
      if (
        workItem.source_type !== "document" ||
        workItem.source_id !== input.documentId
      ) {
        throw new NotFoundError(
          "STAFF_WORK_ITEM_NOT_FOUND",
          "The document review work item was not found",
        );
      }
      if (workItem.version !== input.review.expectedWorkItemVersion) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "This document review changed in another staff session",
        );
      }
      const documentResult = await transaction.execute(sql`
        SELECT id, student_id, requirement_id, file_name, status
        FROM document_record
        WHERE tenant_id = ${input.auth.tenantId}
          AND id = ${input.documentId}
        FOR UPDATE
      `);
      const document = rows<{
        id: string;
        student_id: string;
        requirement_id: string | null;
        file_name: string;
        status: string;
      }>(documentResult)[0];
      if (!document) {
        throw new NotFoundError(
          "STAFF_DOCUMENT_NOT_FOUND",
          "The document was not found",
        );
      }
      if (!["needs_review", "under_review"].includes(document.status)) {
        throw new ConflictError(
          "DOCUMENT_REVIEW_ALREADY_DECIDED",
          "This document already has an official staff decision",
        );
      }
      await transaction.execute(sql`
        UPDATE document_record
        SET status = ${input.review.decision},
            updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND id = ${input.documentId}
      `);
      if (document.requirement_id) {
        const requirementStatus =
          input.review.decision === "accepted" ? "completed" : "rejected";
        const progress =
          input.review.decision === "accepted" ? 100 : 60;
        await transaction.execute(sql`
          UPDATE student_requirement
          SET status = ${requirementStatus},
              progress_percent = ${progress},
              version = version + 1,
              updated_at = NOW()
          WHERE tenant_id = ${input.auth.tenantId}
            AND id = ${document.requirement_id}
        `);
        if (input.review.decision === "accepted") {
          await this.refreshRequirementDependencies(
            transaction,
            input.auth,
            document.requirement_id,
          );
        }
      }
      const itemUpdate = await transaction.execute(sql`
        UPDATE staff_work_item
        SET status = 'done',
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = ${input.auth.tenantId}
          AND id = ${input.review.workItemId}
          AND version = ${input.review.expectedWorkItemVersion}
        RETURNING version
      `);
      const itemVersion = rows<{ version: number }>(itemUpdate)[0]?.version;
      if (!itemVersion) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "This document review changed in another staff session",
        );
      }
      const actorName = await this.staffName(
        transaction,
        input.auth,
        input.auth.actorId,
      );
      await this.insertWorkLog(transaction, {
        auth: input.auth,
        workItemId: input.review.workItemId,
        actorName,
        action: "document_decided",
        message: `${
          input.review.decision === "accepted"
            ? "Accepted"
            : "Requested changes to"
        } ${document.file_name}: ${input.review.note.trim()}`,
      });
      if (input.review.notifyStudent) {
        notification = await this.insertStudentMessage(transaction, {
          auth: input.auth,
          studentId: document.student_id,
          subject:
            input.review.decision === "accepted"
              ? `${document.file_name} was accepted`
              : `${document.file_name} needs changes`,
          body: input.review.note.trim(),
        });
      }
      await this.insertAudit(transaction, {
        auth: input.auth,
        requestId: input.requestId,
        action: "student_document.decided_by_staff",
        resourceType: "document_record",
        resourceId: input.documentId,
        metadata: {
          decision: input.review.decision,
          workItemId: input.review.workItemId,
          notifiedStudent: input.review.notifyStudent,
        },
      });
      await this.insertOutbox(transaction, {
        auth: input.auth,
        requestId: input.requestId,
        eventName: "student.document_decided_by_staff.v1",
        aggregateType: "document_record",
        aggregateId: input.documentId,
        aggregateVersion: itemVersion,
        data: {
          documentId: input.documentId,
          studentId: document.student_id,
          decision: input.review.decision,
          workItemId: input.review.workItemId,
          notifiedStudent: input.review.notifyStudent,
        },
      });
    });
    const studentAuth = {
      ...input.auth,
      studentId: (
        await this.requireWorkItem(input.auth, input.review.workItemId)
      ).student.id,
    };
    const [documents, workItem] = await Promise.all([
      this.platform.getStudentDocuments(studentAuth),
      this.requireWorkItem(input.auth, input.review.workItemId),
    ]);
    const document = documents.items.find(
      (candidate) => candidate.id === input.documentId,
    );
    if (!document) {
      throw new ApiError(
        500,
        "STAFF_DOCUMENT_DECISION_INCONSISTENT",
        "The document decision committed but the document could not be reloaded",
      );
    }
    return { document, workItem, notification };
  }

  private requireStaff(auth: AuthContext): void {
    if (auth.actorType !== "staff") {
      throw new ApiError(
        403,
        "STAFF_ACCESS_REQUIRED",
        "This route requires a staff identity",
      );
    }
  }

  private async ensureDocumentWorkItems(auth: AuthContext): Promise<void> {
    const pendingResult = await this.database.db.execute(sql`
      SELECT id, student_id, file_name, category
      FROM document_record
      WHERE tenant_id = ${auth.tenantId}
        AND status IN ('needs_review', 'under_review')
        AND NOT EXISTS (
          SELECT 1
          FROM staff_work_item
          WHERE staff_work_item.tenant_id = document_record.tenant_id
            AND staff_work_item.source_type = 'document'
            AND staff_work_item.source_id = document_record.id
        )
      ORDER BY created_at, id
    `);
    for (const document of rows<{
      id: string;
      student_id: string;
      file_name: string;
      category: string;
    }>(pendingResult)) {
      await this.database.db.transaction(async (transaction) => {
        const component =
          document.category === "financial_aid"
            ? "Financial Aid"
            : document.category === "health"
              ? "Student Health"
              : "Registrar";
        const assigneeResult = await transaction.execute(sql`
          SELECT id
          FROM staff_member
          WHERE tenant_id = ${auth.tenantId}
            AND active = true
          ORDER BY
            CASE WHEN component = ${component} THEN 0 ELSE 1 END,
            display_name,
            id
          LIMIT 1
        `);
        const assigneeId =
          rows<{ id: string }>(assigneeResult)[0]?.id ?? null;
        const workItemId = randomUUID();
        const insertResult = await transaction.execute(sql`
          INSERT INTO staff_work_item (
            id,
            tenant_id,
            student_id,
            key,
            title,
            description,
            status,
            priority,
            work_type,
            component,
            due_at,
            escalated,
            assignee_id,
            source_type,
            source_id,
            version
          )
          VALUES (
            ${workItemId},
            ${auth.tenantId},
            ${document.student_id},
            ${`DOC-${document.id.replaceAll("-", "").slice(0, 8).toUpperCase()}`},
            ${`Review ${document.file_name}`},
            'Verify the stored original and make the official staff decision.',
            'todo',
            ${document.category === "financial_aid" ? "urgent" : "high"},
            'document_review',
            ${component},
            NOW() + INTERVAL '2 days',
            false,
            ${assigneeId},
            'document',
            ${document.id},
            1
          )
          ON CONFLICT (tenant_id, source_type, source_id) DO NOTHING
          RETURNING id
        `);
        if (rows<{ id: string }>(insertResult).length === 1) {
          await this.insertWorkLog(transaction, {
            auth,
            workItemId,
            actorName: "VV workflow",
            actorType: "system",
            action: "created",
            message:
              "Created when the student document entered staff review.",
          });
        }
      });
    }
  }

  private mapWorkItem(
    item: WorkItemRow,
    logs: WorkLogRow[],
  ): StaffWorkItem {
    return {
      id: item.id,
      key: item.key,
      title: item.title,
      description: item.description,
      status: item.status,
      priority: item.priority,
      type: item.work_type,
      component: item.component,
      dueAt: item.due_at ? isoTimestamp(item.due_at) : null,
      escalated: item.escalated,
      version: item.version,
      createdAt: isoTimestamp(item.created_at),
      updatedAt: isoTimestamp(item.updated_at),
      assignee:
        item.assignee_id &&
        item.assignee_name &&
        item.assignee_email &&
        item.assignee_component
          ? {
              id: item.assignee_id,
              name: item.assignee_name,
              email: item.assignee_email,
              component: item.assignee_component,
            }
          : null,
      student: {
        id: item.student_id,
        name: `${item.first_name} ${item.last_name}`,
        preferredName: item.preferred_name,
        programName: item.program_name,
        classYear: item.class_year,
      },
      source:
        item.source_type && item.source_id
          ? { type: item.source_type, id: item.source_id }
          : null,
      history: logs
        .filter((log) => log.work_item_id === item.id)
        .map((log) => ({
          id: log.id,
          action: log.action,
          message: log.message,
          actorName: log.actor_name,
          occurredAt: isoTimestamp(log.occurred_at),
        })),
    };
  }

  private async requireWorkItem(
    auth: AuthContext,
    workItemId: string,
  ): Promise<StaffWorkItem> {
    const center = await this.getActionCenter(auth);
    const item = center.items.find((candidate) => candidate.id === workItemId);
    if (!item) {
      throw new NotFoundError(
        "STAFF_WORK_ITEM_NOT_FOUND",
        "The work item was not found",
      );
    }
    return item;
  }

  private async lockWorkItem(
    transaction: Transaction,
    auth: AuthContext,
    workItemId: string,
  ): Promise<LockedWorkItemRow> {
    const result = await transaction.execute(sql`
      SELECT
        id,
        student_id,
        status,
        assignee_id,
        escalated,
        version,
        source_type,
        source_id
      FROM staff_work_item
      WHERE tenant_id = ${auth.tenantId}
        AND id = ${workItemId}
      FOR UPDATE
    `);
    const item = rows<LockedWorkItemRow>(result)[0];
    if (!item) {
      throw new NotFoundError(
        "STAFF_WORK_ITEM_NOT_FOUND",
        "The work item was not found",
      );
    }
    return item;
  }

  private async staffName(
    transaction: Transaction,
    auth: AuthContext,
    staffId: string,
  ): Promise<string> {
    const result = await transaction.execute(sql`
      SELECT display_name
      FROM staff_member
      WHERE tenant_id = ${auth.tenantId}
        AND id = ${staffId}
        AND active = true
    `);
    const name = rows<{ display_name: string }>(result)[0]?.display_name;
    if (!name) {
      throw new ApiError(
        403,
        "STAFF_IDENTITY_NOT_CONFIGURED",
        "The staff identity is not configured for this university",
      );
    }
    return name;
  }

  private async insertWorkLog(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      workItemId: string;
      actorName: string;
      actorType?: "staff" | "system";
      action: StaffWorkItemLog["action"];
      message: string;
    },
  ): Promise<void> {
    const actorType = input.actorType ?? "staff";
    await transaction.execute(sql`
      INSERT INTO staff_work_log (
        id,
        tenant_id,
        work_item_id,
        actor_type,
        actor_id,
        actor_name,
        action,
        message,
        occurred_at
      )
      VALUES (
        ${randomUUID()},
        ${input.auth.tenantId},
        ${input.workItemId},
        ${actorType},
        ${actorType === "staff" ? input.auth.actorId : null},
        ${input.actorName},
        ${input.action},
        ${input.message},
        NOW()
      )
    `);
  }

  private async insertStudentMessage(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      studentId: string;
      subject: string;
      body: string;
    },
  ): Promise<StudentMessage> {
    const id = randomUUID();
    const result = await transaction.execute(sql`
      INSERT INTO student_message (
        id,
        tenant_id,
        student_id,
        subject,
        body,
        sender_name,
        sent_at,
        read_at,
        created_at
      )
      VALUES (
        ${id},
        ${input.auth.tenantId},
        ${input.studentId},
        ${input.subject},
        ${input.body},
        'Enrollment Team',
        NOW(),
        NULL,
        NOW()
      )
      RETURNING sent_at
    `);
    const sentAt = rows<{ sent_at: Date }>(result)[0]?.sent_at;
    if (!sentAt) {
      throw new ApiError(
        500,
        "STUDENT_NOTIFICATION_FAILED",
        "The student notification could not be created",
      );
    }
    return {
      id,
      subject: input.subject,
      body: input.body,
      senderName: "Enrollment Team",
      sentAt: isoTimestamp(sentAt),
      readAt: null,
    };
  }

  private async refreshRequirementDependencies(
    transaction: Transaction,
    auth: AuthContext,
    completedRequirementId: string,
  ): Promise<void> {
    await transaction.execute(sql`
      WITH completed_journey AS (
        SELECT journey_id
        FROM student_requirement
        WHERE tenant_id = ${auth.tenantId}
          AND id = ${completedRequirementId}
      )
      UPDATE student_requirement candidate
      SET status = 'ready',
          version = version + 1,
          updated_at = NOW()
      FROM requirement_definition_version definition
      WHERE candidate.tenant_id = ${auth.tenantId}
        AND candidate.journey_id = (
          SELECT journey_id FROM completed_journey
        )
        AND candidate.status = 'blocked'
        AND definition.id = candidate.requirement_definition_version_id
        AND definition.tenant_id = candidate.tenant_id
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(definition.depends_on_codes) dependency_code
          WHERE NOT EXISTS (
            SELECT 1
            FROM student_requirement dependency
            JOIN requirement_definition_version dependency_definition
              ON dependency_definition.id =
                   dependency.requirement_definition_version_id
             AND dependency_definition.tenant_id = dependency.tenant_id
            WHERE dependency.tenant_id = candidate.tenant_id
              AND dependency.journey_id = candidate.journey_id
              AND dependency_definition.code = dependency_code
              AND dependency.status IN (
                'completed',
                'waived',
                'not_applicable'
              )
          )
        )
    `);
  }

  private async insertAudit(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      requestId: string;
      action: string;
      resourceType: string;
      resourceId: string;
      metadata: Record<string, unknown>;
    },
  ): Promise<void> {
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
        ${randomUUID()},
        ${input.auth.tenantId},
        'staff',
        ${input.auth.actorId},
        NULL,
        ${input.action},
        ${input.resourceType},
        ${input.resourceId},
        'staff_enrollment_operations',
        ${input.requestId},
        ${input.requestId},
        ${JSON.stringify(input.metadata)}::jsonb,
        NOW(),
        NOW()
      )
    `);
  }

  private async insertOutbox(
    transaction: Transaction,
    input: {
      auth: AuthContext;
      requestId: string;
      eventName: string;
      aggregateType: string;
      aggregateId: string;
      aggregateVersion: number;
      data: Record<string, unknown>;
    },
  ): Promise<void> {
    const eventId = randomUUID();
    const occurredAt = new Date();
    const payload = {
      eventId,
      eventName: input.eventName,
      occurredAt: occurredAt.toISOString(),
      tenantId: input.auth.tenantId,
      aggregateType: input.aggregateType,
      aggregateId: input.aggregateId,
      aggregateVersion: input.aggregateVersion,
      actor: { type: "staff", id: input.auth.actorId },
      correlationId: input.requestId,
      causationId: eventId,
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
        ${occurredAt},
        'staff',
        ${input.auth.actorId},
        ${input.requestId},
        ${eventId},
        ${JSON.stringify(payload)}::jsonb,
        ${occurredAt}
      )
    `);
  }
}
