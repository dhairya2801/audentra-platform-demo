/**
 * Direct reads of the eval database.
 *
 * A write suite has to answer one question the API cannot: *did the row
 * actually change?* Everything Edward reports — the preview, the receipt, the
 * prose — is Edward's account of itself. The canonical table is the only
 * witness that is not, so effect assertions come from here while behaviour
 * assertions come from the HTTP responses and per-turn traces.
 *
 * `psql` is invoked through the shell rather than a driver so the harness
 * keeps this repository's zero-dependency convention. Every statement is
 * wrapped into a single `json_agg`, so there is no delimiter to get wrong and
 * types survive the round trip. Override the command with WRITE_EVAL_PSQL
 * when the database is not the local compose container.
 */

import { execFileSync } from "node:child_process";

const PSQL_COMMAND =
  process.env.WRITE_EVAL_PSQL ?? "docker exec -i audentra-platform-postgres-1 psql -U vv -d {db}";
const DB_NAME = process.env.WRITE_EVAL_DB ?? "vv_enrollment_write_eval";
const PSQL = PSQL_COMMAND.replace("{db}", DB_NAME);

export const TENANT_ID =
  process.env.WRITE_EVAL_TENANT_ID ?? "00000000-0000-7000-8000-000000000003";

/** Run one SELECT and return its rows as objects. */
export function sql(statement) {
  const [command, ...args] = PSQL.split(/\s+/);
  const wrapped = `SELECT coalesce(json_agg(row_to_json(q)), '[]'::json) FROM (${statement}) q`;
  let out;
  try {
    out = execFileSync(command, [...args, "-v", "ON_ERROR_STOP=1", "-tAc", wrapped], {
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    });
  } catch (error) {
    const detail = String(error.stderr ?? error.message).trim();
    throw new Error(`psql failed: ${detail}\n  statement: ${statement.slice(0, 240)}`);
  }
  return JSON.parse(out.trim() || "[]");
}

export function one(statement) {
  const rows = sql(statement);
  return rows.length === 0 ? null : rows[0];
}

export function count(statement) {
  const row = one(statement);
  if (row === null) return 0;
  return Number(Object.values(row)[0] ?? 0);
}

