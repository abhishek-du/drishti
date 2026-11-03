"""D1 copy tool: the Parquet-side bar hash equals the SQL-side hash; Prajna is never written."""

import pytest

from drishti.switch import d1copy


def test_python_hash_equals_sql_hash():
    # (key, bar start, o, h, l, c, v, fetched_at) as Postgres types, and the Parquet-side text
    rows = [("NSE_EQ|INE002A01018", "2026-09-01 03:45:00+00", "1234.5000", "1240.0500",
             "1230.0000", "1238.1000", "12345", "2026-09-01 11:00:03.745786+00",
             "1788234300000000", "1788260403745786"),
            ("NSE_INDEX|Nifty 50", "2026-09-02 03:45:00+00", "0.0500", "1.0000", "0.0100",
             "0.9999", "0", "2026-09-02 11:00:00+00", "1788320700000000", "1788346800000000")]
    for k, bs, o, h, lo, c, v, fa, bs_us, fa_us in rows:
        got = d1copy.sql("drishti_test", f"""select {d1copy.HASH_SQL} from (select
            '{k}'::text instrument_key, timestamptz '{bs}' bar_start_utc,
            {o}::numeric(18,4) open, {h}::numeric(18,4) high, {lo}::numeric(18,4) low,
            {c}::numeric(18,4) close, {v}::numeric(22,0) volume,
            timestamptz '{fa}' fetched_at) t""", False)[0][0]
        assert d1copy._h60([(k, bs_us, o, h, lo, c, v, fa_us)]) == int(got)


def test_prajna_is_read_only_in_the_tool():
    with pytest.raises(RuntimeError):
        d1copy.sql("prajna", "select 1", read_only=False)
    with pytest.raises(RuntimeError):            # the read-only session refuses any write
        d1copy.sql("prajna", "create temp table x(i int); insert into x values (1);")
