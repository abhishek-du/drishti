-- Telegram routing (user 2026-11-04): 15m alerts go out as one digest per 15m bar and their
-- outcomes as one end-of-day digest; the events folded into a digest are marked 'digested'
-- (never sent individually later).
ALTER TABLE notify_event DROP CONSTRAINT notify_event_kind_check;
ALTER TABLE notify_event ADD CONSTRAINT notify_event_kind_check CHECK (kind IN
  ('SCOUT_DIGEST', 'RADAR_NEW', 'ALERT', 'OUTCOME', 'SUMMARY', 'DIGEST_15M', 'OUTCOME_15M'));
ALTER TABLE notify_event DROP CONSTRAINT notify_event_status_check;
ALTER TABLE notify_event ADD CONSTRAINT notify_event_status_check CHECK (status IN
  ('pending', 'sent', 'logged', 'failed', 'batched', 'digested'));
