-- Census is a distinct institutional deadline, not the add/drop deadline.
UPDATE university.term t SET census_at=c.starts_at FROM university.calendar c
 WHERE c.tenant_id=t.tenant_id AND c.term_label=t.name
 AND c.id IN ('fall2026-census','spring2027-census');
