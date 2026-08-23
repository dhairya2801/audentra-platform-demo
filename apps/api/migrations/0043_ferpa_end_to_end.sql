-- Canonical FERPA authorization, immutable signing evidence, and scoped
-- parent/guardian delegate sessions. Raw link and session tokens are never
-- persisted; only SHA-256 digests cross this database boundary.

ALTER TABLE requirement_definition_version
  DROP CONSTRAINT IF EXISTS requirement_definition_version_interaction_type_check;
ALTER TABLE requirement_definition_version
  ADD CONSTRAINT requirement_definition_version_interaction_type_check CHECK (
    interaction_type IN (
      'information','approval','form','single_select','multiple_select',
      'selection_flow','upload_file','signature','payment','scheduling','ferpa'
    )
  );

-- Ordinary actions performed through a scoped delegate session retain the
-- acting person in durable workflow/audit lineage. FERPA revisions themselves
-- intentionally remain student/staff/system-only below.
ALTER TABLE agent_run
  DROP CONSTRAINT IF EXISTS agent_run_actor_type_check;
ALTER TABLE agent_run
  ADD CONSTRAINT agent_run_actor_type_check CHECK (
    actor_type IN ('student','delegate','staff','system')
  );

ALTER TABLE staff_work_log
  DROP CONSTRAINT IF EXISTS staff_work_log_actor_type_check;
ALTER TABLE staff_work_log
  ADD CONSTRAINT staff_work_log_actor_type_check CHECK (
    actor_type IN ('student','delegate','staff','system')
  );

CREATE TABLE ferpa_authorization (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  requirement_id uuid NOT NULL REFERENCES student_requirement(id),
  access_decision varchar(24) CHECK (access_decision IN ('grant', 'no_access')),
  status varchar(24) NOT NULL DEFAULT 'incomplete'
    CHECK (status IN ('incomplete', 'completed')),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ferpa_authorization_requirement_uidx UNIQUE (tenant_id, requirement_id),
  CONSTRAINT ferpa_authorization_student_uidx UNIQUE (tenant_id, student_id),
  CHECK (
    (status='completed' AND completed_at IS NOT NULL AND access_decision IS NOT NULL)
    OR (status='incomplete' AND completed_at IS NULL)
  )
);

CREATE TABLE ferpa_signed_document (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  authorization_id uuid NOT NULL REFERENCES ferpa_authorization(id),
  requirement_id uuid NOT NULL REFERENCES student_requirement(id),
  requirement_version integer NOT NULL CHECK (requirement_version > 0),
  template_code varchar(100) NOT NULL DEFAULT 'ferpa_release',
  title varchar(180) NOT NULL,
  file_name varchar(255) NOT NULL,
  mime_type varchar(80) NOT NULL DEFAULT 'application/pdf'
    CHECK (mime_type='application/pdf'),
  size_bytes integer NOT NULL CHECK (size_bytes > 0),
  storage_provider varchar(40) NOT NULL,
  storage_key varchar(512) NOT NULL,
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  signer_name varchar(160) NOT NULL,
  signature_method varchar(20) NOT NULL CHECK (signature_method IN ('typed','drawn')),
  signed_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ferpa_signed_document_authorization_uidx UNIQUE (authorization_id),
  CONSTRAINT ferpa_signed_document_storage_uidx UNIQUE (tenant_id, storage_key)
);

CREATE OR REPLACE FUNCTION prevent_ferpa_signed_document_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ferpa_signed_document is immutable; retain the original signing evidence';
END;
$$;
CREATE TRIGGER ferpa_signed_document_immutable
BEFORE UPDATE OR DELETE ON ferpa_signed_document
FOR EACH ROW EXECUTE FUNCTION prevent_ferpa_signed_document_mutation();

