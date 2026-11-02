-- P1c (Option B, D2): Drishti's own news from Upstox /v2/news (no NSE fetching anywhere).
-- The vendor serves only the last 7 days, so this runs every day. Append-only.
-- Design copied from Prajna news_article / news_instrument (parsers/upstox_news.py).

CREATE TABLE news_payload (                  -- one /v2/news page
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  keys text[] NOT NULL,
  page integer NOT NULL,
  http_status integer NOT NULL,
  sha256 char(64),
  total_records integer, total_pages integer,
  n_articles integer NOT NULL DEFAULT 0,
  error text,
  fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE news_article (                  -- identity: (sha256(heading), published_at)
  id bigserial PRIMARY KEY,
  payload_id bigint NOT NULL REFERENCES news_payload(id),   -- the first page that had it
  headline text NOT NULL,
  headline_sha256 char(64) NOT NULL,
  published_at timestamptz NOT NULL,
  summary text, url text, vendor_article_id varchar(32),
  vendor_payload jsonb NOT NULL,
  first_seen_at timestamptz NOT NULL,
  knowable_at timestamptz NOT NULL,          -- published_at, or first_seen_at if published later
  UNIQUE (headline_sha256, published_at)
);
CREATE INDEX ON news_article (published_at);

CREATE TABLE news_link (                     -- the vendor's article -> instrument grouping
  article_id bigint NOT NULL REFERENCES news_article(id),
  instrument_key varchar(64) NOT NULL,
  first_seen_at timestamptz NOT NULL,
  PRIMARY KEY (article_id, instrument_key)
);
CREATE INDEX ON news_link (instrument_key);

DO $$ BEGIN
  EXECUTE 'CREATE TRIGGER news_payload_append_only BEFORE UPDATE OR DELETE ON news_payload FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER news_article_append_only BEFORE UPDATE OR DELETE ON news_article FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER news_link_append_only BEFORE UPDATE OR DELETE ON news_link FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
END $$;
