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
    const aiResponseMigration = await readFile(
      resolve(
        __dirname,
        "../migrations/0005_ai_provider_response_attempts.sql",
      ),
      "utf8",
    );
    const credentialIdentityMigration = await readFile(
      resolve(
        __dirname,
        "../migrations/0006_credential_identity.sql",
      ),
      "utf8",
    );
    const migration = `${initialMigration}\n${portalMigration}\n${agenticDocumentMigration}\n${aiResponseMigration}\n${credentialIdentityMigration}`;
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
      "ai_provider_response_attempt",
      "student_identity_invitation",
      "credential_account",
      "auth_session",
      "auth_verification_challenge",
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

  it("stores only hashed credential session and verification tokens", async () => {
    const migration = await readFile(
      resolve(
        __dirname,
        "../migrations/0006_credential_identity.sql",
      ),
      "utf8",
    );

    expect(migration).toContain("password_hash text NOT NULL");
    expect(migration).toContain("token_hash char(64) NOT NULL UNIQUE");
    expect(migration).toContain("email_verified_at timestamptz");
    expect(migration).toContain("phone_verified_at timestamptz");
    expect(migration).toContain("REFERENCES credential_account(id) ON DELETE CASCADE");
    expect(migration).not.toContain("session_token");
  });

  it("stores raw provider attempts separately from normalized document extraction", async () => {
    const migration = await readFile(
      resolve(
        __dirname,
        "../migrations/0005_ai_provider_response_attempts.sql",
      ),
      "utf8",
    );

    expect(migration).toContain("raw_response_text text");
    expect(migration).toContain("response_body jsonb");
    expect(migration).toContain("transport_error jsonb");
    expect(migration).toContain("finish_reason varchar(80)");
    expect(migration).toContain("document_id uuid NOT NULL");
    expect(migration).toContain("'openrouter', 'groq'");
  });

  it("persists opaque document content references and reviewable extraction data", async () => {
    const [agenticMigration, processingPolicyMigration] = await Promise.all([
      readFile(
        resolve(__dirname, "../migrations/0002_agentic_documents.sql"),
        "utf8",
      ),
      readFile(
        resolve(__dirname, "../migrations/0007_document_processing_policy.sql"),
        "utf8",
      ),
    ]);
    const migration = `${agenticMigration}\n${processingPolicyMigration}`;

    expect(migration).toContain("storage_key varchar(512)");
    expect(migration).toContain("sha256 char(64)");
    expect(migration).toContain("extraction jsonb");
    expect(migration).toContain("'processing'");
    expect(migration).toContain("'needs_review'");
    expect(migration).toContain("'under_review'");
    expect(migration).toContain("document_record_storage_key_idx");
    expect(migration).toContain("processing_mode varchar(24)");
    expect(migration).toContain("'manual_review'");
  });

  it("migrates to the authoritative eight-step onboarding sequence", async () => {
    const migration = await readFile(
      resolve(
        __dirname,
        "../migrations/0008_onboarding_question_alignment.sql",
      ),
      "utf8",
    );
    for (const step of [
      "offer",
      "about_you",
      "housing",
      "campus_life",
      "emergency_contacts",
      "family_permissions",
      "review_and_sign",
      "deposit",
    ]) {
      expect(migration).toContain(`'${step}'`);
    }
    expect(migration).toContain(
      "array_remove(completed_steps, 'other_records')",
    );
    expect(migration).toContain(
      "DROP CONSTRAINT IF EXISTS student_onboarding_current_step_check",
    );
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
