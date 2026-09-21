-- Explicit term allocations: an annual offer must never be guessed to be cash.
CREATE TABLE university.award_term (
    tenant_id uuid NOT NULL REFERENCES public.tenant(id),
    award_id text NOT NULL,
    term_id text NOT NULL,
    offered_cents integer NOT NULL CHECK (offered_cents >= 0),
    accepted_cents integer NOT NULL CHECK (accepted_cents BETWEEN 0 AND offered_cents),
    accepted_net_cents integer NOT NULL CHECK (accepted_net_cents BETWEEN 0 AND accepted_cents),
    decision_due_at text,
    note text NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, award_id, term_id),
    FOREIGN KEY (tenant_id, award_id) REFERENCES university.award(tenant_id, id),
    FOREIGN KEY (tenant_id, term_id) REFERENCES university.term(tenant_id, id)
);
ALTER TABLE university.award_term ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.award_term FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.award_term
    USING (tenant_id = NULLIF(current_setting('audentra.tenant_id', true), '')::uuid);