CREATE TABLE ferpa_delegate (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  authorization_id uuid NOT NULL REFERENCES ferpa_authorization(id),
  full_name varchar(160) NOT NULL,
  relationship varchar(32) NOT NULL CHECK (
    relationship IN ('parent','guardian','partner','sponsor','relative','other')
  ),
  email_normalized varchar(254) NOT NULL CHECK (email_normalized=lower(email_normalized)),
  scopes text[] NOT NULL DEFAULT '{}',
  display_order smallint NOT NULL DEFAULT 0 CHECK (display_order BETWEEN 0 AND 3),
  active boolean NOT NULL DEFAULT true,
  legacy_review_required boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    scopes <@ ARRAY[
      'onboarding','dashboard','enrollment','financials','classrooms','campus_life',
      'edward','documents','messages','appointments','payments','profile','help'
    ]::text[]
  ),
  CHECK (
    legacy_review_required
    OR NOT active
    OR cardinality(scopes) BETWEEN 1 AND 13
  )
);
CREATE UNIQUE INDEX ferpa_delegate_active_email_uidx
  ON ferpa_delegate(authorization_id, email_normalized) WHERE active;
CREATE INDEX ferpa_delegate_student_idx
  ON ferpa_delegate(tenant_id, student_id, active, display_order);

CREATE TABLE ferpa_delegate_link (
  delegate_id uuid PRIMARY KEY REFERENCES ferpa_delegate(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  token_hash char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  status varchar(16) NOT NULL CHECK (status IN ('active','revoked')),
  idempotency_key_hash char(64) NOT NULL CHECK (idempotency_key_hash ~ '^[0-9a-f]{64}$'),
  issued_at timestamptz NOT NULL,
  rotated_at timestamptz,
  revoked_at timestamptz,
  last_used_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (status='active' AND revoked_at IS NULL)
    OR (status='revoked' AND revoked_at IS NOT NULL)
  )
);

