/**
 * The Edward READ generalization bank (development split).
 *
 * Written 2026-09-02 against the frozen aster-demo snapshot BEFORE any run
 * against Edward. Sixteen categories, ~110 cases, roughly half student and
 * half staff. Every expectation is a template over ground-truth.json (plain
 * SQL) or a structural check; wording is never the ground truth. Do not edit
 * a case to make a run pass — a wrong *data* expectation may be corrected,
 * recorded in CHANGELOG.md.
 */

import { ADVISER_CONTACT_CASES } from "./cases/adviser_contact.mjs";
import { AVAILABILITY_CASES } from "./cases/availability.mjs";
import { DEADLINES_BLOCKERS_CASES } from "./cases/deadlines_blockers.mjs";
import { DOCUMENTS_CASES } from "./cases/documents.mjs";
import { STATUS_OVERVIEW_CASES } from "./cases/status_overview.mjs";
import { APPOINTMENTS_CASES } from "./cases/appointments.mjs";
import { ACTION_CENTER_CASES } from "./cases/action_center_tasks.mjs";
import { OWNERSHIP_CASES } from "./cases/ownership_assignments.mjs";
import { RECENT_CHANGES_CASES } from "./cases/recent_changes.mjs";
import { CROSS_ENTITY_CASES } from "./cases/cross_entity.mjs";
import { MULTI_INTENT_CASES } from "./cases/multi_intent.mjs";
import { MULTI_TURN_CASES } from "./cases/multi_turn.mjs";
import { AMBIGUOUS_CASES } from "./cases/ambiguous_incomplete.mjs";
import { HONESTY_CASES } from "./cases/honesty_unavailable.mjs";
import { AUTHORIZATION_CASES } from "./cases/authorization.mjs";
import { UNSUPPORTED_CASES } from "./cases/unsupported_actions.mjs";

export const CATEGORIES = [
  "adviser_contact",
  "availability",
  "deadlines_blockers",
  "documents",
  "status_overview",
  "appointments",
  "action_center_tasks",
  "ownership_assignments",
  "recent_changes",
  "cross_entity",
  "multi_intent",
  "multi_turn",
  "ambiguous_incomplete",
  "honesty_unavailable",
  "authorization",
  "unsupported_actions",
];

export const CASES = [
  ...ADVISER_CONTACT_CASES,
  ...AVAILABILITY_CASES,
  ...DEADLINES_BLOCKERS_CASES,
  ...DOCUMENTS_CASES,
  ...STATUS_OVERVIEW_CASES,
  ...APPOINTMENTS_CASES,
  ...ACTION_CENTER_CASES,
  ...OWNERSHIP_CASES,
  ...RECENT_CHANGES_CASES,
  ...CROSS_ENTITY_CASES,
  ...MULTI_INTENT_CASES,
  ...MULTI_TURN_CASES,
  ...AMBIGUOUS_CASES,
  ...HONESTY_CASES,
  ...AUTHORIZATION_CASES,
  ...UNSUPPORTED_CASES,
];

const ids = new Set();
for (const item of CASES) {
  if (ids.has(item.id)) throw new Error(`Duplicate case id ${item.id}`);
  ids.add(item.id);
  if (!CATEGORIES.includes(item.category)) throw new Error(`${item.id}: unknown category ${item.category}`);
  if (!["student", "staff"].includes(item.actorKind)) throw new Error(`${item.id}: bad actorKind`);
}
