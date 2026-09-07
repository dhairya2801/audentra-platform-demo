-- V3 relational world, tenant-scoped alongside the canonical product runtime.

CREATE SCHEMA university;

CREATE TABLE university.action_receipt (tenant_id uuid NOT NULL REFERENCES public.tenant(id), idempotency_key TEXT, request_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(tenant_id,idempotency_key));

CREATE TABLE university.application (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , term_id TEXT NOT NULL , status TEXT NOT NULL, submitted_at TEXT NOT NULL, decided_at TEXT, respond_by TEXT, CHECK(decided_at IS NULL OR decided_at>=submitted_at), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.appointment (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , staff_id TEXT NOT NULL , starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('scheduled','completed','cancelled','no_show')), purpose TEXT NOT NULL, CHECK(ends_at>starts_at), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.assignment (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , staff_id TEXT NOT NULL , role TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT, reason TEXT NOT NULL, CHECK(ends_at IS NULL OR ends_at>starts_at), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.award (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , fund_id TEXT NOT NULL , aid_year TEXT NOT NULL, offered_cents INTEGER NOT NULL CHECK(offered_cents>=0), accepted_cents INTEGER NOT NULL CHECK(accepted_cents BETWEEN 0 AND offered_cents), status TEXT NOT NULL CHECK(status IN ('offered','accepted','declined')), UNIQUE(tenant_id,student_id,fund_id,aid_year), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.bed (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, residence_id TEXT NOT NULL , room TEXT NOT NULL, accessible INTEGER NOT NULL CHECK(accessible IN (0,1)), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.calendar (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, term_label TEXT NOT NULL, title TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT, office_id TEXT NOT NULL , policy_id TEXT NOT NULL , audience TEXT NOT NULL, category TEXT NOT NULL, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.communication (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , workflow_id TEXT , channel TEXT NOT NULL, direction TEXT NOT NULL, delivery TEXT NOT NULL CHECK(delivery IN ('delivered','bounced','recorded','queued')), audience TEXT NOT NULL CHECK(audience IN ('student','internal')), sent_at TEXT NOT NULL, body TEXT NOT NULL, authoritative INTEGER NOT NULL CHECK(authoritative IN (0,1)), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.consent (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , delegate_name TEXT NOT NULL, scope TEXT NOT NULL, starts_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT, CHECK(expires_at>starts_at), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.course (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, code TEXT NOT NULL, title TEXT NOT NULL, credits INTEGER NOT NULL CHECK(credits BETWEEN 0 AND 6), level INTEGER NOT NULL, description TEXT NOT NULL, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,code));

CREATE TABLE university.disbursement (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, award_id TEXT NOT NULL , term_id TEXT NOT NULL , amount_cents INTEGER NOT NULL CHECK(amount_cents>0), status TEXT NOT NULL CHECK(status IN ('scheduled','held','posted','reversed')), scheduled_at TEXT NOT NULL, posted_at TEXT, reason TEXT, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.document (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , category TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('NOT_SUBMITTED','UPLOADED','UNDER_REVIEW','ACCEPTED','REJECTED','EXPIRED','WAIVED','NEEDS_RESUBMISSION')), office_id TEXT NOT NULL , version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.document_revision (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, document_id TEXT NOT NULL , revision INTEGER NOT NULL, status TEXT NOT NULL, effective_at TEXT NOT NULL, recorded_at TEXT NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL, UNIQUE(tenant_id,document_id,revision), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.enrollment (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , section_id TEXT NOT NULL , status TEXT NOT NULL CHECK(status IN ('enrolled','dropped','withdrawn','completed')), enrolled_at TEXT NOT NULL, ended_at TEXT, grade TEXT, version INTEGER NOT NULL DEFAULT 1, UNIQUE(tenant_id,student_id,section_id), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.event (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, effective_at TEXT NOT NULL, recorded_at TEXT NOT NULL, actor TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL, description TEXT NOT NULL, visibility TEXT NOT NULL CHECK(visibility IN ('student','internal')), correlation_id TEXT NOT NULL, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.exception (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , policy_id TEXT NOT NULL , kind TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('requested','approved','denied','expired')), office_id TEXT NOT NULL , approver_id TEXT , starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, reason TEXT NOT NULL, CHECK(ends_at>starts_at), CHECK(status<>'approved' OR approver_id IS NOT NULL), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.fund (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, name TEXT NOT NULL, source TEXT NOT NULL, annual_cap_cents INTEGER NOT NULL CHECK(annual_cap_cents>0), posts_to_account INTEGER NOT NULL CHECK(posts_to_account IN (0,1)), international_eligible INTEGER NOT NULL CHECK(international_eligible IN (0,1)), policy_id TEXT NOT NULL, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.hold (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , term_id TEXT NOT NULL DEFAULT '2026FA' , kind TEXT NOT NULL, office_id TEXT NOT NULL , blocks_registration INTEGER NOT NULL CHECK(blocks_registration IN (0,1)), placed_at TEXT NOT NULL, released_at TEXT, reason TEXT NOT NULL, policy_id TEXT NOT NULL , version INTEGER NOT NULL DEFAULT 1, CHECK(released_at IS NULL OR released_at>=placed_at), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.housing (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , term_id TEXT NOT NULL , bed_id TEXT , status TEXT NOT NULL CHECK(status IN ('assigned','waitlisted','exempt','cancelled')), starts_at TEXT NOT NULL, ends_at TEXT, reason TEXT NOT NULL, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.ledger (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , term_id TEXT NOT NULL , kind TEXT NOT NULL CHECK(kind IN ('charge','payment','aid','refund','reversal','credit_adjustment')), amount_cents INTEGER NOT NULL, posted_at TEXT NOT NULL, due_at TEXT, payment_id TEXT , disbursement_id TEXT , reverses_id TEXT , description TEXT NOT NULL, CHECK((kind IN ('charge','refund','reversal') AND amount_cents>0) OR (kind IN ('payment','aid','credit_adjustment') AND amount_cents<0)), PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,reverses_id));

CREATE TABLE university.meta (tenant_id uuid NOT NULL REFERENCES public.tenant(id), key TEXT, value TEXT NOT NULL, PRIMARY KEY(tenant_id,key));

CREATE TABLE university.office (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, name TEXT NOT NULL, location TEXT, email TEXT, sla_days INTEGER, description TEXT, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.payment (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , term_id TEXT NOT NULL , amount_cents INTEGER NOT NULL CHECK(amount_cents>0), status TEXT NOT NULL CHECK(status IN ('pending','posted','failed','reversed')), submitted_at TEXT NOT NULL, settled_at TEXT, method TEXT NOT NULL, idempotency_key TEXT NOT NULL, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,idempotency_key));

CREATE TABLE university.policy (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, code TEXT NOT NULL, version TEXT NOT NULL, title TEXT NOT NULL, owner_id TEXT NOT NULL , audience TEXT NOT NULL CHECK(audience IN ('student','internal','all')), authority TEXT NOT NULL CHECK(authority IN ('policy','procedure','guide','advisory')), effective_from TEXT NOT NULL, effective_until TEXT, published_at TEXT NOT NULL, supersedes_id TEXT , applies_json TEXT NOT NULL, body TEXT NOT NULL, source_path TEXT NOT NULL, content_hash TEXT NOT NULL, UNIQUE(tenant_id,code,version), CHECK(effective_until IS NULL OR effective_until>effective_from), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.policy_link (tenant_id uuid NOT NULL REFERENCES public.tenant(id), policy_id TEXT NOT NULL , related_id TEXT NOT NULL , PRIMARY KEY(tenant_id,policy_id,related_id));

CREATE TABLE university.prerequisite (tenant_id uuid NOT NULL REFERENCES public.tenant(id), course_id TEXT NOT NULL , required_course_id TEXT NOT NULL , minimum_grade TEXT NOT NULL, CHECK(course_id<>required_course_id), PRIMARY KEY(tenant_id,course_id,required_course_id));

CREATE TABLE university.program (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, name TEXT NOT NULL, department TEXT NOT NULL, degree_credits INTEGER NOT NULL, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.requirement (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, program_id TEXT NOT NULL , course_id TEXT , category TEXT NOT NULL, recommended_term INTEGER, credits INTEGER NOT NULL CHECK(credits>=0), description TEXT NOT NULL, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.residence (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, name TEXT NOT NULL, style TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity>0), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.sap_evaluation (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , term_id TEXT NOT NULL , attempted_credits INTEGER NOT NULL CHECK(attempted_credits>=0), earned_credits INTEGER NOT NULL CHECK(earned_credits BETWEEN 0 AND attempted_credits), gpa REAL, completion_rate REAL, maximum_attempted_credits INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('meeting','warning','suspension','not_evaluated')), previous_id TEXT , evaluated_at TEXT NOT NULL, policy_id TEXT NOT NULL , UNIQUE(tenant_id,student_id,term_id), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.section (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, course_id TEXT NOT NULL , term_id TEXT NOT NULL , label TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity>0), weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6), start_minute INTEGER NOT NULL, end_minute INTEGER NOT NULL, room TEXT NOT NULL, modality TEXT NOT NULL CHECK(modality IN ('in_person','online')), status TEXT NOT NULL CHECK(status IN ('open','cancelled')), CHECK(end_minute>start_minute), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.source_issue (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, severity TEXT NOT NULL, domain TEXT NOT NULL, title TEXT NOT NULL, finding TEXT NOT NULL, resolution TEXT NOT NULL, intentional INTEGER NOT NULL CHECK(intentional IN (0,1)), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.staff (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, name TEXT NOT NULL, office_id TEXT NOT NULL , title TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','leave','departed')), manager_id TEXT , capacity INTEGER NOT NULL CHECK(capacity>0), department TEXT, email TEXT NOT NULL, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.staff_absence (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, staff_id TEXT NOT NULL , starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, covering_staff_id TEXT NOT NULL , reason TEXT NOT NULL, CHECK(ends_at>starts_at), CHECK(staff_id<>covering_staff_id), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.staff_availability (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT, staff_id TEXT NOT NULL ,
 weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
 start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
 end_minute INTEGER NOT NULL CHECK(end_minute BETWEEN 1 AND 1440),
 timezone TEXT NOT NULL, location TEXT NOT NULL,
 CHECK(end_minute>start_minute), UNIQUE(tenant_id,staff_id,weekday,start_minute), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.staff_calendar_event (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT, staff_id TEXT NOT NULL ,
 title TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
 location TEXT NOT NULL, blocks_bookings INTEGER NOT NULL CHECK(blocks_bookings IN (0,1)),
 CHECK(ends_at>starts_at), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.step_dependency (tenant_id uuid NOT NULL REFERENCES public.tenant(id), step_id TEXT NOT NULL , prerequisite_id TEXT NOT NULL , CHECK(step_id<>prerequisite_id), PRIMARY KEY(tenant_id,step_id,prerequisite_id));

CREATE TABLE university.student (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, external_ref TEXT NOT NULL, name TEXT NOT NULL, preferred_name TEXT NOT NULL, email TEXT NOT NULL, program_id TEXT NOT NULL , admit_term TEXT NOT NULL , residency TEXT NOT NULL CHECK(residency IN ('in_state','out_of_state','international')), admit_type TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('applicant','admitted','enrolled','leave','withdrawn','denied')), birth_date TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,external_ref), UNIQUE(tenant_id,email));

CREATE TABLE university.term (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, name TEXT NOT NULL, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL, registration_opens TEXT NOT NULL, add_drop_at TEXT NOT NULL, census_at TEXT NOT NULL, CHECK(ends_on>starts_on), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.transfer_credit (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , course_id TEXT NOT NULL , institution TEXT NOT NULL, credits INTEGER NOT NULL, grade TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected')), received_at TEXT NOT NULL, evaluated_at TEXT, evaluator_id TEXT, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.waitlist (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , section_id TEXT NOT NULL , position INTEGER NOT NULL CHECK(position>0), status TEXT NOT NULL CHECK(status IN ('waiting','offered','accepted','expired')), offered_at TEXT, expires_at TEXT, UNIQUE(tenant_id,section_id,position), PRIMARY KEY(tenant_id,id));

CREATE TABLE university.workflow (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, student_id TEXT NOT NULL , title TEXT NOT NULL, kind TEXT NOT NULL, office_id TEXT NOT NULL , owner_id TEXT NOT NULL , status TEXT NOT NULL CHECK(status IN ('open','waiting','resolved','cancelled')), opened_at TEXT NOT NULL, due_at TEXT NOT NULL, resolved_at TEXT, policy_id TEXT NOT NULL , version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(tenant_id,id));

CREATE TABLE university.workflow_step (tenant_id uuid NOT NULL REFERENCES public.tenant(id), id TEXT, workflow_id TEXT NOT NULL , title TEXT NOT NULL, office_id TEXT NOT NULL , status TEXT NOT NULL CHECK(status IN ('blocked','ready','complete','waived')), completed_at TEXT, evidence TEXT, CHECK(status NOT IN ('complete','waived') OR evidence IS NOT NULL), PRIMARY KEY(tenant_id,id));

ALTER TABLE university.application ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.application ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.appointment ADD FOREIGN KEY(tenant_id,staff_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.appointment ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.assignment ADD FOREIGN KEY(tenant_id,staff_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.assignment ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.award ADD FOREIGN KEY(tenant_id,fund_id) REFERENCES university.fund(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.award ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.bed ADD FOREIGN KEY(tenant_id,residence_id) REFERENCES university.residence(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.calendar ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.calendar ADD FOREIGN KEY(tenant_id,office_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.communication ADD FOREIGN KEY(tenant_id,workflow_id) REFERENCES university.workflow(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.communication ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.consent ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.disbursement ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.disbursement ADD FOREIGN KEY(tenant_id,award_id) REFERENCES university.award(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.document ADD FOREIGN KEY(tenant_id,office_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.document ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.document_revision ADD FOREIGN KEY(tenant_id,document_id) REFERENCES university.document(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.enrollment ADD FOREIGN KEY(tenant_id,section_id) REFERENCES university.section(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.enrollment ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.event ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.exception ADD FOREIGN KEY(tenant_id,approver_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.exception ADD FOREIGN KEY(tenant_id,office_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.exception ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.exception ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.fund ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.hold ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.hold ADD FOREIGN KEY(tenant_id,office_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.hold ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.hold ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.housing ADD FOREIGN KEY(tenant_id,bed_id) REFERENCES university.bed(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.housing ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.housing ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.ledger ADD FOREIGN KEY(tenant_id,reverses_id) REFERENCES university.ledger(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.ledger ADD FOREIGN KEY(tenant_id,disbursement_id) REFERENCES university.disbursement(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.ledger ADD FOREIGN KEY(tenant_id,payment_id) REFERENCES university.payment(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.ledger ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.ledger ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.payment ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.payment ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.policy ADD FOREIGN KEY(tenant_id,supersedes_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.policy ADD FOREIGN KEY(tenant_id,owner_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.policy_link ADD FOREIGN KEY(tenant_id,related_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.policy_link ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.prerequisite ADD FOREIGN KEY(tenant_id,required_course_id) REFERENCES university.course(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.prerequisite ADD FOREIGN KEY(tenant_id,course_id) REFERENCES university.course(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.requirement ADD FOREIGN KEY(tenant_id,course_id) REFERENCES university.course(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.requirement ADD FOREIGN KEY(tenant_id,program_id) REFERENCES university.program(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.sap_evaluation ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.sap_evaluation ADD FOREIGN KEY(tenant_id,previous_id) REFERENCES university.sap_evaluation(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.sap_evaluation ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.sap_evaluation ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.section ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.section ADD FOREIGN KEY(tenant_id,course_id) REFERENCES university.course(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.staff ADD FOREIGN KEY(tenant_id,manager_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.staff ADD FOREIGN KEY(tenant_id,office_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.staff_absence ADD FOREIGN KEY(tenant_id,covering_staff_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.staff_absence ADD FOREIGN KEY(tenant_id,staff_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.staff_availability ADD FOREIGN KEY(tenant_id,staff_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.staff_calendar_event ADD FOREIGN KEY(tenant_id,staff_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.step_dependency ADD FOREIGN KEY(tenant_id,prerequisite_id) REFERENCES university.workflow_step(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.step_dependency ADD FOREIGN KEY(tenant_id,step_id) REFERENCES university.workflow_step(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.student ADD FOREIGN KEY(tenant_id,admit_term) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.student ADD FOREIGN KEY(tenant_id,program_id) REFERENCES university.program(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.transfer_credit ADD FOREIGN KEY(tenant_id,evaluator_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.transfer_credit ADD FOREIGN KEY(tenant_id,course_id) REFERENCES university.course(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.transfer_credit ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.waitlist ADD FOREIGN KEY(tenant_id,section_id) REFERENCES university.section(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.waitlist ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.workflow ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.workflow ADD FOREIGN KEY(tenant_id,owner_id) REFERENCES university.staff(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.workflow ADD FOREIGN KEY(tenant_id,office_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.workflow ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.workflow_step ADD FOREIGN KEY(tenant_id,office_id) REFERENCES university.office(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE university.workflow_step ADD FOREIGN KEY(tenant_id,workflow_id) REFERENCES university.workflow(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;

CREATE UNIQUE INDEX one_current_adviser ON university.assignment(tenant_id,student_id,role) WHERE ends_at IS NULL;

CREATE UNIQUE INDEX payment_posts_once ON university.ledger(tenant_id,payment_id) WHERE kind='payment';

CREATE UNIQUE INDEX aid_posts_once ON university.ledger(tenant_id,disbursement_id) WHERE kind='aid';

CREATE UNIQUE INDEX occupied_bed ON university.housing(tenant_id,bed_id,term_id) WHERE status='assigned' AND ends_at IS NULL;

CREATE UNIQUE INDEX one_housing ON university.housing(tenant_id,student_id,term_id) WHERE ends_at IS NULL;

CREATE INDEX event_student_clock ON university.event(tenant_id,student_id,recorded_at,effective_at);

CREATE INDEX enrollment_student ON university.enrollment(tenant_id,student_id,status);

CREATE INDEX ledger_student ON university.ledger(tenant_id,student_id,posted_at);

CREATE INDEX workflow_due ON university.workflow(tenant_id,status,due_at);

CREATE INDEX document_student ON university.document(tenant_id,student_id);

CREATE INDEX hold_student ON university.hold(tenant_id,student_id,released_at);

CREATE INDEX assignment_staff ON university.assignment(tenant_id,staff_id,ends_at);

CREATE INDEX staff_calendar_time ON university.staff_calendar_event(tenant_id,staff_id,starts_at,ends_at);

CREATE VIEW university.account_balance AS SELECT tenant_id,student_id,term_id,SUM(amount_cents)::bigint AS balance_cents FROM university.ledger GROUP BY tenant_id,student_id,term_id;

CREATE VIEW university.current_load AS SELECT e.tenant_id,e.student_id,s.term_id,SUM(c.credits)::integer AS credits FROM university.enrollment e JOIN university.section s ON s.id=e.section_id AND s.tenant_id=e.tenant_id JOIN university.course c ON c.id=s.course_id AND c.tenant_id=e.tenant_id WHERE e.status='enrolled' GROUP BY e.tenant_id,e.student_id,s.term_id;

ALTER TABLE university.meta ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.meta FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.meta USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.office ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.office FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.office USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.staff ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.staff FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.staff USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.staff_absence ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.staff_absence FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.staff_absence USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.term ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.term FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.term USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.program ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.program FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.program USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.student ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.student FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.student USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.application ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.application FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.application USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.assignment ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.assignment FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.assignment USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.course ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.course FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.course USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.prerequisite ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.prerequisite FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.prerequisite USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.requirement ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.requirement FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.requirement USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.section ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.section FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.section USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.enrollment ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.enrollment FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.enrollment USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.transfer_credit ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.transfer_credit FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.transfer_credit USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.sap_evaluation ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.sap_evaluation FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.sap_evaluation USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.waitlist ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.waitlist FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.waitlist USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.policy ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.policy FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.policy USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.policy_link ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.policy_link FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.policy_link USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.calendar ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.calendar FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.calendar USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.document ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.document FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.document USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.document_revision ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.document_revision FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.document_revision USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.fund ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.fund FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.fund USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.award ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.award FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.award USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.disbursement ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.disbursement FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.disbursement USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.payment ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.payment FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.payment USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.ledger ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.ledger FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.ledger USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.hold ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.hold FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.hold USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.residence ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.residence FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.residence USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.bed ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.bed FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.bed USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.housing ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.housing FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.housing USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.consent ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.consent FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.consent USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.exception ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.exception FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.exception USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.workflow ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.workflow FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.workflow USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.workflow_step ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.workflow_step FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.workflow_step USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.step_dependency ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.step_dependency FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.step_dependency USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.communication ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.communication FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.communication USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.appointment ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.appointment FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.appointment USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.event ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.event FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.event USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.source_issue ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.source_issue FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.source_issue USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.action_receipt ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.action_receipt FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.action_receipt USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.staff_availability ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.staff_availability FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.staff_availability USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

ALTER TABLE university.staff_calendar_event ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.staff_calendar_event FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.staff_calendar_event USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);

CREATE TABLE university.policy_section (tenant_id uuid NOT NULL REFERENCES public.tenant(id), policy_id text NOT NULL, ordinal integer NOT NULL, heading text NOT NULL, body text NOT NULL, search tsvector GENERATED ALWAYS AS (setweight(to_tsvector('english',heading),'A') || setweight(to_tsvector('english',body),'B')) STORED, PRIMARY KEY(tenant_id,policy_id,ordinal), FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id));

CREATE INDEX university_policy_search ON university.policy_section USING gin(search);

ALTER TABLE university.policy_section ENABLE ROW LEVEL SECURITY;

ALTER TABLE university.policy_section FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope ON university.policy_section USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);

CREATE TABLE university.runtime_link (tenant_id uuid NOT NULL REFERENCES public.tenant(id), kind text NOT NULL, world_id text NOT NULL, runtime_id uuid NOT NULL, PRIMARY KEY(tenant_id,kind,world_id), UNIQUE(tenant_id,kind,runtime_id));
