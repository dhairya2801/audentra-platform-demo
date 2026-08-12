-- Edward voice sessions.
--
-- A voice session binds one LiveKit room to one assistant conversation for
-- one student. The room name and participant identity are opaque values the
-- platform mints; the voice agent worker resolves a session by id through the
-- internal API and never receives student identifiers beyond this binding.
-- Sessions expire on a fixed clock and end explicitly, so a leaked room name
-- or token can never outlive its session.

-- The conversation FK re-checks tenant and student so a voice session can
-- never bridge a conversation into another student's scope.
CREATE UNIQUE INDEX assistant_conversation_owner_id_uidx
  ON assistant_conversation (id, tenant_id, student_id);

CREATE TABLE assistant_voice_session (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  actor_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  provider varchar(24) NOT NULL DEFAULT 'livekit'
    CHECK (provider = 'livekit'),
  room_name varchar(255) NOT NULL UNIQUE,
  participant_identity varchar(255) NOT NULL,
  page_path varchar(240),
  page_label varchar(240),
  status varchar(24) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'ended')),
  expires_at timestamptz NOT NULL,
  ended_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT assistant_voice_session_conversation_owner_fk
    FOREIGN KEY (conversation_id, tenant_id, student_id)
    REFERENCES assistant_conversation(id, tenant_id, student_id)
    ON DELETE CASCADE,
  CONSTRAINT assistant_voice_session_end_state_check CHECK (
    (status = 'active' AND ended_at IS NULL)
    OR (status = 'ended' AND ended_at IS NOT NULL)
  ),
  CONSTRAINT assistant_voice_session_expiry_check CHECK (
    expires_at > created_at
  )
);

CREATE INDEX assistant_voice_session_owner_lookup_idx
  ON assistant_voice_session (tenant_id, student_id, status, expires_at);

CREATE INDEX assistant_voice_session_active_expiry_idx
  ON assistant_voice_session (status, expires_at);
