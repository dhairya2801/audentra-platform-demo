-- Forward integration migration. No earlier migration is renumbered or rewritten.
CREATE TABLE university.rate_catalog (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('tuition','housing','meal','insurance','fee')),
 name TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),
 period TEXT NOT NULL CHECK(period IN ('term','year','once')),
 effective_from TEXT NOT NULL, effective_until TEXT NOT NULL,
 policy_id TEXT NOT NULL , eligibility TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}'
, PRIMARY KEY(tenant_id,id));
ALTER TABLE university.rate_catalog ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.rate_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.rate_catalog FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.rate_catalog USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.financial_plan_input (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 student_id TEXT NOT NULL , term_id TEXT NOT NULL ,
 version INTEGER NOT NULL CHECK(version>0), inputs_json TEXT NOT NULL,
 updated_at TEXT NOT NULL, provenance TEXT NOT NULL CHECK(provenance='student_entered'),
 PRIMARY KEY(tenant_id,student_id,term_id)
);
ALTER TABLE university.financial_plan_input ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.financial_plan_input ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.financial_plan_input ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.financial_plan_input FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.financial_plan_input USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.financial_scenario (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT NOT NULL, student_id TEXT NOT NULL , term_id TEXT NOT NULL ,
 name TEXT NOT NULL, inputs_json TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
, PRIMARY KEY(tenant_id,id));
ALTER TABLE university.financial_scenario ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.financial_scenario ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.financial_scenario ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.financial_scenario FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.financial_scenario USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.meal_enrollment (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT NOT NULL, student_id TEXT NOT NULL , term_id TEXT NOT NULL ,
 catalog_id TEXT NOT NULL ,
 status TEXT NOT NULL CHECK(status IN ('requested','approved','active','cancelled')),
 version INTEGER NOT NULL CHECK(version>0), updated_at TEXT NOT NULL,
 UNIQUE(tenant_id,student_id,term_id)
, PRIMARY KEY(tenant_id,id));
ALTER TABLE university.meal_enrollment ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.meal_enrollment ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.meal_enrollment ADD FOREIGN KEY(tenant_id,catalog_id) REFERENCES university.rate_catalog(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.meal_enrollment ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.meal_enrollment FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.meal_enrollment USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.insurance_coverage (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT NOT NULL, student_id TEXT NOT NULL , term_id TEXT NOT NULL ,
 catalog_id TEXT NOT NULL ,
 status TEXT NOT NULL CHECK(status IN ('enrolled','waiver_submitted','waiver_approved','waiver_rejected','cancelled')),
 evidence_document_id TEXT , version INTEGER NOT NULL CHECK(version>0), updated_at TEXT NOT NULL,
 UNIQUE(tenant_id,student_id,term_id)
, PRIMARY KEY(tenant_id,id));
ALTER TABLE university.insurance_coverage ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.insurance_coverage ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.insurance_coverage ADD FOREIGN KEY(tenant_id,catalog_id) REFERENCES university.rate_catalog(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.insurance_coverage ADD FOREIGN KEY(tenant_id,evidence_document_id) REFERENCES university.document(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.insurance_coverage ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.insurance_coverage FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.insurance_coverage USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.payment_agreement (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT NOT NULL, student_id TEXT NOT NULL , term_id TEXT NOT NULL ,
 principal_cents INTEGER NOT NULL CHECK(principal_cents>0), fee_cents INTEGER NOT NULL CHECK(fee_cents>=0),
 status TEXT NOT NULL CHECK(status IN ('proposed','signed','active','completed','cancelled','defaulted')),
 signed_at TEXT, version INTEGER NOT NULL CHECK(version>0), terms_policy_id TEXT NOT NULL 
, PRIMARY KEY(tenant_id,id));
ALTER TABLE university.payment_agreement ADD FOREIGN KEY(tenant_id,student_id) REFERENCES university.student(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.payment_agreement ADD FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.payment_agreement ADD FOREIGN KEY(tenant_id,terms_policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.payment_agreement ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.payment_agreement FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.payment_agreement USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.payment_installment (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 id TEXT NOT NULL, agreement_id TEXT NOT NULL ,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), due_at TEXT NOT NULL,
 payment_id TEXT , UNIQUE(tenant_id,agreement_id,due_at)
, PRIMARY KEY(tenant_id,id));
ALTER TABLE university.payment_installment ADD FOREIGN KEY(tenant_id,agreement_id) REFERENCES university.payment_agreement(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.payment_installment ADD FOREIGN KEY(tenant_id,payment_id) REFERENCES university.payment(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.payment_installment ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.payment_installment FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.payment_installment USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.loan_terms (tenant_id uuid NOT NULL REFERENCES public.tenant(id), 
 fund_id TEXT NOT NULL , interest_basis_points INTEGER CHECK(interest_basis_points>=0),
 fee_basis_points INTEGER CHECK(fee_basis_points>=0), term_months INTEGER CHECK(term_months>0),
 policy_id TEXT NOT NULL , effective_from TEXT NOT NULL, effective_until TEXT NOT NULL
, PRIMARY KEY(tenant_id,fund_id));
ALTER TABLE university.loan_terms ADD FOREIGN KEY(tenant_id,policy_id) REFERENCES university.policy(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE university.loan_terms ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.loan_terms FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.loan_terms USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
CREATE TABLE university.planning_receipt (tenant_id uuid NOT NULL REFERENCES public.tenant(id), actor_id uuid NOT NULL, idempotency_key text NOT NULL, payload_hash text NOT NULL, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,actor_id,idempotency_key));
ALTER TABLE university.planning_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.planning_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.planning_receipt USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
