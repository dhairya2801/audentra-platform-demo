ALTER TABLE university.exception ADD COLUMN term_id text;
ALTER TABLE university.exception ADD COLUMN minimum_credits integer CHECK(minimum_credits>=0);
ALTER TABLE university.exception ADD COLUMN maximum_credits integer CHECK(maximum_credits>0);
ALTER TABLE university.exception ADD CONSTRAINT exception_term_fk
 FOREIGN KEY(tenant_id,term_id) REFERENCES university.term(tenant_id,id);