export function quote(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

const T = quote(TENANT_ID);

// --- fixture resolution ----------------------------------------------------

/**
 * Turn the external references in `fixtures.mjs` into the ids, names and live
 * record state the cases and the grader need. One round trip per kind.
 */
export function resolveFixtures({ staff, students, workItems }) {
  const staffRefs = Object.values(staff).map((entry) => quote(entry.ref)).join(",");
  const studentRefs = Object.values(students).map((entry) => quote(entry.ref)).join(",");
  const itemKeys = Object.values(workItems).map(quote).join(",");

  const staffRows = sql(`
    SELECT m.external_ref AS ref, m.id::text AS id, m.display_name AS name,
           m.role_code AS role_code, m.component AS component,
           coalesce((SELECT array_agg(c.capability ORDER BY c.capability)
                     FROM staff_role_capability c
                     WHERE c.tenant_id = m.tenant_id AND c.role_code = m.role_code),
                    ARRAY[]::varchar[]) AS capabilities
    FROM staff_member m
    WHERE m.tenant_id = ${T} AND m.external_ref IN (${staffRefs})`);

  const studentRows = sql(`
    SELECT s.external_ref AS ref, s.id::text AS id, s.person_id::text AS person_id,
           p.first_name || ' ' || p.last_name AS full_name,
           coalesce(sp.preferred_name, p.preferred_name, p.first_name) AS preferred_name,
           sp.pronouns AS pronouns, sp.mobile_phone AS mobile_phone,
           sp.communication_preference AS communication_preference,
           (SELECT m.external_ref FROM student_staff_assignment a
              JOIN staff_member m ON m.id = a.staff_member_id AND m.tenant_id = a.tenant_id
             WHERE a.tenant_id = s.tenant_id AND a.student_id = s.id
               AND a.ended_at IS NULL AND a.role = 'primary_advisor' LIMIT 1) AS adviser_ref
    FROM student s
    JOIN person p ON p.id = s.person_id AND p.tenant_id = s.tenant_id
    LEFT JOIN student_profile sp ON sp.student_id = s.id AND sp.tenant_id = s.tenant_id
    WHERE s.tenant_id = ${T} AND s.external_ref IN (${studentRefs})`);

  const itemRows = sql(`
    SELECT w.key AS key, w.id::text AS id, w.title AS title, w.status AS status,
           w.priority AS priority, w.version AS version, w.component AS component,
           w.next_step AS next_step, s.external_ref AS student_ref,
           p.first_name || ' ' || p.last_name AS student_name,
           m.external_ref AS assignee_ref
    FROM staff_work_item w
    JOIN student s ON s.id = w.student_id AND s.tenant_id = w.tenant_id
    JOIN person p ON p.id = s.person_id AND p.tenant_id = s.tenant_id
    LEFT JOIN staff_member m ON m.id = w.assignee_id AND m.tenant_id = w.tenant_id
    WHERE w.tenant_id = ${T} AND w.key IN (${itemKeys})`);

  const byStaffRef = new Map(staffRows.map((row) => [row.ref, row]));
  const byStudentRef = new Map(studentRows.map((row) => [row.ref, row]));
  const byItemKey = new Map(itemRows.map((row) => [row.key, row]));

  const resolved = { staff: {}, students: {}, workItems: {} };
  for (const [key, spec] of Object.entries(staff)) {
    const row = byStaffRef.get(spec.ref);
    if (!row) throw new Error(`staff fixture missing from the database: ${spec.ref}`);
    resolved.staff[key] = {
      key,
      ...spec,
      id: row.id,
      name: row.name,
      roleCode: row.role_code,
      component: row.component,
      capabilities: row.capabilities ?? [],
    };
  }
  for (const [key, spec] of Object.entries(students)) {
    const row = byStudentRef.get(spec.ref);
    if (!row) throw new Error(`student fixture missing from the database: ${spec.ref}`);
    resolved.students[key] = {
      key,
      ...spec,
      id: row.id,
      personId: row.person_id,
      fullName: row.full_name,
      preferredName: row.preferred_name,
      pronouns: row.pronouns,
      mobilePhone: row.mobile_phone,
      communicationPreference: row.communication_preference,
      adviserRef: row.adviser_ref,
    };
  }
  for (const [key, itemKey] of Object.entries(workItems)) {
    const row = byItemKey.get(itemKey);
    if (!row) throw new Error(`work item fixture missing from the database: ${itemKey}`);
    resolved.workItems[key] = {
      key: row.key,
      id: row.id,
      title: row.title,
      status: row.status,
      priority: row.priority,
      version: Number(row.version),
      component: row.component,
      nextStep: row.next_step,
      studentRef: row.student_ref,
      studentName: row.student_name,
      assigneeRef: row.assignee_ref,
    };
  }
  return resolved;
}

// --- effect probes ---------------------------------------------------------

export function workItemByKey(key) {
  return one(`
    SELECT w.key AS key, w.status AS status, w.priority AS priority,
           w.next_step AS "nextStep", w.version AS version, w.title AS title,
           m.external_ref AS "assigneeRef", s.external_ref AS "studentRef",
           to_char(w.due_at, 'YYYY-MM-DD') AS "dueAt",
           to_char(w.follow_up_at, 'YYYY-MM-DD') AS "followUpAt"
    FROM staff_work_item w
    JOIN student s ON s.id = w.student_id AND s.tenant_id = w.tenant_id
    LEFT JOIN staff_member m ON m.id = w.assignee_id AND m.tenant_id = w.tenant_id
    WHERE w.tenant_id = ${T} AND w.key = ${quote(key)}`);
}

export function studentProfile(studentId) {
  return one(`
    SELECT sp.preferred_name AS "preferredName", sp.pronouns AS pronouns,
           sp.mobile_phone AS "mobilePhone",
           sp.communication_preference AS "communicationPreference"
    FROM student s
    LEFT JOIN student_profile sp ON sp.student_id = s.id AND sp.tenant_id = s.tenant_id
    WHERE s.tenant_id = ${T} AND s.id = ${quote(studentId)}`);
}

/** Work items Edward created since a moment, newest last. */
export function edwardWorkItems(sinceIso) {
  return sql(`
    SELECT w.key AS key, w.title AS title, w.priority AS priority, w.status AS status,
           s.external_ref AS "studentRef", m.external_ref AS "assigneeRef",
           to_char(w.due_at, 'YYYY-MM-DD') AS "dueAt", w.next_step AS "nextStep"
    FROM staff_work_item w
    JOIN student s ON s.id = w.student_id AND s.tenant_id = w.tenant_id
    LEFT JOIN staff_member m ON m.id = w.assignee_id AND m.tenant_id = w.tenant_id
    WHERE w.tenant_id = ${T} AND w.created_at > ${quote(sinceIso)}
      AND w.description LIKE '%from Edward%'
    ORDER BY w.created_at`);
}

export function helpRequests(studentId, sinceIso) {
  return sql(`
    SELECT topic_code AS topic, status AS status, subject AS subject, message AS message
    FROM student_inquiry
    WHERE tenant_id = ${T} AND student_id = ${quote(studentId)}
      AND created_at > ${quote(sinceIso)}`);
}

export function intentRow(intentId) {
  return one(`
    SELECT status AS status, action_type AS action, version AS version,
           failure_code AS failure_code, target_student_id::text AS target_student_id,
           risk_class AS risk_class, confirmation_mode AS confirmation_mode
    FROM agent_action_intent
    WHERE tenant_id = ${T} AND id = ${quote(intentId)}`);
}

export function receiptForIntent(intentId) {
  return one(`
    SELECT status AS status, action_type AS action, affected_count AS affected_count,
           jsonb_array_length(audit_event_ids) AS audit_events
    FROM agent_action_receipt
    WHERE tenant_id = ${T} AND action_intent_id = ${quote(intentId)}`);
}

export function batchItems(intentId) {
  return sql(`
    SELECT b.status AS status, s.external_ref AS student_ref, w.key AS work_item_key
    FROM agent_action_batch_item b
    JOIN student s ON s.id = b.student_id AND s.tenant_id = b.tenant_id
    LEFT JOIN staff_work_item w ON w.id = b.work_item_id AND w.tenant_id = b.tenant_id
    WHERE b.tenant_id = ${T} AND b.action_intent_id = ${quote(intentId)}`);
}

export function sendIntents(sinceIso) {
  return sql(`
    SELECT status AS status, subject AS subject,
           agent_action_intent_id::text AS "actionIntentId",
           to_char(sent_at, 'YYYY-MM-DD') AS "sentAt"
    FROM staff_email_send_intent
    WHERE tenant_id = ${T} AND created_at > ${quote(sinceIso)}`);
}

/** Everything a turn is capable of changing, counted since a moment. */
export function mutationCensus(sinceIso) {
  const since = quote(sinceIso);
  return {
    workItems: count(
      `SELECT count(*) AS n FROM staff_work_item WHERE tenant_id = ${T} AND created_at > ${since}`,
    ),
    workItemUpdates: count(
      `SELECT count(*) AS n FROM staff_work_item WHERE tenant_id = ${T} AND updated_at > ${since} AND created_at <= ${since}`,
    ),
    inquiries: count(
      `SELECT count(*) AS n FROM student_inquiry WHERE tenant_id = ${T} AND created_at > ${since}`,
    ),
    sendIntents: count(
      `SELECT count(*) AS n FROM staff_email_send_intent WHERE tenant_id = ${T} AND created_at > ${since}`,
    ),
    receipts: count(
      `SELECT count(*) AS n FROM agent_action_receipt WHERE tenant_id = ${T} AND committed_at > ${since}`,
    ),
    profileUpdates: count(
      `SELECT count(*) AS n FROM student_profile WHERE tenant_id = ${T} AND updated_at > ${since}`,
    ),
  };
}

export function nowIso() {
  const row = one(
    `SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS now`,
  );
  return row.now;
}

export function databaseName() {
  return DB_NAME;
}
