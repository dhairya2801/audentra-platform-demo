-- Normalize historic onboarding contact spellings and publish a content-only
-- staff configuration revision. Parent/guardian contacts remain emergency
-- contacts until the student explicitly selects page scopes in FERPA.

WITH normalized AS (
  SELECT onboarding.tenant_id,
         onboarding.student_id,
         jsonb_agg(
           (
             contact.value - 'name' - 'phone'
             || CASE
                  WHEN COALESCE(contact.value->>'fullName', contact.value->>'name', '') <> ''
                  THEN jsonb_build_object(
                    'fullName', COALESCE(contact.value->>'fullName', contact.value->>'name')
                  )
                  ELSE '{}'::jsonb
                END
             || CASE
                  WHEN COALESCE(contact.value->>'mobilePhone', contact.value->>'phone', '') <> ''
                  THEN jsonb_build_object(
                    'mobilePhone', COALESCE(contact.value->>'mobilePhone', contact.value->>'phone')
                  )
                  ELSE '{}'::jsonb
                END
           )
           ORDER BY contact.ordinality
         ) AS contacts
  FROM student_onboarding onboarding
  CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(onboarding.payload->'emergencyContacts')='array'
      THEN onboarding.payload->'emergencyContacts' ELSE '[]'::jsonb END
  ) WITH ORDINALITY AS contact(value, ordinality)
  WHERE EXISTS (
    SELECT 1
    FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(onboarding.payload->'emergencyContacts')='array'
        THEN onboarding.payload->'emergencyContacts' ELSE '[]'::jsonb END
    ) AS existing(value)
    WHERE existing.value ? 'name' OR existing.value ? 'phone'
  )
  GROUP BY onboarding.tenant_id, onboarding.student_id
)
UPDATE student_onboarding onboarding
SET payload=jsonb_set(onboarding.payload, '{emergencyContacts}', normalized.contacts, true),
    version=onboarding.version+1,
    updated_at=NOW()
FROM normalized
WHERE onboarding.tenant_id=normalized.tenant_id
  AND onboarding.student_id=normalized.student_id;

-- Staff-managed configurations are immutable versions. Create a successor
-- rather than rewriting the published record so staff sees the same updated
-- onboarding journey that students receive, without changing task structure
-- or requirement materialization.
WITH changed AS (
  SELECT configuration.id,
         configuration.tenant_id,
         configuration.kind,
         configuration.version,
         configuration.yaml,
         configuration.record_count,
         configuration.created_by,
         jsonb_set(
           configuration.document,
           '{flows}',
           (
             SELECT jsonb_agg(
               CASE WHEN flow.value->>'kind'='onboarding' THEN
                 jsonb_set(
                   flow.value,
                   '{tasks}',
                   (
                     SELECT jsonb_agg(
                       CASE WHEN task.value->>'student_step'='emergency_contacts' THEN
                        task.value || jsonb_build_object(
                          'title', 'Add parent, guardian & emergency contacts',
                          'description', CASE
                            WHEN task.value->>'description' ILIKE '%College%' THEN
                              'Provide people the College may reach in an emergency. Include an email for any parent or guardian you may later select for FERPA portal access.'
                            ELSE
                              'Provide people the university may reach in an emergency. Include an email for any parent or guardian you may later select for FERPA portal access.'
                          END
                         )
                       ELSE task.value END
                       ORDER BY task.ordinality
                     )
                     FROM jsonb_array_elements(COALESCE(flow.value->'tasks', '[]'::jsonb))
                       WITH ORDINALITY AS task(value, ordinality)
                   ),
                   false
                 )
               ELSE flow.value END
               ORDER BY flow.ordinality
             )
             FROM jsonb_array_elements(COALESCE(configuration.document->'flows', '[]'::jsonb))
               WITH ORDINALITY AS flow(value, ordinality)
           ),
           false
         ) AS document
  FROM staff_managed_configuration_version configuration
  WHERE configuration.kind='journeys'
    AND configuration.active=true
    AND EXISTS (
      SELECT 1
      FROM jsonb_array_elements(COALESCE(configuration.document->'flows', '[]'::jsonb)) AS flow(value)
      CROSS JOIN LATERAL jsonb_array_elements(COALESCE(flow.value->'tasks', '[]'::jsonb)) AS task(value)
      WHERE flow.value->>'kind'='onboarding'
        AND task.value->>'student_step'='emergency_contacts'
        AND task.value->>'title'='Add emergency contacts'
    )
), deactivated AS (
  UPDATE staff_managed_configuration_version configuration
  SET active=false
  FROM changed
  WHERE configuration.id=changed.id
  RETURNING changed.*
)
INSERT INTO staff_managed_configuration_version (
  id, tenant_id, kind, version, yaml, document, record_count,
  change_summary, created_by, active, created_at, published_at
)
SELECT gen_random_uuid(),
       tenant_id,
       kind,
       version+1,
       regexp_replace(
         replace(
           yaml,
           'title: Add emergency contacts',
           'title: Add parent, guardian & emergency contacts'
         ),
         'description: Provide at least one contact[^\n]*',
         CASE
           WHEN yaml ILIKE '%description: Provide at least one contact the College%' THEN
             'description: Provide people the College may reach in an emergency. Include an email for any parent or guardian you may later select for FERPA portal access.'
           ELSE
             'description: Provide people the university may reach in an emergency. Include an email for any parent or guardian you may later select for FERPA portal access.'
         END
       ),
       document,
       record_count,
       'Clarified parent and guardian contact reuse for FERPA',
       created_by,
       true,
       NOW(),
       NOW()
FROM deactivated;
