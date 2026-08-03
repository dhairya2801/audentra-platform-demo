ALTER TABLE requirement_definition_version
  ADD COLUMN flow_kind varchar(24) NOT NULL DEFAULT 'enrollment'
    CHECK (flow_kind IN ('onboarding', 'enrollment')),
  ADD COLUMN interaction_type varchar(40) NOT NULL DEFAULT 'form'
    CHECK (interaction_type IN (
      'information',
      'approval',
      'form',
      'single_select',
      'multiple_select',
      'selection_flow',
      'upload_file',
      'signature',
      'payment',
      'scheduling'
    )),
  ADD COLUMN input_config jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE journey_definition_version
  ADD COLUMN onboarding_required boolean NOT NULL DEFAULT true;

ALTER TABLE student_requirement
  ADD COLUMN retired_at timestamptz,
  ADD COLUMN retired_reason varchar(24)
    CHECK (retired_reason IN ('inactive', 'deleted')),
  ADD CONSTRAINT student_requirement_retirement_check CHECK (
    (retired_at IS NULL AND retired_reason IS NULL)
    OR (retired_at IS NOT NULL AND retired_reason IS NOT NULL)
  );

CREATE INDEX student_requirement_current_flow_idx
  ON student_requirement(tenant_id, journey_id, created_at)
  WHERE retired_at IS NULL;

CREATE TABLE student_requirement_response (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  requirement_id uuid NOT NULL REFERENCES student_requirement(id),
  requirement_definition_version_id uuid NOT NULL
    REFERENCES requirement_definition_version(id),
  interaction_type varchar(40) NOT NULL CHECK (interaction_type IN (
    'information','approval','form','single_select','multiple_select',
    'selection_flow','signature','scheduling'
  )),
  response_data jsonb NOT NULL CHECK (jsonb_typeof(response_data) = 'object'),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  submitted_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_requirement_response_uidx
    UNIQUE (tenant_id, requirement_id, version)
);

CREATE INDEX student_requirement_response_student_idx
  ON student_requirement_response(tenant_id, student_id, submitted_at DESC);

UPDATE requirement_definition_version
SET interaction_type = CASE submission_type
  WHEN 'document' THEN 'upload_file'
  WHEN 'payment' THEN 'payment'
  WHEN 'appointment' THEN 'scheduling'
  WHEN 'none' THEN 'information'
  ELSE 'form'
END;

WITH managed_definition_occurrences AS (
  SELECT definition.id, definition.tenant_id, definition.code,
         row_number() OVER (
           PARTITION BY definition.tenant_id, definition.code
           ORDER BY journey.version, definition.version, definition.id
         ) AS occurrence
  FROM requirement_definition_version definition
  JOIN journey_requirement_definition link
    ON link.requirement_definition_version_id=definition.id
  JOIN journey_definition_version journey
    ON journey.id=link.journey_definition_version_id
   AND journey.tenant_id=definition.tenant_id
  WHERE journey.code='staff_managed_enrollment'
),
published_task_occurrences AS (
  SELECT configuration.tenant_id, task->>'id' AS code,
         flow->>'kind' AS flow_kind,
         row_number() OVER (
           PARTITION BY configuration.tenant_id, task->>'id'
           ORDER BY configuration.version
         ) AS occurrence
  FROM staff_managed_configuration_version configuration
  CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(configuration.document->'flows','[]'::jsonb)
  ) flow
  CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(flow->'tasks','[]'::jsonb)
  ) task
  WHERE configuration.kind='journeys'
    AND COALESCE(flow->>'status','published')='published'
    AND flow->>'kind' IN ('onboarding','enrollment')
    AND NOT (
      flow->>'kind'='onboarding'
      AND task->>'student_step' IN (
        'offer','about_you','housing','campus_life','emergency_contacts',
        'family_permissions','review_and_sign','deposit'
      )
    )
)
UPDATE requirement_definition_version definition
SET flow_kind=task.flow_kind
FROM managed_definition_occurrences managed
JOIN published_task_occurrences task
  ON task.tenant_id=managed.tenant_id
 AND task.code=managed.code
 AND task.occurrence=managed.occurrence
WHERE definition.id=managed.id;

UPDATE journey_definition_version
SET onboarding_required=false
WHERE code='system_zero_step_enrollment';
