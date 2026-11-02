-- P1c (Option B): Drishti's own corporate actions (Upstox /v2/fundamentals/{ISIN}/corporate-
-- actions; the vendor serves ~1 year, so this must run from day one) and their factors.
-- Append-only. Copied design: Prajna corporate_action / ca_factor (cafactor-v1).

CREATE TABLE ca_payload (                    -- one corporate-actions response
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  isin varchar(12) NOT NULL,
  http_status integer NOT NULL,
  sha256 char(64),
  n_events integer NOT NULL DEFAULT 0,
  error text,
  fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON ca_payload (isin, fetched_at DESC);

CREATE TABLE corporate_action (              -- one vendor event, identified by its content
  id bigserial PRIMARY KEY,
  payload_id bigint NOT NULL REFERENCES ca_payload(id),   -- the first response that had it
  isin varchar(12) NOT NULL,
  instrument_key varchar(64),
  action_type varchar(12) NOT NULL,          -- DIVIDEND / BONUS / SPLIT / RIGHTS / OTHER
  ex_date date, record_date date, announcement_date date,
  amount numeric(18,6), ratio_from numeric(18,6), ratio_to numeric(18,6),
  face_value_before numeric(18,6), face_value_after numeric(18,6),
  content_sha256 char(64) NOT NULL,
  vendor_payload jsonb NOT NULL,
  first_seen_at timestamptz NOT NULL,
  knowable_at timestamptz NOT NULL,          -- greatest(end of announcement IST day, first seen)
  UNIQUE (isin, content_sha256)
);
CREATE INDEX ON corporate_action (instrument_key, ex_date);

CREATE TABLE ca_factor (                     -- a derivation; the latest row per ca wins
  id bigserial PRIMARY KEY,
  ca_id bigint NOT NULL REFERENCES corporate_action(id),
  run_id uuid NOT NULL REFERENCES run(run_id),
  status varchar(12) NOT NULL,               -- EXACT / UNCERTAIN / UNSUPPORTED
  method varchar(16) NOT NULL,               -- SPLIT_FV / BONUS_RATIO / RIGHTS_TERP / NONE
  factor_price numeric(18,8), factor_volume numeric(18,8),
  vendor_applied varchar(12) NOT NULL,       -- APPLIED / NOT_APPLIED / UNKNOWN / N/A
  vendor_evidence jsonb NOT NULL DEFAULT '{}',
  reason text NOT NULL, inputs jsonb NOT NULL DEFAULT '{}',
  method_version varchar(16) NOT NULL,
  derived_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON ca_factor (ca_id, derived_at DESC);

DO $$ BEGIN
  EXECUTE 'CREATE TRIGGER ca_payload_append_only BEFORE UPDATE OR DELETE ON ca_payload FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER corporate_action_append_only BEFORE UPDATE OR DELETE ON corporate_action FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER ca_factor_append_only BEFORE UPDATE OR DELETE ON ca_factor FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
END $$;