CREATE TABLE ferpa_delegate_session (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  delegate_id uuid NOT NULL REFERENCES ferpa_delegate(id) ON DELETE CASCADE,
  token_hash char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX ferpa_delegate_session_active_idx
  ON ferpa_delegate_session(delegate_id, expires_at DESC) WHERE revoked_at IS NULL;

-- The idempotency record is written only when the aggregate transaction
-- commits. This reservation makes the earlier object-storage side effect
-- stable and recoverable without persisting raw signature pixels or names.
CREATE TABLE ferpa_signing_reservation (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  authorization_id uuid NOT NULL REFERENCES ferpa_authorization(id),
  actor_id uuid NOT NULL,
  operation varchar(180) NOT NULL,
  idempotency_key varchar(200) NOT NULL,
  request_hash char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  document_id uuid NOT NULL UNIQUE,
  signed_at timestamptz NOT NULL,
  storage_key varchar(512) NOT NULL,
  status varchar(20) NOT NULL DEFAULT 'reserved'
    CHECK (status IN ('reserved','generating','stored','finalized','failed')),
  sha256 char(64) CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes integer CHECK (size_bytes IS NULL OR size_bytes > 0),
  title varchar(240),
  file_name varchar(255),
  signer_name varchar(160),
  signature_method varchar(16) CHECK (signature_method IN ('typed','drawn')),
  lease_expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ferpa_signing_reservation_request_uidx
    UNIQUE (tenant_id, actor_id, operation, idempotency_key),
  CONSTRAINT ferpa_signing_reservation_storage_uidx UNIQUE (tenant_id, storage_key),
  CHECK (
    (status IN ('reserved','generating','failed'))
    OR (
      sha256 IS NOT NULL AND size_bytes IS NOT NULL
      AND title IS NOT NULL AND file_name IS NOT NULL
      AND signer_name IS NOT NULL AND signature_method IS NOT NULL
    )
  )
);
CREATE UNIQUE INDEX ferpa_signing_reservation_active_authorization_uidx
  ON ferpa_signing_reservation(authorization_id)
  WHERE status IN ('reserved','generating','stored');

CREATE TABLE ferpa_authorization_revision (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  authorization_id uuid NOT NULL REFERENCES ferpa_authorization(id),
  authorization_version integer NOT NULL CHECK (authorization_version > 0),
  actor_type varchar(40) NOT NULL CHECK (actor_type IN ('student','staff','system')),
  actor_id uuid NOT NULL,
  action varchar(64) NOT NULL CHECK (
    action IN ('backfilled','completed','access_updated','link_issued','link_rotated','link_revoked')
  ),
  snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
  request_id varchar(128) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ferpa_authorization_revision_uidx
    UNIQUE (authorization_id, authorization_version, action, request_id)
);

CREATE OR REPLACE FUNCTION prevent_ferpa_authorization_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ferpa_authorization_revision is append-only';
END;
$$;
CREATE TRIGGER ferpa_authorization_revision_append_only
BEFORE UPDATE OR DELETE ON ferpa_authorization_revision
FOR EACH ROW EXECUTE FUNCTION prevent_ferpa_authorization_revision_mutation();

-- Upgrade historical core family_permissions screens that were intentionally
-- not materialized as requirements by the pre-FERPA journey publisher. This
-- makes the canonical task available on existing deployments without waiting
-- for staff to republish tenant configuration.
UPDATE requirement_definition_version definition
SET interaction_type='ferpa', submission_type='form', blocking=1,
    flow_kind='onboarding',
    input_config=jsonb_build_object(
      'signatureProvider','built_in',
      'portalScopes',jsonb_build_array(
        'onboarding','dashboard','enrollment','financials','classrooms',
        'campus_life','edward','documents','messages','appointments',
        'payments','profile','help'
      )
    ),
    updated_at=NOW()
WHERE definition.code='family_permissions'
  AND EXISTS (
    SELECT 1 FROM journey_requirement_definition link
    JOIN journey_definition_version journey_definition
      ON journey_definition.id=link.journey_definition_version_id
    WHERE link.requirement_definition_version_id=definition.id
      AND journey_definition.tenant_id=definition.tenant_id
      AND journey_definition.onboarding_required
  );

WITH eligible_tenant AS (
  SELECT DISTINCT definition.tenant_id
  FROM journey_definition_version definition
  WHERE definition.onboarding_required
    AND (
      definition.active=1
      OR EXISTS (
        SELECT 1 FROM enrollment_journey journey
        WHERE journey.journey_definition_version_id=definition.id
          AND journey.tenant_id=definition.tenant_id
          AND journey.status<>'cancelled'
      )
    )
), missing_definition AS (
  SELECT eligible.tenant_id,
         COALESCE((
           SELECT MAX(existing.version)
           FROM requirement_definition_version existing
           WHERE existing.tenant_id=eligible.tenant_id
             AND existing.code='family_permissions'
         ),0)+1 AS version
  FROM eligible_tenant eligible
  WHERE NOT EXISTS (
    SELECT 1 FROM requirement_definition_version existing
    WHERE existing.tenant_id=eligible.tenant_id
      AND existing.code='family_permissions'
      AND existing.interaction_type='ferpa'
  )
)
INSERT INTO requirement_definition_version (
  id, tenant_id, code, title, description, blocking, display_order,
  depends_on_codes, due_offset_days, version, submission_type,
  responsible_office, flow_kind, interaction_type, input_config, priority,
  activation_rules
)
SELECT gen_random_uuid(), tenant_id, 'family_permissions',
       'FERPA information release',
       'Sign the FERPA release and decide parent or guardian portal access.',
       1, 60, '{}', NULL, version, 'form', 'Registrar', 'onboarding', 'ferpa',
       jsonb_build_object(
         'signatureProvider','built_in',
         'portalScopes',jsonb_build_array(
           'onboarding','dashboard','enrollment','financials','classrooms',
           'campus_life','edward','documents','messages','appointments',
           'payments','profile','help'
         )
       ),
       100, '{"match":"all","rules":[]}'::jsonb
FROM missing_definition;

WITH target_journey_definition AS (
  SELECT DISTINCT definition.id, definition.tenant_id
  FROM journey_definition_version definition
  WHERE definition.onboarding_required
    AND (
      definition.active=1
      OR EXISTS (
        SELECT 1 FROM enrollment_journey journey
        WHERE journey.journey_definition_version_id=definition.id
          AND journey.tenant_id=definition.tenant_id
          AND journey.status<>'cancelled'
      )
    )
), canonical_definition AS (
  SELECT DISTINCT ON (tenant_id) tenant_id, id
  FROM requirement_definition_version
  WHERE code='family_permissions' AND interaction_type='ferpa'
  ORDER BY tenant_id, version DESC, id DESC
)
INSERT INTO journey_requirement_definition (
  journey_definition_version_id, requirement_definition_version_id
)
SELECT journey.id, canonical.id
FROM target_journey_definition journey
JOIN canonical_definition canonical ON canonical.tenant_id=journey.tenant_id
WHERE NOT EXISTS (
  SELECT 1
  FROM journey_requirement_definition link
  JOIN requirement_definition_version existing
    ON existing.id=link.requirement_definition_version_id
  WHERE link.journey_definition_version_id=journey.id
    AND existing.code='family_permissions'
)
ON CONFLICT DO NOTHING;

-- Establish an immutable baseline after every legacy field, signature, and
-- contact has been normalized. No raw token or signature pixels enter history.
INSERT INTO ferpa_authorization_revision (
  id, tenant_id, authorization_id, authorization_version,
  actor_type, actor_id, action, snapshot, request_id
)
SELECT gen_random_uuid(), ferpa_auth.tenant_id, ferpa_auth.id,
       ferpa_auth.version, 'system', ferpa_auth.student_id,
       'backfilled',
       jsonb_build_object(
         'authorizationId',ferpa_auth.id,
         'status',ferpa_auth.status,
         'accessDecision',ferpa_auth.access_decision,
         'version',ferpa_auth.version,
         'requirement',jsonb_build_object(
           'id',requirement.id,
           'code',definition.code,
           'definitionVersionId',definition.id,
           'version',requirement.version,
           'flowKind',definition.flow_kind,
           'status',requirement.status
         ),
         'signedDocument',CASE WHEN signed.id IS NULL THEN NULL ELSE
           jsonb_build_object(
             'id',signed.id,
             'sha256',signed.sha256,
             'requirementId',signed.requirement_id,
             'requirementVersion',signed.requirement_version,
             'signedAt',signed.signed_at
           )
         END,
         'delegates',COALESCE((
           SELECT jsonb_agg(
             jsonb_build_object(
               'id',delegate.id,
               'fullName',delegate.full_name,
               'relationship',delegate.relationship,
               'email',delegate.email_normalized,
               'scopes',to_jsonb(delegate.scopes),
               'legacyReviewRequired',delegate.legacy_review_required,
               'link',jsonb_build_object(
                 'status',COALESCE(link.status,'not_issued'),
                 'issuedAt',link.issued_at,
                 'rotatedAt',link.rotated_at,
                 'lastUsedAt',link.last_used_at,
                 'updatedAt',COALESCE(link.updated_at,delegate.updated_at)
               )
             ) ORDER BY delegate.display_order, delegate.id
           )
           FROM ferpa_delegate delegate
           LEFT JOIN ferpa_delegate_link link ON link.delegate_id=delegate.id
           WHERE delegate.authorization_id=ferpa_auth.id AND delegate.active
         ),'[]'::jsonb)
       ),
       'migration-0043-legacy-backfill'
FROM ferpa_authorization ferpa_auth
JOIN student_requirement requirement
  ON requirement.id=ferpa_auth.requirement_id
 AND requirement.tenant_id=ferpa_auth.tenant_id
JOIN requirement_definition_version definition
  ON definition.id=requirement.requirement_definition_version_id
 AND definition.tenant_id=requirement.tenant_id
LEFT JOIN LATERAL (
  SELECT document.id, document.sha256, document.requirement_id,
         document.requirement_version, document.signed_at
  FROM ferpa_signed_document document
  WHERE document.authorization_id=ferpa_auth.id
  LIMIT 1
) signed ON true
ON CONFLICT DO NOTHING;

INSERT INTO student_requirement (
  id, tenant_id, journey_id, requirement_definition_version_id,
  status, progress_percent, version
)
SELECT gen_random_uuid(), journey.tenant_id, journey.id, definition.id,
       'ready', 0, 1
FROM enrollment_journey journey
JOIN journey_requirement_definition link
  ON link.journey_definition_version_id=journey.journey_definition_version_id
JOIN requirement_definition_version definition
  ON definition.id=link.requirement_definition_version_id
 AND definition.tenant_id=journey.tenant_id
WHERE journey.status<>'cancelled'
  AND definition.code='family_permissions'
  AND definition.interaction_type='ferpa'
  AND NOT EXISTS (
    SELECT 1
    FROM student_requirement existing_requirement
    JOIN requirement_definition_version existing_definition
      ON existing_definition.id=existing_requirement.requirement_definition_version_id
    WHERE existing_requirement.tenant_id=journey.tenant_id
      AND existing_requirement.journey_id=journey.id
      AND existing_requirement.retired_at IS NULL
      AND existing_definition.code='family_permissions'
  )
ON CONFLICT DO NOTHING;

-- Materialize aggregates for a FERPA task already published before this
-- migration. Future publications perform the same INSERT in application code.
INSERT INTO ferpa_authorization (id, tenant_id, student_id, requirement_id)
SELECT DISTINCT ON (requirement.tenant_id, journey.student_id)
       gen_random_uuid(), requirement.tenant_id, journey.student_id, requirement.id
FROM student_requirement requirement
JOIN enrollment_journey journey
  ON journey.id=requirement.journey_id AND journey.tenant_id=requirement.tenant_id
JOIN requirement_definition_version definition
  ON definition.id=requirement.requirement_definition_version_id
 AND definition.tenant_id=requirement.tenant_id
WHERE (definition.interaction_type='ferpa' OR definition.code='family_permissions')
  AND requirement.retired_at IS NULL
ORDER BY requirement.tenant_id, journey.student_id,
         requirement.created_at DESC, requirement.id DESC
ON CONFLICT DO NOTHING;

-- Preserve an existing immutable FERPA signature where one exists. Contacts
-- are copied below without translating old record-category scopes into portal
-- page scopes, which would silently broaden disclosure.
INSERT INTO ferpa_signed_document (
  id, tenant_id, student_id, authorization_id, requirement_id,
  requirement_version, template_code, title, file_name, mime_type, size_bytes,
  storage_provider, storage_key, sha256, signer_name, signature_method, signed_at,
  created_at
)
SELECT DISTINCT ON (ferpa_auth.id)
       signed.id, signed.tenant_id, signed.student_id, ferpa_auth.id,
       ferpa_auth.requirement_id, requirement.version,
       signed.template_code, signed.title, signed.file_name, signed.mime_type,
       signed.size_bytes, signed.storage_provider, signed.storage_key, signed.sha256,
       signed.signer_name, signed.signature_method, signed.signed_at, signed.created_at
FROM student_signed_document signed
JOIN ferpa_authorization ferpa_auth
  ON ferpa_auth.tenant_id=signed.tenant_id
 AND ferpa_auth.student_id=signed.student_id
JOIN student_requirement requirement
  ON requirement.id=ferpa_auth.requirement_id
 AND requirement.tenant_id=ferpa_auth.tenant_id
WHERE signed.template_code='ferpa_release'
ORDER BY ferpa_auth.id, signed.signed_at DESC, signed.id DESC
ON CONFLICT DO NOTHING;

-- A completed legacy family-permissions step with no contacts is the only
-- safe automatic decision. Non-empty legacy contacts require a fresh student
-- choice of page scopes and receive no link until then.
UPDATE ferpa_authorization ferpa_auth
SET access_decision='no_access', updated_at=NOW()
FROM student_onboarding onboarding
WHERE onboarding.tenant_id=ferpa_auth.tenant_id
  AND onboarding.student_id=ferpa_auth.student_id
  AND 'family_permissions'=ANY(onboarding.completed_steps)
  AND jsonb_array_length(COALESCE(onboarding.payload->'familyPermissions','[]'::jsonb))=0;

INSERT INTO ferpa_delegate (
  id, tenant_id, student_id, authorization_id, full_name, relationship,
  email_normalized, scopes, display_order, active, legacy_review_required
)
SELECT DISTINCT ON (
         ferpa_auth.id, lower(left(permission->>'email',254))
       )
       gen_random_uuid(), ferpa_auth.tenant_id, ferpa_auth.student_id,
       ferpa_auth.id,
       left(COALESCE(permission->>'fullName','Legacy delegate'),160),
       CASE permission->>'relationship'
         WHEN 'parent' THEN 'parent'
         WHEN 'guardian' THEN 'guardian'
         WHEN 'partner' THEN 'partner'
         WHEN 'sponsor' THEN 'sponsor'
         ELSE 'other'
       END,
       lower(left(permission->>'email',254)), '{}', (ordinality-1)::smallint,
       true, true
FROM ferpa_authorization ferpa_auth
JOIN student_onboarding onboarding
  ON onboarding.tenant_id=ferpa_auth.tenant_id
 AND onboarding.student_id=ferpa_auth.student_id
CROSS JOIN LATERAL jsonb_array_elements(
  COALESCE(onboarding.payload->'familyPermissions','[]'::jsonb)
) WITH ORDINALITY AS legacy(permission, ordinality)
WHERE ordinality<=4 AND COALESCE(permission->>'email','')<>''
ORDER BY ferpa_auth.id, lower(left(permission->>'email',254)), ordinality
ON CONFLICT DO NOTHING;

-- Capture the fully normalized legacy state after evidence and contacts exist.
INSERT INTO ferpa_authorization_revision (
  id, tenant_id, authorization_id, authorization_version,
  actor_type, actor_id, action, snapshot, request_id
)
SELECT gen_random_uuid(), ferpa_auth.tenant_id, ferpa_auth.id,
       ferpa_auth.version, 'system', ferpa_auth.student_id, 'backfilled',
       jsonb_build_object(
         'authorizationId',ferpa_auth.id,
         'status',ferpa_auth.status,
         'accessDecision',ferpa_auth.access_decision,
         'version',ferpa_auth.version,
         'requirement',jsonb_build_object(
           'id',requirement.id,'code',definition.code,
           'definitionVersionId',definition.id,'version',requirement.version,
           'flowKind',definition.flow_kind,'status',requirement.status
         ),
         'signedDocument',CASE WHEN signed.id IS NULL THEN NULL ELSE
           jsonb_build_object(
             'id',signed.id,'sha256',signed.sha256,
             'requirementId',signed.requirement_id,
             'requirementVersion',signed.requirement_version,
             'signedAt',signed.signed_at
           )
         END,
         'delegates',COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
             'id',delegate.id,'fullName',delegate.full_name,
             'relationship',delegate.relationship,'email',delegate.email_normalized,
             'scopes',to_jsonb(delegate.scopes),
             'legacyReviewRequired',delegate.legacy_review_required,
             'link',jsonb_build_object(
               'status',COALESCE(link.status,'not_issued'),
               'issuedAt',link.issued_at,'rotatedAt',link.rotated_at,
               'lastUsedAt',link.last_used_at,
               'updatedAt',COALESCE(link.updated_at,delegate.updated_at)
             )
           ) ORDER BY delegate.display_order, delegate.id)
           FROM ferpa_delegate delegate
           LEFT JOIN ferpa_delegate_link link ON link.delegate_id=delegate.id
           WHERE delegate.authorization_id=ferpa_auth.id AND delegate.active
         ),'[]'::jsonb)
       ),
       'migration-0043-legacy-backfill'
FROM ferpa_authorization ferpa_auth
JOIN student_requirement requirement
  ON requirement.id=ferpa_auth.requirement_id
 AND requirement.tenant_id=ferpa_auth.tenant_id
JOIN requirement_definition_version definition
  ON definition.id=requirement.requirement_definition_version_id
 AND definition.tenant_id=requirement.tenant_id
LEFT JOIN LATERAL (
  SELECT document.id, document.sha256, document.requirement_id,
         document.requirement_version, document.signed_at
  FROM ferpa_signed_document document
  WHERE document.authorization_id=ferpa_auth.id
  LIMIT 1
) signed ON true
ON CONFLICT DO NOTHING;
