-- Adapted intentionally from mainline d859e5d; fresh integration migration.
-- Durable, student-safe document review decisions for enrollment tasks.
--
-- A document submission receives at most one official decision. A student can
-- resubmit by uploading a new document, which preserves the rejected document
-- and its decision while creating a new review cycle.

CREATE TABLE tenant_document_rejection_reason (
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  code varchar(80) NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{0,79}$'),
  label varchar(160) NOT NULL CHECK (char_length(btrim(label)) > 0),
  description varchar(500) NOT NULL CHECK (char_length(btrim(description)) > 0),
  display_order integer NOT NULL DEFAULT 0,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, code)
);

CREATE INDEX tenant_document_rejection_reason_active_idx
  ON tenant_document_rejection_reason(tenant_id, active, display_order, code);

INSERT INTO tenant_document_rejection_reason (
  tenant_id, code, label, description, display_order
)
SELECT tenant.id, reason.code, reason.label, reason.description, reason.display_order
FROM tenant
CROSS JOIN (
  VALUES
    ('illegible', 'Image or text is unclear',
     'Upload a clear, complete scan or photo with every edge visible.', 10),
    ('incomplete', 'Document is incomplete',
     'Upload every required page and complete all required fields or signatures.', 20),
    ('wrong_document', 'Wrong document',
     'Upload the document requested for this enrollment task.', 30),
    ('expired', 'Document is expired',
     'Upload a document that is currently valid.', 40),
    ('information_mismatch', 'Information does not match',
     'Correct the conflicting information or upload a matching document.', 50),
    ('unsupported_evidence', 'Evidence cannot be accepted',
     'Upload an official document that meets the requirement instructions.', 60)
) AS reason(code, label, description, display_order)
ON CONFLICT (tenant_id, code) DO NOTHING;

CREATE TABLE document_review_decision (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  document_id uuid NOT NULL REFERENCES document_record(id) ON DELETE RESTRICT,
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE RESTRICT,
  requirement_id uuid REFERENCES student_requirement(id) ON DELETE RESTRICT,
  work_item_id uuid REFERENCES staff_work_item(id) ON DELETE RESTRICT,
  reviewer_id uuid REFERENCES staff_member(id) ON DELETE RESTRICT,
  reviewer_display_name varchar(160) NOT NULL
    CHECK (char_length(btrim(reviewer_display_name)) > 0),
  decision varchar(24) NOT NULL CHECK (decision IN ('accepted', 'rejected')),
  reason_code varchar(80),
  reason_label varchar(160),
  student_message varchar(500),
  internal_note varchar(1000),
  source varchar(24) NOT NULL DEFAULT 'staff_review'
    CHECK (source IN ('staff_review', 'legacy_backfill')),
  decided_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT document_review_decision_document_uidx
    UNIQUE (tenant_id, document_id),
  CONSTRAINT document_review_decision_live_reviewer_check CHECK (
    source = 'legacy_backfill'
    OR (reviewer_id IS NOT NULL AND work_item_id IS NOT NULL)
  ),
  CONSTRAINT document_review_decision_rejection_reason_check CHECK (
    (decision = 'accepted' AND reason_code IS NULL AND reason_label IS NULL)
    OR (
      decision = 'rejected'
      AND reason_code IS NOT NULL
      AND reason_label IS NOT NULL
      AND student_message IS NOT NULL
      AND char_length(btrim(student_message)) >= 3
    )
  )
);

CREATE INDEX document_review_decision_student_idx
  ON document_review_decision(tenant_id, student_id, decided_at, id);

CREATE INDEX document_review_decision_requirement_idx
  ON document_review_decision(tenant_id, requirement_id, decided_at, id)
  WHERE requirement_id IS NOT NULL;

