"""D1 copy (SWITCH_TO_B Option 2): Prajna's history -> Drishti, lossless, verified.

    .venv/bin/python -m drishti.switch.d1copy plan
    .venv/bin/python -m drishti.switch.d1copy views --target drishti_test   # re-point views
    .venv/bin/python -m drishti.switch.d1copy copy --target drishti_test --out DIR \
        [--tf 1d,1h,15m,5m,1m] [--months 2026-09,2026-10] [--tables a,b|none] [--delta]

Read side: Prajna through `docker exec prajna-postgres psql` in a READ-ONLY session
(default_transaction_read_only=on, max_parallel_workers_per_gather=0 so no query can fill the
container's /dev/shm). Nothing in Prajna changes. Never prints a secret (no URL or password is
used: the container's local socket).

What goes where:
  * every public table except EXCLUDED (Prajna-derived training/feature data, raw payloads,
    ingest logs, ohlcv_bar itself, the optional pre-open tables) -> schema `prajna_copy` in the
    target DB, same columns and types, full refresh on every run (several are updated in place
    in Prajna). Binary COPY, so values are bit-identical.
  * ohlcv_bar 1d -> prajna_copy.ohlcv_bar_1d (Postgres).
  * ohlcv_bar 1h/15m/5m/1m -> Parquet/zstd, OUT/<tf>/<YYYY-MM>/part_<stamp>.parquet, all
    columns (numerics as decimal128, so lossless).
ohlcv_bar is append-only in Prajna (trigger tr_ohlcv_bar_append_only), so a run copies rows with
fetched_at <= W (W = Prajna's now() at the start of the run); --delta copies fetched_at in
(W_prev, W] and appends (a new part file / rows). State: OUT/copy_state.json.

Verification (written to OUT/verify_<stamp>.json): per table, count + an order-independent hash
of each row's text; per (tf, month) of bars, count, the sums of OHLCV and a 60-bit md5 hash sum
of (key, bar start, OHLCV, fetched_at), computed in Prajna and on the copy. All must be equal.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

TZ = "-c timezone=UTC"
PRAJNA_RO = f"{TZ} -c default_transaction_read_only=on -c max_parallel_workers_per_gather=0"
EXCLUDED_PREFIX = ("training_",)
EXCLUDED = {"feature_value", "raw_payload", "ingest_run", "ohlcv_bar", "preopen_tick",
            "preopen_book", "alembic_version"}
INTRADAY = ("1h", "15m", "5m", "1m")
SCHEMA = "prajna_copy"
APP_ROLE = "drishti_app"

BAR_COLS = ["instrument_id", "timeframe", "session_date", "bar_start_utc", "source",
            "instrument_key", "open", "high", "low", "close", "volume", "open_interest",
            "vendor_ts_raw", "run_id", "payload_sha256", "fetched_at", "knowable_at",
            "knowable_at_verified", "knowable_at_basis"]
# the canonical row text of the bar hash, identical in SQL and in Python
HASH_SQL = """('x' || substr(md5(instrument_key || '|' ||
    (extract(epoch from bar_start_utc) * 1000000)::bigint || '|' || open::text || '|' ||
    high::text || '|' || low::text || '|' || close::text || '|' || volume::text || '|' ||
    (extract(epoch from fetched_at) * 1000000)::bigint), 1, 15))::bit(60)::bigint"""


# ---- psql plumbing ----------------------------------------------------------------------

def _psql(db: str, read_only: bool) -> list[str]:
    env = ["-e", f"PGOPTIONS={PRAJNA_RO if read_only else TZ}"]
    return ["docker", "exec", "-i", *env, "prajna-postgres", "sh", "-c",
            f'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d {db} -At']


def sql(db: str, q: str, read_only: bool = True) -> list[list[str]]:
    if db == "prajna" and not read_only:
        raise RuntimeError("Prajna is read only")
    r = subprocess.run(_psql(db, read_only), input=q, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"psql {db}: {r.stderr.strip()[:500]}")
    return [line.split("|") for line in r.stdout.splitlines() if line]


def pipe(src_q: str, dst_db: str, dst_q: str) -> None:
    """Prajna COPY ... TO STDOUT (read-only session) | target COPY ... FROM STDIN."""
    a = subprocess.Popen(_psql("prajna", True), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    b = subprocess.Popen([*_psql(dst_db, False)[:-1],
                          _psql(dst_db, False)[-1] + f' -c "{dst_q}"'],
                         stdin=a.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    a.stdin.write(src_q.encode())
    a.stdin.close()
    a.stdout.close()
    _, eb = b.communicate()
    ea = a.stderr.read()
    if a.wait() or b.returncode:
        raise RuntimeError(f"copy failed: {ea.decode()[:300]} {eb.decode()[:300]}")


def stream_csv(q: str):
    """Rows of a Prajna query as a pyarrow CSV stream (read-only session)."""
    p = subprocess.Popen(_psql("prajna", True), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    p.stdin.write(f"copy ({q}) to stdout with (format csv, header true)".encode())
    p.stdin.close()
    return p


# ---- tables -----------------------------------------------------------------------------

def tables() -> list[str]:
    rows = sql("prajna", """select c.relname from pg_class c join pg_namespace n
        on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind in ('r', 'p')
        order by 1""")
    return [r[0] for r in rows if r[0] not in EXCLUDED and not r[0].startswith(EXCLUDED_PREFIX)]


def ddl(t: str, name: str) -> str:
    cols = sql("prajna", f"""select a.attname, format_type(a.atttypid, a.atttypmod),
        a.attnotnull from pg_attribute a where a.attrelid = 'public.{t}'::regclass
        and a.attnum > 0 and not a.attisdropped order by a.attnum""")
    body = ", ".join(f'"{c}" {ty}{" not null" if nn == "t" else ""}' for c, ty, nn in cols)
    return f"create table {SCHEMA}.{name} ({body})"


def table_check(db: str, schema: str, t: str, where: str = "") -> dict:
    n, h = sql(db, f"""select count(*), coalesce(sum(('x' || substr(md5(x::text), 1, 15))
        ::bit(60)::bigint), 0) from {schema}.{t} x {where}""", db == "prajna")[0]
    return {"rows": int(n), "hash": h}


def copy_table(target: str, t: str, prajna_where: str = "", name: str | None = None) -> dict:
    """Full refresh of one table: create <name>__new, binary COPY, swap, grant."""
    name = name or t
    new = f"{name}__new"
    sql(target, f"drop table if exists {SCHEMA}.{new}; {ddl(t, new)};", False)
    pipe(f"copy (select * from public.{t} {prajna_where}) to stdout (format binary)",
         target, f"copy {SCHEMA}.{new} from stdin (format binary)")
    sql(target, f"""begin; drop table if exists {SCHEMA}.{name};
        alter table {SCHEMA}.{new} rename to {name};
        grant select on {SCHEMA}.{name} to {APP_ROLE}; commit;""", False)
    return table_check(target, SCHEMA, name)


# ---- bars -------------------------------------------------------------------------------

def bar_checks_prajna(tf: str, w: str, months: list[str] | None) -> dict[str, dict]:
    mf = "" if not months else "and to_char(session_date, 'YYYY-MM') = any(array[%s])" % \
        ",".join(f"'{m}'" for m in months)
    rows = sql("prajna", f"""select to_char(session_date, 'YYYY-MM'), count(*), sum(open),
        sum(high), sum(low), sum(close), sum(volume), sum({HASH_SQL}) from ohlcv_bar
        where timeframe = '{tf}' and fetched_at <= '{w}' {mf} group by 1 order by 1""")
    return {r[0]: {"rows": int(r[1]), "open": r[2], "high": r[3], "low": r[4], "close": r[5],
                   "volume": r[6], "hash": r[7]} for r in rows}


def bar_checks_pg(target: str, months: list[str] | None) -> dict[str, dict]:
    mf = "" if not months else "where to_char(session_date, 'YYYY-MM') = any(array[%s])" % \
        ",".join(f"'{m}'" for m in months)
    rows = sql(target, f"""select to_char(session_date, 'YYYY-MM'), count(*), sum(open),
        sum(high), sum(low), sum(close), sum(volume), sum({HASH_SQL})
        from {SCHEMA}.ohlcv_bar_1d {mf} group by 1 order by 1""", False)
    return {r[0]: {"rows": int(r[1]), "open": r[2], "high": r[3], "low": r[4], "close": r[5],
                   "volume": r[6], "hash": r[7]} for r in rows}


def _schema():
    import pyarrow as pa
    ts = pa.timestamp("us", tz="UTC")
    return pa.schema([("instrument_id", pa.int64()), ("timeframe", pa.string()),
                      ("session_date", pa.date32()), ("bar_start_utc", ts),
                      ("source", pa.string()), ("instrument_key", pa.string()),
                      ("open", pa.decimal128(18, 4)), ("high", pa.decimal128(18, 4)),
                      ("low", pa.decimal128(18, 4)), ("close", pa.decimal128(18, 4)),
                      ("volume", pa.decimal128(22, 0)), ("open_interest", pa.decimal128(22, 0)),
                      ("vendor_ts_raw", pa.string()), ("run_id", pa.string()),
                      ("payload_sha256", pa.string()), ("fetched_at", ts),
                      ("knowable_at", ts), ("knowable_at_verified", pa.bool_()),
                      ("knowable_at_basis", pa.string())])


def copy_intraday(out: Path, tf: str, lo: str | None, w: str, stamp: str,
                  months: list[str] | None) -> dict[str, int]:
    """Rows of tf with fetched_at in (lo, w] -> one part file per month."""
    import pyarrow.csv as pc
    import pyarrow.parquet as pq
    sch = _schema()
    cond = [f"timeframe = '{tf}'", f"fetched_at <= '{w}'"]
    if lo:
        cond.append(f"fetched_at > '{lo}'")
    if months:
        cond.append("to_char(session_date, 'YYYY-MM') = any(array[%s])"
                    % ",".join(f"'{m}'" for m in months))
    p = stream_csv(f"select {', '.join(BAR_COLS)} from ohlcv_bar where {' and '.join(cond)} "
                   "order by session_date")
    rd = pc.open_csv(p.stdout, read_options=pc.ReadOptions(block_size=64 << 20),
                     convert_options=pc.ConvertOptions(column_types=sch,
                                                       strings_can_be_null=False,
                                                       true_values=["t"], false_values=["f"]))
    writers, counts = {}, {}
    try:
        for batch in rd:
            import pyarrow as pa
            import pyarrow.compute as pcm
            tb = pa.Table.from_batches([batch]).cast(sch)
            mon = pcm.strftime(tb["session_date"].cast(pa.timestamp("s")), format="%Y-%m")
            for m in pcm.unique(mon).to_pylist():
                part = tb.filter(pcm.equal(mon, m))
                if m not in writers:
                    f = out / tf / m / f"part_{stamp}.parquet"
                    f.parent.mkdir(parents=True, exist_ok=True)
                    writers[m] = pq.ParquetWriter(f, sch, compression="zstd")
                writers[m].write_table(part)
                counts[m] = counts.get(m, 0) + part.num_rows
    finally:
        for wr in writers.values():
            wr.close()
    err = p.stderr.read()
    if p.wait():
        raise RuntimeError(f"psql copy failed: {err.decode()[:300]}")
    return counts


def _h60(parts) -> int:
    return sum(int(hashlib.md5("|".join(x).encode()).hexdigest()[:15], 16) for x in parts)


def bar_checks_parquet(out: Path, tf: str, months: list[str]) -> dict[str, dict]:
    import pyarrow.compute as pcm
    import pyarrow.parquet as pq
    res = {}
    for m in months:
        files = sorted((out / tf / m).glob("part_*.parquet"))
        if not files:
            continue
        acc = {"rows": 0, "open": Decimal(0), "high": Decimal(0), "low": Decimal(0),
               "close": Decimal(0), "volume": Decimal(0), "hash": 0}
        for f in files:
            pf = pq.ParquetFile(f)
            for i in range(pf.num_row_groups):
                t = pf.read_row_group(i, columns=["instrument_key", "bar_start_utc", "open",
                                                  "high", "low", "close", "volume",
                                                  "fetched_at"])
                acc["rows"] += t.num_rows
                for c in ("open", "high", "low", "close", "volume"):
                    s = pcm.sum(t[c]).as_py()
                    acc[c] += s if s is not None else 0
                us = lambda c: pcm.cast(t[c], "int64").to_pylist()  # noqa: E731
                cols = [t["instrument_key"].to_pylist(), map(str, us("bar_start_utc")),
                        *(map(str, t[c].to_pylist()) for c in ("open", "high", "low", "close",
                                                                 "volume")),
                        map(str, us("fetched_at"))]
                acc["hash"] += _h60(zip(*cols))
        res[m] = {k: str(v) if k not in ("rows",) else v for k, v in acc.items()}
    return res


def _same(a: dict, b: dict) -> bool:
    keys = ("rows", "open", "high", "low", "close", "volume", "hash")
    return all(Decimal(str(a[k])) == Decimal(str(b[k])) for k in keys)


# ---- views (re-point) -------------------------------------------------------------------

# Prajna views Drishti reads, recreated inside the copy schema over the copied tables. ohlcv_bar
# in the copy is the 1d table only (intraday history lives in the Parquet copy).
VIEWS = ("canon_ca_factor", "canon_market_bar")


def make_views(target: str) -> dict:
    """Create (or replace) the copy schema's views from Prajna's own definitions, resolved
    against the copied tables (search_path = prajna_copy)."""
    out = {}
    stmts = [f"set search_path = {SCHEMA}",
             f"create or replace view {SCHEMA}.ohlcv_bar as select * from {SCHEMA}.ohlcv_bar_1d"]
    for v in VIEWS:
        d = sql("prajna", f"select pg_get_viewdef('public.{v}'::regclass, true)")
        body = "\n".join("|".join(r) for r in d).strip().rstrip(";")
        stmts.append(f"create or replace view {SCHEMA}.{v} as {body}")
    stmts.append(f"grant select on all tables in schema {SCHEMA} to {APP_ROLE}")
    sql(target, ";\n".join(stmts) + ";", False)
    for v in ("ohlcv_bar",) + VIEWS:
        out[v] = int(sql(target, f"select count(*) from {SCHEMA}.{v}", False)[0][0])
    return out


# ---- commands ---------------------------------------------------------------------------

def plan() -> dict:
    ts = tables()
    sizes = dict((r[0], r[1]) for r in sql("prajna", """select relname,
        pg_size_pretty(pg_total_relation_size(c.oid)) from pg_class c join pg_namespace n
        on n.oid = c.relnamespace where n.nspname = 'public'"""))
    bars = sql("prajna", """select timeframe, count(*), min(session_date), max(session_date)
        from ohlcv_bar group by 1 order by 1""")
    return {"tables": {t: sizes.get(t) for t in ts}, "n_tables": len(ts),
            "excluded": sorted(EXCLUDED) + [p + "*" for p in EXCLUDED_PREFIX],
            "bars": {r[0]: {"rows": int(r[1]), "from": r[2], "to": r[3]} for r in bars}}


def copy(target: str, out: Path, tfs: list[str], months: list[str] | None,
         only_tables: list[str] | None, delta: bool) -> dict:
    if target not in ("drishti", "drishti_test"):
        raise SystemExit("target must be drishti or drishti_test")
    out.mkdir(parents=True, exist_ok=True)
    state_f = out / "copy_state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {"runs": []}
    w = sql("prajna", "select now()")[0][0]
    lo = state["runs"][-1]["w"] if delta and state["runs"] else None
    if delta and lo is None:
        raise SystemExit("--delta needs a previous run in copy_state.json")
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    rep = {"target": target, "w": w, "w_prev": lo, "stamp": stamp, "tables": {}, "bars": {},
           "ok": True}
    sql(target, f"create schema if not exists {SCHEMA}; grant usage on schema {SCHEMA} "
        f"to {APP_ROLE};", False)
    # 1) small tables: full refresh, checked against Prajna at the same moment
    for t in (tables() if only_tables is None else only_tables):
        for _ in range(3):              # a live Prajna writer between copy and check: retry
            got = copy_table(target, t)
            src = table_check("prajna", "public", t)
            ok = got == src
            if ok:
                break
        rep["tables"][t] = {"prajna": src, "copy": got, "ok": ok}
        rep["ok"] &= ok
    # 2) 1d bars -> Postgres
    if "1d" in tfs:
        if delta:
            mf = "" if not months else " and to_char(session_date, 'YYYY-MM') = any(array[%s])" \
                % ",".join(f"'{m}'" for m in months)
            pipe(f"copy (select * from ohlcv_bar where timeframe = '1d' and fetched_at > '{lo}' "
                 f"and fetched_at <= '{w}'{mf}) to stdout (format binary)",
                 target, f"copy {SCHEMA}.ohlcv_bar_1d from stdin (format binary)")
        else:
            copy_table(target, "ohlcv_bar", f"where timeframe = '1d' and fetched_at <= '{w}'"
                       + ("" if not months else " and to_char(session_date, 'YYYY-MM') = "
                          "any(array[%s])" % ",".join(f"'{m}'" for m in months)),
                       name="ohlcv_bar_1d")
        src, got = bar_checks_prajna("1d", w, months), bar_checks_pg(target, months)
        bad = sorted(m for m in src.keys() | got.keys()
                     if m not in src or m not in got or not _same(src[m], got[m]))
        rep["bars"]["1d"] = {"months": len(src), "rows": sum(v["rows"] for v in src.values()),
                             "mismatch": bad}
        rep["ok"] &= not bad
    # 3) intraday -> Parquet
    for tf in (x for x in tfs if x in INTRADAY):
        counts = copy_intraday(out, tf, lo, w, stamp, months)
        src = bar_checks_prajna(tf, w, months)
        got = bar_checks_parquet(out, tf, sorted(src))
        bad = sorted(m for m in src if m not in got or not _same(src[m], got[m]))
        rep["bars"][tf] = {"months": len(src), "rows": sum(v["rows"] for v in src.values()),
                           "written": sum(counts.values()), "mismatch": bad,
                           "bytes": sum(f.stat().st_size for f in (out / tf).rglob("*.parquet"))}
        rep["ok"] &= not bad
    if "1d" in tfs:
        rep["views"] = make_views(target)
    state["runs"].append({"w": w, "stamp": stamp, "delta": delta, "tfs": tfs,
                          "months": months, "ok": rep["ok"]})
    state_f.write_text(json.dumps(state, indent=1))
    (out / f"verify_{stamp}.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep


def main(argv: list[str]) -> dict:
    if not argv or argv[0] not in ("plan", "copy", "views"):
        raise SystemExit(__doc__)
    if argv[0] == "plan":
        return plan()
    if argv[0] == "views":
        return make_views(argv[argv.index("--target") + 1])
    a = dict(zip(argv[1::2], argv[2::2])) if "--delta" not in argv else \
        dict(zip([x for x in argv[1:] if x != "--delta"][0::2],
                 [x for x in argv[1:] if x != "--delta"][1::2]))
    tfs = a.get("--tf", "1d,1h,15m,5m,1m").split(",")
    months = a["--months"].split(",") if "--months" in a else None
    tb = a.get("--tables")
    only = None if tb is None else ([] if tb == "none" else tb.split(","))
    return copy(a["--target"], Path(a["--out"]), tfs, months, only, "--delta" in argv)


if __name__ == "__main__":
    r = main(sys.argv[1:])
    if "tables" in r and isinstance(r["tables"], dict) and r.get("target"):
        r["tables"] = {k: v["ok"] for k, v in r["tables"].items()}
    print(json.dumps(r, indent=1, default=str))
