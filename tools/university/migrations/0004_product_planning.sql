-- Published institutional catalog and student-authored planning are different facts.
CREATE TABLE rate_catalog (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('tuition','housing','meal','insurance','fee')),
 name TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),
 period TEXT NOT NULL CHECK(period IN ('term','year','once')),
 effective_from TEXT NOT NULL, effective_until TEXT NOT NULL,
 policy_id TEXT NOT NULL REFERENCES policy, eligibility TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE financial_plan_input (
 student_id TEXT NOT NULL REFERENCES student, term_id TEXT NOT NULL REFERENCES term,
 version INTEGER NOT NULL CHECK(version>0), inputs_json TEXT NOT NULL,
 updated_at TEXT NOT NULL, provenance TEXT NOT NULL CHECK(provenance='student_entered'),
 PRIMARY KEY(student_id,term_id)
);
CREATE TABLE financial_scenario (
 id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES student, term_id TEXT NOT NULL REFERENCES term,
 name TEXT NOT NULL, inputs_json TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE meal_enrollment (
 id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES student, term_id TEXT NOT NULL REFERENCES term,
 catalog_id TEXT NOT NULL REFERENCES rate_catalog,
 status TEXT NOT NULL CHECK(status IN ('requested','approved','active','cancelled')),
 version INTEGER NOT NULL CHECK(version>0), updated_at TEXT NOT NULL,
 UNIQUE(student_id,term_id)
);
CREATE TABLE insurance_coverage (
 id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES student, term_id TEXT NOT NULL REFERENCES term,
 catalog_id TEXT NOT NULL REFERENCES rate_catalog,
 status TEXT NOT NULL CHECK(status IN ('enrolled','waiver_submitted','waiver_approved','waiver_rejected','cancelled')),
 evidence_document_id TEXT REFERENCES document, version INTEGER NOT NULL CHECK(version>0), updated_at TEXT NOT NULL,
 UNIQUE(student_id,term_id)
);
CREATE TABLE payment_agreement (
 id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES student, term_id TEXT NOT NULL REFERENCES term,
 principal_cents INTEGER NOT NULL CHECK(principal_cents>0), fee_cents INTEGER NOT NULL CHECK(fee_cents>=0),
 status TEXT NOT NULL CHECK(status IN ('proposed','signed','active','completed','cancelled','defaulted')),
 signed_at TEXT, version INTEGER NOT NULL CHECK(version>0), terms_policy_id TEXT NOT NULL REFERENCES policy
);
CREATE TABLE payment_installment (
 id TEXT PRIMARY KEY, agreement_id TEXT NOT NULL REFERENCES payment_agreement,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), due_at TEXT NOT NULL,
 payment_id TEXT REFERENCES payment, UNIQUE(agreement_id,due_at)
);
CREATE TABLE loan_terms (
 fund_id TEXT PRIMARY KEY REFERENCES fund, interest_basis_points INTEGER CHECK(interest_basis_points>=0),
 fee_basis_points INTEGER CHECK(fee_basis_points>=0), term_months INTEGER CHECK(term_months>0),
 policy_id TEXT NOT NULL REFERENCES policy, effective_from TEXT NOT NULL, effective_until TEXT NOT NULL
);
