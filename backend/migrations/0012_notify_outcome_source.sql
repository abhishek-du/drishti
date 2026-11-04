-- Telegram delivery (user 2026-11-04): one row per message event, claimed BEFORE sending, so
-- a re-run never repeats a message. status is updated after the send (sent / logged / failed),
-- so this table is not append-only (DEVIATIONS D6); the message text itself is never edited.
CREATE TABLE notify_event (
  id bigserial PRIMARY KEY,
  kind varchar(16) NOT NULL CHECK (kind IN ('SCOUT_DIGEST', 'RADAR_NEW', 'ALERT', 'OUTCOME',
                                            'SUMMARY')),
  ref varchar(80) NOT NULL,                  -- scout:<date> radar:<id> alert:<id> outcome:<id>
  status varchar(8) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sent', 'logged', 'failed', 'batched')),
  attempts integer NOT NULL DEFAULT 0,
  text text NOT NULL,
  error text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  sent_at timestamptz,
  UNIQUE (kind, ref)
);
CREATE INDEX ON notify_event (created_at);

-- who produced a grade: 'nightly' (scan_grade on stored bars) or 'live' (the intraday tracker
-- on the recorder's closed 15m bars). A live final 15m grade is never replaced by the nightly one.
ALTER TABLE journal_outcome ADD COLUMN source varchar(8) NOT NULL DEFAULT 'nightly'
  CHECK (source IN ('nightly', 'live'));
