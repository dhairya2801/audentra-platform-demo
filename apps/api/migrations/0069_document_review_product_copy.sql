-- Intentionally adapted from mainline d859e5d 0051; fresh integration migration.
-- Keep open document-review work student-centered and aligned with the current task UI.
WITH document_task_copy AS (
  SELECT
    work_item.id,
    work_item.tenant_id,
    COALESCE(
      NULLIF(split_part(BTRIM(profile.preferred_name), ' ', 1), ''),
      NULLIF(BTRIM(person.first_name), ''),
      'the student'
    ) AS student_name,
    CASE document.category
      WHEN 'consent' THEN 'FERPA Consent Form'
      WHEN 'financial_aid' THEN 'Financial Aid Document'
      WHEN 'health' THEN 'Immunization Record'
      WHEN 'identity' THEN 'Identity Document'
      WHEN 'immunization' THEN 'Immunization Record'
      WHEN 'residency' THEN 'Residency Document'
      WHEN 'transcript' THEN 'Transcript'
      WHEN 'other' THEN 'Supporting Document'
      ELSE INITCAP(REPLACE(document.category, '_', ' ')) || ' Document'
    END AS document_label
  FROM staff_work_item AS work_item
  JOIN document_record AS document
    ON document.id = work_item.source_id
   AND document.tenant_id = work_item.tenant_id
  JOIN student
    ON student.id = document.student_id AND student.tenant_id = document.tenant_id
  JOIN person
    ON person.id = student.person_id AND person.tenant_id = student.tenant_id
  LEFT JOIN student_profile AS profile
    ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
  WHERE work_item.source_type = 'document'
    AND work_item.status NOT IN ('done', 'cancelled')
)
UPDATE staff_work_item AS work_item
SET
  action_type = 'document_review',
  work_type = 'document_review',
  title = LEFT(
    'Review ' ||
    CASE
      WHEN copy.student_name = 'the student' THEN 'the student''s'
      ELSE copy.student_name || '''s'
    END ||
    ' ' || copy.document_label,
    240
  ),
  description = (
    CASE
      WHEN copy.student_name = 'the student' THEN 'The student'
      ELSE copy.student_name
    END ||
    ' is waiting for a quick review. Compare the document with the extracted details, '
    'then approve it or ask for an update.'
  ),
  version = work_item.version + 1,
  updated_at = NOW()
FROM document_task_copy AS copy
WHERE work_item.id = copy.id AND work_item.tenant_id = copy.tenant_id;
