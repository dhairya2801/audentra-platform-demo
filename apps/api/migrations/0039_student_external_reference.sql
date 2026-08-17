-- A human-typable, institution-facing student reference.
--
-- Every student already has a UUID, and a UUID is the right primary key. It is
-- the wrong thing to read off a spreadsheet, quote in a support call, or type
-- into a demo sign-in box. Institutions issue a short reference for exactly
-- that purpose, and the synthetic demo population already generates one
-- ("SYN-000417"), so the column stores it rather than leaving the reference
-- outside the canonical model.
--
-- Nullable: existing students and every tenant that does not issue references
-- keep working untouched. Unique per tenant when present, so a reference
-- resolves to exactly one student and never crosses a tenant boundary.

ALTER TABLE student
  ADD COLUMN external_ref varchar(64),
  ADD CONSTRAINT student_external_ref_format_check
    CHECK (external_ref IS NULL OR external_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$');

CREATE UNIQUE INDEX student_tenant_external_ref_uidx
  ON student (tenant_id, external_ref)
  WHERE external_ref IS NOT NULL;

-- "Which journey belongs to this student" had no index.
--
-- `enrollment_journey` was only reachable by primary key or by
-- (tenant_id, offer_id), so every per-student lookup was a sequential scan.
-- With fourteen journeys nobody noticed. With two and a half thousand, the
-- staff cohort page pays for one scan per row it renders: measured on the
-- demo campus, a twenty-student page spent 17 ms almost entirely here, and
-- the portal repeats the same lookup on every student read.
CREATE INDEX enrollment_journey_student_idx
  ON enrollment_journey (tenant_id, student_id);