-- Capture every requirement status mutation at the database boundary so
-- document uploads/reviews, help, FERPA, managed configuration, and future
-- writers cannot silently bypass enrollment-task history.
CREATE TABLE student_requirement_status_event (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE RESTRICT,
  requirement_id uuid NOT NULL REFERENCES student_requirement(id) ON DELETE RESTRICT,
  from_status varchar(40),
  to_status varchar(40) NOT NULL CHECK (char_length(btrim(to_status)) > 0),
  source varchar(24) NOT NULL CHECK (
    source IN ('assignment', 'status_transition', 'legacy_snapshot')
  ),
  occurred_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX student_requirement_status_event_history_idx
  ON student_requirement_status_event(
    tenant_id, student_id, requirement_id, occurred_at, id
  );

-- Earlier transition detail did not exist. Record only the status honestly
-- known at migration time instead of inventing prior transitions.
INSERT INTO student_requirement_status_event (
  id, tenant_id, student_id, requirement_id, from_status, to_status,
  source, occurred_at
)
SELECT
  gen_random_uuid(), requirement.tenant_id, journey.student_id,
  requirement.id, NULL, requirement.status, 'legacy_snapshot',
  COALESCE(requirement.updated_at, requirement.created_at)
FROM student_requirement AS requirement
JOIN enrollment_journey AS journey
  ON journey.id=requirement.journey_id
 AND journey.tenant_id=requirement.tenant_id;

CREATE FUNCTION record_student_requirement_status_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  journey_student_id uuid;
  previous_status varchar(40);
  event_source varchar(24);
  event_time timestamptz;
  prior_scope text;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.status IS NOT DISTINCT FROM NEW.status THEN
    RETURN NEW;
  END IF;

  prior_scope:=current_setting('audentra.tenant_id',true);
  PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
  SELECT journey.student_id INTO STRICT journey_student_id
  FROM enrollment_journey AS journey
  WHERE journey.tenant_id=NEW.tenant_id AND journey.id=NEW.journey_id;

  IF TG_OP = 'INSERT' THEN
    previous_status := NULL;
    event_source := 'assignment';
    event_time := COALESCE(NEW.created_at, now());
  ELSE
    previous_status := OLD.status;
    event_source := 'status_transition';
    event_time := clock_timestamp();
  END IF;

  INSERT INTO student_requirement_status_event (
    id, tenant_id, student_id, requirement_id, from_status, to_status,
    source, occurred_at
  ) VALUES (
    gen_random_uuid(), NEW.tenant_id, journey_student_id, NEW.id,
    previous_status, NEW.status, event_source, event_time
  );
  PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
  RETURN NEW;
END;
$$;

CREATE TRIGGER student_requirement_status_event_on_insert
AFTER INSERT ON student_requirement
FOR EACH ROW EXECUTE FUNCTION record_student_requirement_status_event();

CREATE TRIGGER student_requirement_status_event_on_update
AFTER UPDATE OF status ON student_requirement
FOR EACH ROW EXECUTE FUNCTION record_student_requirement_status_event();

CREATE FUNCTION prevent_student_requirement_status_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'student_requirement_status_event is append-only';
END;
$$;

CREATE TRIGGER student_requirement_status_event_append_only
BEFORE UPDATE OR DELETE ON student_requirement_status_event
FOR EACH ROW EXECUTE FUNCTION prevent_student_requirement_status_event_mutation();

-- Preserve pre-migration terminal state as honest legacy history. We do not
-- invent a reviewer or staff-authored note for decisions whose original
-- lineage was not persisted.
INSERT INTO document_review_decision (
  id, tenant_id, document_id, student_id, requirement_id,
  reviewer_display_name, decision, reason_code, reason_label,
  student_message, source, decided_at
)
SELECT
  gen_random_uuid(), document.tenant_id, document.id, document.student_id,
  document.requirement_id, 'Reviewer not recorded', document.status,
  CASE WHEN document.status = 'rejected' THEN 'legacy_review' ELSE NULL END,
  CASE WHEN document.status = 'rejected' THEN 'Reason not recorded' ELSE NULL END,
  CASE
    WHEN document.status = 'rejected'
    THEN 'The earlier submission was rejected; its original reviewer guidance was not recorded.'
    ELSE 'This earlier submission was accepted.'
  END,
  'legacy_backfill', document.updated_at
FROM document_record AS document
WHERE document.status IN ('accepted', 'rejected')
ON CONFLICT (tenant_id, document_id) DO NOTHING;

CREATE FUNCTION prevent_document_review_decision_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'document_review_decision is append-only';
END;
$$;

CREATE TRIGGER document_review_decision_append_only
BEFORE UPDATE OR DELETE ON document_review_decision
FOR EACH ROW EXECUTE FUNCTION prevent_document_review_decision_mutation();

-- New evidence cannot claim a document, student, requirement, work item or
-- reviewer from another tenant. Application queries enforce the same boundary.
CREATE FUNCTION validate_document_review_links() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM document_record d WHERE d.tenant_id=NEW.tenant_id
   AND d.id=NEW.document_id AND d.student_id=NEW.student_id
   AND d.requirement_id IS NOT DISTINCT FROM NEW.requirement_id) THEN
   RAISE EXCEPTION 'Document review identity or requirement mismatch';
 END IF;
 IF NEW.reviewer_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM staff_member m
   WHERE m.tenant_id=NEW.tenant_id AND m.id=NEW.reviewer_id) THEN
   RAISE EXCEPTION 'Document review actor tenant mismatch';
 END IF;
 IF NEW.work_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM staff_work_item w
   WHERE w.tenant_id=NEW.tenant_id AND w.id=NEW.work_item_id
     AND w.student_id=NEW.student_id AND w.source_type='document'
     AND w.source_id=NEW.document_id) THEN
   RAISE EXCEPTION 'Document review work item mismatch';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER document_review_links BEFORE INSERT ON document_review_decision
 FOR EACH ROW EXECUTE FUNCTION validate_document_review_links();

ALTER TABLE document_review_decision ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_review_decision FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON document_review_decision
 USING(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
ALTER TABLE tenant_document_rejection_reason ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_document_rejection_reason FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON tenant_document_rejection_reason
 USING(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
ALTER TABLE student_requirement_status_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE student_requirement_status_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON student_requirement_status_event
 USING(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
