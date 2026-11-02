"""Drishti's own news (P1c, Option B, user decision D2: Upstox /v2/news; no NSE fetching).

Provenance (copied, not imported; Prajna commit 25712fc): `app/parsers/upstox_news.py` and
`app/ingest/news.py`. Endpoint GET /v2/news?category=instrument_keys&instrument_keys=<<= 30>
&page_number=<n>&page_size=<= 100>; shape measured 2026-09-24:
  {"status": "success", "data": {"<key>": [article, ...]}, "metadata": {"page": {...}}}
  article = {heading, summary, thumbnail, article_link, published_time (epoch ms)}
An article is identified by (sha256(heading), published_time); one article can be listed
under several keys (news_link). There is no publisher field (never invented). knowable_at =
published_time, or the fetch time when the vendor's time is later (clock skew).
Only the last 7 days are served: a day without a run loses news for good.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from urllib.parse import quote

from sqlalchemy import text

MAX_KEYS = 30
PAGE_SIZE = 100
MAX_PAGES = 100
_ARTICLE_ID = re.compile(r"article-(\d+)/?$")


def path(keys: list[str], page: int) -> str:
    if not 1 <= len(keys) <= MAX_KEYS or not 1 <= page <= MAX_PAGES:
        raise ValueError("1..30 keys and page 1..100")
    return (f"/v2/news?category=instrument_keys&instrument_keys="
            f"{','.join(quote(k, safe='') for k in keys)}&page_number={page}"
            f"&page_size={PAGE_SIZE}")


def parse(body: dict, requested: list[str], fetched_at: _dt.datetime) -> dict:
    """-> {articles: {key: art}, links: {(key, ikey)}, total_pages, total_records, problems}."""
    if not isinstance(body, dict) or body.get("status") != "success":
        raise ValueError(f"not a success envelope: {str(body)[:200]}")
    page = (body.get("metadata") or {}).get("page") or {}
    data = body.get("data") or {}
    out = {"articles": {}, "links": set(), "total_pages": page.get("total_pages"),
           "total_records": page.get("total_records"), "problems": []}
    if not isinstance(data, dict):
        out["problems"].append("data is not an object")
        return out
    extra = set(data) - set(requested)
    if extra:
        out["problems"].append(f"articles for keys not requested: {sorted(extra)[:5]}")
    for ikey, items in data.items():
        for a in items if isinstance(items, list) else []:
            head, ts = (a or {}).get("heading"), (a or {}).get("published_time")
            if not isinstance(head, str) or not head.strip() or isinstance(ts, bool) \
                    or not isinstance(ts, int) or ts <= 0:
                out["problems"].append(f"bad article under {ikey}")
                continue
            pub = _dt.datetime.fromtimestamp(ts / 1000, _dt.timezone.utc)
            link = a.get("article_link")
            m = _ARTICLE_ID.search(link) if isinstance(link, str) else None
            k = (hashlib.sha256(head.encode()).hexdigest(), pub)
            out["articles"].setdefault(k, {
                "headline": head, "headline_sha256": k[0], "published_at": pub,
                "summary": a.get("summary") or None,
                "url": link if isinstance(link, str) and link else None,
                "vendor_article_id": m.group(1) if m else None, "vendor_payload": a,
                "knowable_at": pub if pub <= fetched_at else fetched_at})
            out["links"].add((k, ikey))
    return out


async def fetch_store(s, rid, api, keys: list[str]) -> dict:
    c = {"keys": len(keys), "requests": 0, "errors": 0, "articles_seen": 0, "articles_new": 0,
         "links_new": 0, "problems": 0}
    ks = sorted(set(keys))
    for i in range(0, len(ks), MAX_KEYS):
        batch, pg, pages = ks[i:i + MAX_KEYS], 1, 1
        while pg <= pages:
            r = api.get_response(path(batch, pg))
            c["requests"] += 1
            parsed, err = None, None
            if r.status_code == 200:
                try:
                    parsed = parse(r.json(), batch, _dt.datetime.now(_dt.timezone.utc))
                except ValueError as e:
                    err = str(e)[:300]
            else:
                err = r.content[:300].decode(errors="replace")
            pid, ts = (await s.execute(text("""insert into news_payload(run_id, keys, page,
                http_status, sha256, total_records, total_pages, n_articles, error) values
                (:r, :k, :p, :st, :h, :tr, :tp, :n, :e) returning id, fetched_at"""),
                {"r": rid, "k": batch, "p": pg, "st": r.status_code,
                 "h": hashlib.sha256(r.content).hexdigest(),
                 "tr": parsed and parsed["total_records"], "tp": parsed and parsed["total_pages"],
                 "n": len(parsed["articles"]) if parsed else 0, "e": err})).one()
            if parsed is None:
                c["errors"] += 1
                break
            ids = {}
            for k, a in parsed["articles"].items():
                row = (await s.execute(text("""insert into news_article(payload_id, headline,
                    headline_sha256, published_at, summary, url, vendor_article_id,
                    vendor_payload, first_seen_at, knowable_at) values (:p, :headline,
                    :headline_sha256, :published_at, :summary, :url, :vendor_article_id,
                    cast(:vp as jsonb), :ts, :knowable_at)
                    on conflict (headline_sha256, published_at) do nothing returning id"""),
                    {**a, "p": pid, "vp": json.dumps(a["vendor_payload"]), "ts": ts})).first()
                if row:
                    ids[k] = row[0]
                    c["articles_new"] += 1
                else:
                    ids[k] = (await s.execute(text("""select id from news_article
                        where headline_sha256 = :h and published_at = :t"""),
                        {"h": k[0], "t": k[1]})).scalar()
            for k, ikey in parsed["links"]:
                c["links_new"] += (await s.execute(text("""insert into news_link(article_id,
                    instrument_key, first_seen_at) values (:a, :k, :ts)
                    on conflict do nothing"""), {"a": ids[k], "k": ikey, "ts": ts})).rowcount
            c["articles_seen"] += len(parsed["articles"])
            c["problems"] += len(parsed["problems"])
            pages = min(max(pages, parsed["total_pages"] or 1), MAX_PAGES)
            pg += 1
        await s.commit()
    return c


async def compare_with_prajna(s, ps, since: _dt.datetime) -> dict:
    """Continuity: Upstox articles published since `since` in Prajna's news_article vs
    Drishti's (identity: sha256(heading), published_at). Prajna polls more often, so Drishti
    may miss an article only if it fell out of the vendor's 7-day window before we polled."""
    ours = {(h, t) for h, t in (await s.execute(text("""select headline_sha256, published_at
        from news_article where published_at >= :s"""), {"s": since})).all()}
    cols = {r[0] for r in (await ps.execute(text("""select column_name from
        information_schema.columns where table_name = 'news_article'"""))).all()}
    hcol = "headline_sha256" if "headline_sha256" in cols else None
    if hcol is None:
        return {"error": "Prajna news_article has no headline_sha256 column"}
    theirs = {(h, t) for h, t in (await ps.execute(text(f"""select {hcol}, published_at
        from news_article where published_at >= :s"""), {"s": since})).all()}
    return {"since": since.isoformat(), "drishti": len(ours), "prajna": len(theirs),
            "both": len(ours & theirs), "only_prajna": len(theirs - ours),
            "only_drishti": len(ours - theirs)}
