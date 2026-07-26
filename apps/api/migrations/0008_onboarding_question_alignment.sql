-- Align the canonical onboarding sequence with the product flow. Identity,
-- health, accessibility, and document collection remain enrollment tasks and
-- are intentionally not part of first-session onboarding.

UPDATE student_onboarding
SET current_step = 'family_permissions'
WHERE current_step = 'other_records';

UPDATE student_onboarding
SET completed_steps = array_remove(completed_steps, 'other_records'),
    payload = (
      payload
      - 'legalNameConfirmed'
      - 'contactInformationConfirmed'
      - 'homeAddressConfirmed'
      - 'emergencyContactConfirmed'
      - 'recordsConfirmed'
      - 'familyPermissionsReviewed'
      - 'signatureConfirmed'
      - 'depositAcknowledged'
    ) || CASE
      WHEN jsonb_typeof(payload -> 'skippedSteps') = 'array'
      THEN jsonb_build_object(
        'skippedSteps',
        (
          SELECT COALESCE(jsonb_agg(item.value), '[]'::jsonb)
          FROM jsonb_array_elements(payload -> 'skippedSteps') AS item(value)
          WHERE item.value <> '"other_records"'::jsonb
        )
      )
      ELSE '{}'::jsonb
    END;

ALTER TABLE student_onboarding
  DROP CONSTRAINT IF EXISTS student_onboarding_current_step_check;

ALTER TABLE student_onboarding
  ADD CONSTRAINT student_onboarding_current_step_check
  CHECK (current_step IN (
    'offer',
    'about_you',
    'housing',
    'campus_life',
    'emergency_contacts',
    'family_permissions',
    'review_and_sign',
    'deposit'
  ));
