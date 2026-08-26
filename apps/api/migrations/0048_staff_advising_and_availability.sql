-- Staff identity, adviser relationships, availability and person-bound
-- appointments.
--
-- Until now a staff member was a display name, an email and a component, and
-- an appointment was a type and a start time. Advising is a relationship
-- between a student and a person with a calendar: this migration makes that
-- relationship, the person's organisational position, their working hours and
-- their absences canonical, so booking can conflict, a student can see who
-- their adviser is, and a staff member can see the students that are theirs.
--
-- Design notes:
-- * `staff_member.active` keeps its authentication meaning (may this person
--   sign in and own work). `employment_status` is the richer HR state; a
--   departed person is never active, an on-leave person still is.
-- * Assignments are a history: an ended assignment stays as a row with
--   `ended_at`, and at most one assignment per (student, role) is current.
-- * Availability is a weekly pattern in the staff member's own time zone plus
--   explicit absences. Open slots are derived from the pattern, the absences
--   and the existing appointments — never stored, so they cannot drift.
-- * A scheduled appointment with a staff member is exclusive: the partial
--   unique index below is the durable booking-conflict guard, independent of
--   the application-level overlap check.

ALTER TABLE staff_member
  ADD COLUMN external_ref varchar(64),
  ADD COLUMN title varchar(160),
  ADD COLUMN role_code varchar(40) NOT NULL DEFAULT 'staff',
  ADD COLUMN manager_id uuid,
  ADD COLUMN employment_status varchar(16) NOT NULL DEFAULT 'active'
    CHECK (employment_status IN ('active', 'on_leave', 'departed')),
  ADD COLUMN employment_type varchar(16) NOT NULL DEFAULT 'full_time'
    CHECK (employment_type IN ('full_time', 'part_time')),
  ADD COLUMN started_at date,
  ADD COLUMN ended_at date,
  ADD COLUMN leave_until date,
  ADD COLUMN timezone varchar(64) NOT NULL DEFAULT 'UTC',
  ADD COLUMN office_location varchar(160),
  ADD COLUMN caseload_cap integer CHECK (caseload_cap IS NULL OR caseload_cap > 0),
  ADD COLUMN student_facing boolean NOT NULL DEFAULT true,
  ADD COLUMN appointment_types text[] NOT NULL DEFAULT '{}',
  ADD CONSTRAINT staff_member_manager_fk
    FOREIGN KEY (manager_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE SET NULL (manager_id),
  ADD CONSTRAINT staff_member_departed_inactive_check
    CHECK (employment_status <> 'departed' OR active = false),
  ADD CONSTRAINT staff_member_not_own_manager_check
    CHECK (manager_id IS NULL OR manager_id <> id);

CREATE UNIQUE INDEX staff_member_tenant_external_ref_uidx
  ON staff_member(tenant_id, external_ref)
  WHERE external_ref IS NOT NULL;

CREATE INDEX staff_member_tenant_manager_idx
  ON staff_member(tenant_id, manager_id)
  WHERE manager_id IS NOT NULL;

-- Who is responsible for a student, by role. `primary_advisor` is the advising
-- relationship the student sees; the counsellor roles are the other people the
-- product may route a student to.
CREATE TABLE student_staff_assignment (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL,
  staff_member_id uuid NOT NULL,
  role varchar(40) NOT NULL CHECK (
    role IN (
      'primary_advisor',
      'admissions_counselor',
      'financial_aid_counselor',
      'international_adviser',
      'housing_coordinator'
    )
  ),
  assigned_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  source varchar(40) NOT NULL DEFAULT 'manual',
  note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_staff_assignment_student_fk
    FOREIGN KEY (student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT student_staff_assignment_staff_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE CASCADE,
  CHECK (ended_at IS NULL OR ended_at >= assigned_at)
);

CREATE UNIQUE INDEX student_staff_assignment_current_uidx
  ON student_staff_assignment(tenant_id, student_id, role)
  WHERE ended_at IS NULL;

CREATE INDEX student_staff_assignment_staff_current_idx
  ON student_staff_assignment(tenant_id, staff_member_id, role)
  WHERE ended_at IS NULL;

CREATE INDEX student_staff_assignment_student_history_idx
  ON student_staff_assignment(tenant_id, student_id, assigned_at DESC);

-- Weekly working pattern, expressed in the staff member's time zone.
-- weekday: 0 = Sunday … 6 = Saturday. Minutes are offsets from local midnight.
CREATE TABLE staff_availability (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  staff_member_id uuid NOT NULL,
  weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
  start_minute smallint NOT NULL CHECK (start_minute BETWEEN 0 AND 1439),
  end_minute smallint NOT NULL CHECK (end_minute BETWEEN 1 AND 1440),
  modality varchar(16) NOT NULL DEFAULT 'either'
    CHECK (modality IN ('in_person', 'virtual', 'either')),
  location varchar(160),
  appointment_types text[] NOT NULL DEFAULT '{}',
  slot_minutes smallint NOT NULL DEFAULT 30 CHECK (slot_minutes BETWEEN 5 AND 480),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_availability_staff_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE CASCADE,
  CHECK (end_minute > start_minute)
);

CREATE INDEX staff_availability_staff_idx
  ON staff_availability(tenant_id, staff_member_id, weekday, start_minute);

-- Absences and blocks. `blocks_bookings` lets a note-only entry (a conference
-- the person still takes calls from) coexist with hard leave.
CREATE TABLE staff_time_off (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  staff_member_id uuid NOT NULL,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  kind varchar(24) NOT NULL DEFAULT 'other'
    CHECK (kind IN ('leave', 'vacation', 'sick', 'training', 'conference', 'blocked', 'other')),
  note text,
  blocks_bookings boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_time_off_staff_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE CASCADE,
  CHECK (ends_at > starts_at)
);

CREATE INDEX staff_time_off_staff_idx
  ON staff_time_off(tenant_id, staff_member_id, starts_at, ends_at);

-- An appointment is with a person, has a duration, a place and a lifecycle.
ALTER TABLE student_appointment
  DROP CONSTRAINT student_appointment_type_check,
  DROP CONSTRAINT student_appointment_status_check,
  ADD CONSTRAINT student_appointment_type_check CHECK (
    type IN (
      'admissions_counseling',
      'financial_aid',
      'enrollment_support',
      'academic_advising',
      'international_check_in'
    )
  ),
  ADD CONSTRAINT student_appointment_status_check CHECK (
    status IN ('scheduled', 'cancelled', 'completed', 'no_show', 'rescheduled')
  ),
  ADD COLUMN staff_member_id uuid,
  ADD COLUMN ends_at timestamptz,
  ADD COLUMN modality varchar(16)
    CHECK (modality IS NULL OR modality IN ('in_person', 'virtual')),
  ADD COLUMN location varchar(160),
  ADD COLUMN booked_via varchar(24) NOT NULL DEFAULT 'student_portal'
    CHECK (booked_via IN ('student_portal', 'staff', 'walk_in', 'import')),
  ADD COLUMN cancelled_at timestamptz,
  ADD COLUMN cancel_reason varchar(240),
  ADD COLUMN rescheduled_to_id uuid REFERENCES student_appointment(id) ON DELETE SET NULL,
  ADD COLUMN outcome_note text,
  ADD COLUMN version integer NOT NULL DEFAULT 1,
  ADD CONSTRAINT student_appointment_staff_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE SET NULL (staff_member_id),
  ADD CONSTRAINT student_appointment_ends_after_start_check
    CHECK (ends_at IS NULL OR ends_at > starts_at);

-- A staff member cannot hold two live appointments starting at the same time.
CREATE UNIQUE INDEX student_appointment_staff_slot_uidx
  ON student_appointment(tenant_id, staff_member_id, starts_at)
  WHERE status = 'scheduled' AND staff_member_id IS NOT NULL;

CREATE INDEX student_appointment_staff_calendar_idx
  ON student_appointment(tenant_id, staff_member_id, starts_at)
  WHERE staff_member_id IS NOT NULL;

-- A demo session is a development-only way into the staff portal as a chosen
-- synthetic person. It has no credential account and no federated identity,
-- and the API refuses to resolve it outside development, preview and test.
ALTER TABLE staff_auth_session
  DROP CONSTRAINT staff_auth_session_authentication_method_check,
  DROP CONSTRAINT staff_auth_session_identity_check,
  ADD CONSTRAINT staff_auth_session_authentication_method_check
    CHECK (authentication_method IN ('credentials', 'google', 'microsoft', 'demo')),
  ADD CONSTRAINT staff_auth_session_identity_check CHECK (
    (authentication_method = 'credentials'
      AND account_id IS NOT NULL AND federated_identity_id IS NULL)
    OR
    (authentication_method IN ('google', 'microsoft')
      AND account_id IS NULL AND federated_identity_id IS NOT NULL)
    OR
    (authentication_method = 'demo'
      AND account_id IS NULL AND federated_identity_id IS NULL)
  );
