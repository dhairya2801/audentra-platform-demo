-- Staff-initiated threads share the existing participant history and retention policy.
ALTER TABLE public.student_inquiry ADD COLUMN initiator_type text NOT NULL DEFAULT 'student'
  CHECK (initiator_type IN ('student','staff'));
