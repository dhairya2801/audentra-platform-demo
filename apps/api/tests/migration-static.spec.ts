import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("initial PostgreSQL migration", () => {
  it("contains the authoritative, audit, outbox, tracking, and projection tables", async () => {
    const initialMigration = await readFile(
      resolve(__dirname, "../migrations/0000_initial.sql"),
      "utf8",
    );
    const portalMigration = await readFile(
      resolve(__dirname, "../migrations/0001_student_portal_core.sql"),
      "utf8",
    );
    const agenticDocumentMigration = await readFile(
      resolve(__dirname, "../migrations/0002_agentic_documents.sql"),
      "utf8",
    );
    const migration = `${initialMigration}\n${portalMigration}\n${agenticDocumentMigration}`;
    const requiredTables = [
      "tenant",
      "person",
      "student",
      "admission_offer",
      "enrollment_journey",
      "student_requirement",
      "audit_event",
      "outbox_event",
      "activity_event",
      "idempotency_record",
      "student_portal_projection",
      "projection_event_receipt",
      "student_onboarding",
      "student_message",
      "document_record",
      "student_appointment",
      "payment_transaction",
      "student_profile",
      "help_article",
    ];

    for (const table of requiredTables) {
      expect(migration).toMatch(
        new RegExp(`CREATE TABLE ${table} \\(`, "i"),
      );
    }
  });

  it("persists opaque document content references and reviewable extraction data", async () => {
    const migration = await readFile(
      resolve(__dirname, "../migrations/0002_agentic_documents.sql"),
      "utf8",
    );

    expect(migration).toContain("storage_key varchar(512)");
    expect(migration).toContain("sha256 char(64)");
    expect(migration).toContain("extraction jsonb");
    expect(migration).toContain("'processing'");
    expect(migration).toContain("'needs_review'");
    expect(migration).toContain("'under_review'");
    expect(migration).toContain("document_record_storage_key_idx");
  });

  it("preserves the authoritative nine-step onboarding sequence", async () => {
    const migration = await readFile(
      resolve(__dirname, "../migrations/0001_student_portal_core.sql"),
      "utf8",
    );
    for (const step of [
      "offer",
      "about_you",
      "housing",
      "campus_life",
      "emergency_contacts",
      "other_records",
      "family_permissions",
      "review_and_sign",
      "deposit",
    ]) {
      expect(migration).toContain(`'${step}'`);
    }
    expect(migration).toContain("completed_steps text[]");
  });

  it("enforces append-only audit records and domain status constraints", async () => {
    const migration = await readFile(
      resolve(__dirname, "../migrations/0000_initial.sql"),
      "utf8",
    );

    expect(migration).toContain("audit_event is append-only");
    expect(migration).toMatch(
      /BEFORE UPDATE OR DELETE ON audit_event[\s\S]*prevent_audit_event_mutation/i,
    );
    expect(migration).toContain(
      "status IN ('offered', 'accepted', 'declined', 'expired')",
    );
    expect(migration).toContain(
      "PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)",
    );
  });
});
