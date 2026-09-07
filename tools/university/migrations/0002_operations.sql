-- Weekly schedules use Python/ISO weekdays: Monday = 0. Runtime import converts
-- to the portal's Sunday = 0 convention explicitly.
CREATE TABLE staff_availability (
 id TEXT PRIMARY KEY, staff_id TEXT NOT NULL REFERENCES staff,
 weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
 start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
 end_minute INTEGER NOT NULL CHECK(end_minute BETWEEN 1 AND 1440),
 timezone TEXT NOT NULL, location TEXT NOT NULL,
 CHECK(end_minute>start_minute), UNIQUE(staff_id,weekday,start_minute)
);
CREATE TABLE staff_calendar_event (
 id TEXT PRIMARY KEY, staff_id TEXT NOT NULL REFERENCES staff,
 title TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
 location TEXT NOT NULL, blocks_bookings INTEGER NOT NULL CHECK(blocks_bookings IN (0,1)),
 CHECK(ends_at>starts_at)
);
CREATE INDEX staff_calendar_time ON staff_calendar_event(staff_id,starts_at,ends_at);
PRAGMA user_version = 2;
