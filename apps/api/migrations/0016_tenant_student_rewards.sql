CREATE TABLE tenant_reward_program (
  tenant_id uuid PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
  point_name varchar(80) NOT NULL,
  points_per_usd integer NOT NULL DEFAULT 100
    CHECK (points_per_usd BETWEEN 1 AND 100000),
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tenant_reward_rule (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  code varchar(100) NOT NULL,
  title varchar(180) NOT NULL,
  description text NOT NULL,
  trigger_type varchar(40) NOT NULL
    CHECK (
      trigger_type IN (
        'onboarding_completed',
        'requirement_completed',
        'activity_event'
      )
    ),
  trigger_key varchar(160) NOT NULL,
  trigger_properties jsonb NOT NULL DEFAULT '{}'::jsonb,
  points integer NOT NULL CHECK (points BETWEEN 1 AND 100000),
  max_awards_per_student integer NOT NULL DEFAULT 1
    CHECK (max_awards_per_student BETWEEN 1 AND 1000),
  display_order integer NOT NULL DEFAULT 0,
  enabled boolean NOT NULL DEFAULT true,
  starts_at timestamptz,
  ends_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code)
);

CREATE INDEX tenant_reward_rule_trigger_idx
  ON tenant_reward_rule (
    tenant_id,
    trigger_type,
    trigger_key,
    enabled,
    display_order
  );

CREATE TABLE student_reward_ledger (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  reward_rule_id uuid NOT NULL REFERENCES tenant_reward_rule(id),
  source_type varchar(40) NOT NULL,
  source_key varchar(200) NOT NULL,
  points integer NOT NULL CHECK (points > 0),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  awarded_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, student_id, reward_rule_id, source_key)
);

CREATE INDEX student_reward_ledger_balance_idx
  ON student_reward_ledger (tenant_id, student_id, awarded_at DESC);

CREATE OR REPLACE FUNCTION prevent_student_reward_ledger_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'student_reward_ledger is append-only; record adjustments as new awards';
END;
$$;

CREATE TRIGGER student_reward_ledger_append_only
BEFORE UPDATE OR DELETE ON student_reward_ledger
FOR EACH ROW
EXECUTE FUNCTION prevent_student_reward_ledger_mutation();
