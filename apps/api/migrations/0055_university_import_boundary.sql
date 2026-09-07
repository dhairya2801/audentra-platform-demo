-- Bulk bootstrap imports already contain these links; do not mint duplicate
-- records while projecting the seed into the operational schema.
DROP TRIGGER university_document_insert ON document_record;
CREATE TRIGGER university_document_insert AFTER INSERT ON document_record
 FOR EACH ROW WHEN (coalesce(current_setting('audentra.university_import',true),'')<>'on')
 EXECUTE FUNCTION university.insert_document();
DROP TRIGGER university_appointment_insert ON student_appointment;
CREATE TRIGGER university_appointment_insert AFTER INSERT ON student_appointment
 FOR EACH ROW WHEN (coalesce(current_setting('audentra.university_import',true),'')<>'on')
 EXECUTE FUNCTION university.insert_appointment();
DROP TRIGGER university_deposit_insert ON payment_transaction;
CREATE TRIGGER university_deposit_insert AFTER INSERT ON payment_transaction
 FOR EACH ROW WHEN (coalesce(current_setting('audentra.university_import',true),'')<>'on')
 EXECUTE FUNCTION university.post_deposit();
