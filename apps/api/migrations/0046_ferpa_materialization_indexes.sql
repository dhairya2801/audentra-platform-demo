-- FERPA authorization materialization begins from the small set of current
-- FERPA task definitions, then joins into the tenant's student journeys. These
-- indexes keep configuration publication bounded for large populations.

CREATE INDEX requirement_definition_ferpa_code_idx
  ON requirement_definition_version(tenant_id, code, id)
  WHERE interaction_type='ferpa';

CREATE INDEX journey_requirement_definition_requirement_idx
  ON journey_requirement_definition(
    requirement_definition_version_id,
    journey_definition_version_id
  );

CREATE INDEX enrollment_journey_definition_student_idx
  ON enrollment_journey(
    tenant_id,
    journey_definition_version_id,
    student_id,
    id
  );
