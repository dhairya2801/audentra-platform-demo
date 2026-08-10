-- Answer-driven journey routing. Rules live on the target requirement and
-- reference an earlier prerequisite's canonical response. Keeping routing on
-- immutable definition versions makes publication and student reconciliation
-- auditable without rewriting completed response records.

ALTER TABLE requirement_definition_version
  ADD COLUMN activation_rules jsonb NOT NULL DEFAULT '{"match":"all","rules":[]}'::jsonb
    CHECK (jsonb_typeof(activation_rules) = 'object')
    CHECK (jsonb_typeof(COALESCE(activation_rules->'rules', '[]'::jsonb)) = 'array')
    CHECK (COALESCE(activation_rules->>'match', 'all') IN ('all', 'any'));
