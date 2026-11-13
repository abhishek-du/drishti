"""AI Trading Intelligence Agent for Drishti (user 2026-11-13).
DECISION SUPPORT ONLY · NOT FINANCIAL ADVICE · PAPER ONLY.

Generates a complete 360-degree quantitative & price-action breakdown for any stock:
  1. Section 1: Major Positives & Strengths (Multi-TF trend alignment, 52W high distance, price action, alerts, rule verdict)
  2. Section 2: Time Machine Reality & The Catch (Statistical odds, neither % breakdown, EMA20 distance, risk)
  3. Section 3: Actionable Execution Playbook (Option A Intraday Momentum vs Option B Swing Retest entry, stops, targets)

Integrates with Amazon Bedrock Nova Pro (amazon.nova-pro-v1:0) via boto3 Converse API
with credentials from Prajna / .env. Falls back seamlessly to the built-in deterministic
quantitative reasoning engine if Bedrock credentials require rotation or encounter API limits.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from typing import Any

import numpy as np
import pandas as pd

from drishti.analysis import now as A
from drishti.core.clock import IST, now
from drishti.core.config import get_settings
from drishti.features import indicators, levels

CACHE_DIR = pathlib.Path(__file__).resolve().parents[2] / "var" / "cache" / "ai"
CACHE_TTL_S = 900.0  # 15 minutes cache per stock


def _safe_str(val: Any, default: str = "–") -> str:
    if val is None:
        return default
    if isinstance(val, float):
        if not np.isfinite(val):
            return default
        return f"{val:,.2f}".rstrip("0").rstrip(".") if "." in f"{val:,.2f}" else f"{val:,.2f}"
    return str(val)


def _safe_round(val: Any, nd: int = 2) -> float | None:
    if val is None or not isinstance(val, (int, float)) or not np.isfinite(val):
        return None
    return round(float(val), nd)


async def compile_stock_facts(s, ps, instrument_key: str, live) -> dict[str, Any]:
    """Compiles all technical, quantitative, and rule facts across all timeframes."""
    # 1. Base analysis
    raw = await A.analyse(s, ps, instrument_key, live)
    if "error" in raw:
        return {"error": raw["error"], "key": instrument_key}

    symbol = raw.get("symbol")
    if not symbol:
        try:
            from sqlalchemy import text
            row = (await ps.execute(text("select trading_symbol, name from instrument where instrument_key = :k limit 1"), {"k": instrument_key})).first()
            if row and row[0]:
                symbol = row[0]
        except Exception:
            pass
    if not symbol:
        symbol = instrument_key
    price = raw.get("price") or {}
    ltp = _safe_round(price.get("ltp"))
    change_pct = _safe_round(price.get("change_pct"))
    prev_close = _safe_round(price.get("prev_close"))
    today = price.get("today") or {}
    today_open = _safe_round(today.get("open"))
    today_high = _safe_round(today.get("high"))
    today_low = _safe_round(today.get("low"))

    ctx = raw.get("context") or {}
    hi52 = _safe_round(ctx.get("hi52"))
    pct_below_52w = _safe_round(ctx.get("pct_below_52w_high"))
    rsi14 = _safe_round(ctx.get("rsi14"), 1)
    atr = _safe_round(ctx.get("atr"))
    atr_pct = _safe_round(ctx.get("atr_pct"), 2)
    sma50 = _safe_round(ctx.get("sma50"))
    sma200 = _safe_round(ctx.get("sma200"))
    ma_trend_up = bool(ctx.get("ma_trend_up"))

    plan = raw.get("plan_if_bought_now") or {}
    entry = _safe_round(plan.get("entry"))
    stop = _safe_round(plan.get("stop"))
    target = _safe_round(plan.get("target"))
    risk_per_share = _safe_round(plan.get("risk_per_share"))
    stop_basis = plan.get("stop_basis") or "support - 0.1 ATR"
    target_basis = plan.get("target_basis") or "2R"
    net_rr = _safe_round(plan.get("net_rr"))
    qty = plan.get("qty_1pct_of_10L") or 0
    horizon_sessions = plan.get("horizon_sessions") or 5

    odds = raw.get("odds") or {}
    mc = odds.get("mc") or {}
    target_first = _safe_round(mc.get("target_first"))
    stop_first = _safe_round(mc.get("stop_first"))
    neither = _safe_round(mc.get("neither"))
    avg_r_net = _safe_round(mc.get("avg_r_net"))
    median_r_net = _safe_round(mc.get("median_r_net"))
    replay_n = odds.get("replay_n") or 0
    tm_verdict = odds.get("verdict") or "Mostly neither barrier"
    breakeven = _safe_round(odds.get("breakeven"))
    paper_gate_pass = bool(odds.get("paper_gate_pass"))

    lvls = raw.get("levels") or {}
    sup = lvls.get("support") or {}
    res = lvls.get("resistance") or {}
    sup_bottom = _safe_round(sup.get("bottom"))
    sup_top = _safe_round(sup.get("top"))
    res_bottom = _safe_round(res.get("bottom"))
    res_top = _safe_round(res.get("top"))
    pct_to_sup = _safe_round(lvls.get("pct_to_support"))
    pct_to_res = _safe_round(lvls.get("pct_to_resistance"))

    scn = raw.get("scanner") or {}
    alerts = scn.get("alerts_today") or []
    watchlist_rank = scn.get("watchlist_rank")

    verdicts = raw.get("verdicts") or {}
    v1d = verdicts.get("1d") or {}
    rule_tag = v1d.get("tag") or "NO RULE"
    rule_tone = v1d.get("tone") or "info"
    rule_reasons = v1d.get("reasons") or []

    # 2. Multi-timeframe trend & EMA20 calculation
    trends: dict[str, str | None] = {}
    ema20 = None
    dist_ema20_pct = None
    volr20 = None

    tfs = getattr(live, "TFS", ["1m", "5m", "15m", "1h", "1d"])
    for tf in tfs:
        try:
            df, _ = await live.load(instrument_key, tf)
            if not df.empty:
                ind = indicators.basic(df.reset_index(drop=True))
                trends[tf] = levels.trend(df.reset_index(drop=True), ind)
                if tf == "1d":
                    if "ema20" in ind.columns and pd.notna(ind.ema20.iloc[-1]):
                        ema20 = _safe_round(float(ind.ema20.iloc[-1]))
                        if ltp and ema20:
                            dist_ema20_pct = _safe_round(((ltp - ema20) / ema20) * 100)
                    if "volr20" in ind.columns and pd.notna(ind.volr20.iloc[-1]):
                        volr20 = _safe_round(float(ind.volr20.iloc[-1]))
            else:
                trends[tf] = None
        except Exception:
            trends[tf] = None

    if raw.get("context", {}).get("trend"):
        trends["1d"] = raw["context"]["trend"]

    return {
        "key": instrument_key,
        "symbol": symbol,
        "as_of": now().isoformat(),
        "price": {
            "ltp": ltp,
            "change_pct": change_pct,
            "prev_close": prev_close,
            "open": today_open,
            "high": today_high,
            "low": today_low,
        },
        "trends": trends,
        "technical": {
            "ema20": ema20,
            "dist_ema20_pct": dist_ema20_pct,
            "sma50": sma50,
            "sma200": sma200,
            "ma_trend_up": ma_trend_up,
            "rsi14": rsi14,
            "atr": atr,
            "atr_pct": atr_pct,
            "hi52": hi52,
            "pct_below_52w_high": pct_below_52w,
            "volr20": volr20,
        },
        "levels": {
            "support_bottom": sup_bottom,
            "support_top": sup_top,
            "pct_to_support": pct_to_sup,
            "resistance_bottom": res_bottom,
            "resistance_top": res_top,
            "pct_to_resistance": pct_to_res,
        },
        "plan": {
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk_per_share": risk_per_share,
            "stop_basis": stop_basis,
            "target_basis": target_basis,
            "net_rr": net_rr,
            "qty": qty,
            "horizon_sessions": horizon_sessions,
        },
        "time_machine": {
            "target_first": target_first,
            "stop_first": stop_first,
            "neither": neither,
            "avg_r_net": avg_r_net,
            "median_r_net": median_r_net,
            "replay_n": replay_n,
            "verdict": tm_verdict,
            "breakeven": breakeven,
            "paper_gate_pass": paper_gate_pass,
        },
        "scanner": {
            "alerts_today": alerts,
            "alerts_count": len(alerts),
            "watchlist_rank": watchlist_rank,
        },
        "rule_verdict": {
            "tag": rule_tag,
            "tone": rule_tone,
            "reasons": rule_reasons,
        },
    }


def generate_quantitative_markdown(f: dict[str, Any]) -> str:
    """Deterministic, high-accuracy 360-degree breakdown generator.

    Provides rigorous quantitative analysis when Bedrock is in fallback mode
    or for instant synchronous verification.
    """
    sym = f["symbol"]
    p = f["price"]
    t = f["trends"]
    tech = f["technical"]
    pl = f["plan"]
    tm = f["time_machine"]
    lv = f["levels"]
    sc = f["scanner"]
    rv = f["rule_verdict"]

    ltp = p.get("ltp") or 0.0
    chg = p.get("change_pct") or 0.0
    hi52 = tech.get("hi52") or ltp
    pct_52w = tech.get("pct_below_52w_high") or 0.0
    t1d = t.get("1d") or "SIDE"
    t1h = t.get("1h") or "SIDE"
    t15m = t.get("15m") or "SIDE"
    triple_up = (t1d == "UP" and t1h == "UP" and t15m == "UP")

    neither_pct = int(round((tm.get("neither") or 0.0) * 100))
    tgt_first_pct = int(round((tm.get("target_first") or 0.0) * 100))
    stp_first_pct = int(round((tm.get("stop_first") or 0.0) * 100))
    avg_r = tm.get("avg_r_net") or 0.0

    stop_val = pl.get("stop") or (ltp * 0.95)
    tgt_val = pl.get("target") or (ltp * 1.10)
    entry = pl.get("entry") or ltp
    risk_ps = pl.get("risk_per_share") or (ltp - stop_val)
    stop_dist_pct = round(((ltp - stop_val) / ltp) * 100, 1) if ltp > 0 else 0.0
    tgt_dist_pct = round(((tgt_val - ltp) / ltp) * 100, 1) if ltp > 0 else 0.0

    ema20_val = tech.get("ema20")
    ema20_dist = tech.get("dist_ema20_pct")
    rsi = tech.get("rsi14") or 50.0

    # Section 1 Positives
    lines = []
    lines.append(f"### 1. {sym} Kyun Strong / Weak Hai? (Major Positives & Strengths)")
    lines.append("")
    lines.append("| Metric | Current Status | Tactical Assessment |")
    lines.append("| :--- | :--- | :--- |")
    
    # Row 1: Trend Alignment
    if triple_up:
        trend_status = "🟢 1D: UP · 1H: UP · 15M: UP"
        trend_eval = "Triple Trend UP! Daily, hourly aur 15-minute sabhi timeframes par bull structure confirmed hai."
    elif t1d == "UP":
        trend_status = f"🟡 1D: UP · 1H: {t1h} · 15M: {t15m}"
        trend_eval = "Daily trend bullish hai, par lower timeframes par consolidation/pullback chal raha hai."
    else:
        trend_status = f"⚪ 1D: {t1d} · 1H: {t1h} · 15M: {t15m}"
        trend_eval = "Range-bound ya sideways swing structure. Breakout confirmation ka wait karein."
    lines.append(f"| **Multi-Timeframe Trend** | {trend_status} | {trend_eval} |")

    # Row 2: 52W High Proximity
    if pct_52w <= 2.0:
        h52_eval = f"Naye 52-Week All-Time High ke bilkul kareeb hai (sirf {pct_52w}% door). Strong relative strength."
    else:
        h52_eval = f"52-Week High ₹{_safe_str(hi52)} se {pct_52w}% niche trade ho raha hai."
    lines.append(f"| **52-Week High Proximity** | LTP ₹{_safe_str(ltp)} · 52W High ₹{_safe_str(hi52)} | {h52_eval} |")

    # Row 3: Today's Price Action
    chg_sign = "+" if chg >= 0 else ""
    candle_color = "🟢 Green Candle" if chg >= 0 else "🔴 Red Candle"
    vol_text = f" · Vol Ratio {tech.get('volr20')}×" if tech.get('volr20') else ""
    lines.append(f"| **Today's Price Action** | {candle_color} ({chg_sign}{chg}%){vol_text} | Intraday range: Low ₹{_safe_str(p.get('low'))} se High ₹{_safe_str(p.get('high'))}. |")

    # Row 4: Scanner Alerts
    n_alerts = sc.get("alerts_count") or 0
    if n_alerts > 0:
        det_names = ", ".join(set(a.get("detector", "") for a in sc.get("alerts_today", [])))
        alert_str = f"⚡ {n_alerts} Alert(s) Today ({det_names})"
        alert_eval = "Live radar & scanner ne breakout/momentum triggers confirm kiye hain."
    else:
        alert_str = "No Scanner Alerts Today"
        alert_eval = "Aaj koi fresh intraday automated alert trigger nahi hua."
    lines.append(f"| **Live Scanner Alerts** | {alert_str} | {alert_eval} |")

    # Row 5: Rule Verdict
    rv_tag = rv.get("tag", "NO RULE")
    if "BUY" in rv_tag and "DON'T" not in rv_tag:
        rv_eval = "Saare 5 quantitative gates (regime ON, positive odds, no results clash, R:R >= 1, size fits) pass hain."
    else:
        rv_eval = f"Compliance filter check: {', '.join(rv.get('reasons', []))[:90]}"
    lines.append(f"| **Rule Compliance (pf-v1)** | {rv_tag} | {rv_eval} |")
    lines.append("")

    # Section 2 Catch / Time Machine
    lines.append("### 2. Iska Catch Kya Hai? (Time Machine Reality & Statistical Odds)")
    lines.append("")
    lines.append(f"> **Time Machine 5-Session Odds:** Target first: **{tgt_first_pct}%** · Stop first: **{stp_first_pct}%** · Neither: **{neither_pct}%**")
    lines.append(f"> **Calculated Paper Plan:** Entry ₹{_safe_str(entry)} · Stop ₹{_safe_str(stop_val)} (-{stop_dist_pct}%) · Target ₹{_safe_str(tgt_val)} (+{tgt_dist_pct}%) · Net R:R: {_safe_str(pl.get('net_rr'))}")
    lines.append("")
    
    if neither_pct >= 75:
        lines.append(f"#### **Yeh {neither_pct}% 'Neither' Kyun Aa Raha Hai?**")
        lines.append(f"- **Wide Structural Stop-Loss:** Stock ka primary support zone ₹{_safe_str(lv.get('support_bottom'))} – ₹{_safe_str(lv.get('support_top'))} par hai. Entry se stop-loss lagbhag **{stop_dist_pct}%** niche ban raha hai.")
        lines.append(f"- **Target Distance:** Agar Stop-Loss {stop_dist_pct}% niche hai, toh 2R Target lene ke liye stock ko agle 5 sessions me **+{tgt_dist_pct}%** bhaagna hoga!")
        lines.append(f"- **Historical Move Reality:** Historical volatility ke hisaab se 5 dino ke andar {tgt_dist_pct}% ka move rare hota hai. Isliye 5 sessions ke andar na stop katta hai, na target touch hota hai — stock continuous gradual upward drift karta hai (Avg Net R: **{avg_r:+.2f}R**).")
    else:
        lines.append("- **Balanced Odds:** Time machine distribution show karti hai ki historical volatility stop aur target dono ko 5 sessions ke andar test karne ke liye sufficient hai.")

    if ema20_dist and ema20_dist > 5.0:
        lines.append(f"- **⚠️ EMA20 Over-Extension:** Stock apne 20-Day EMA (₹{_safe_str(ema20_val)}) se **+{ema20_dist}%** upar stretch ho chuka hai. RSI14 filhal **{rsi}** par hai. Direct top par buy karne me pullback risk rehta hai.")
    elif ema20_dist and ema20_dist < -5.0:
        lines.append(f"- **EMA20 Distance:** Price apne 20-Day EMA (₹{_safe_str(ema20_val)}) se **{ema20_dist}%** niche discount par trade ho raha hai. Rebound attempt ho sakti hai.")
    else:
        lines.append(f"- **EMA20 Proximity:** Price apne 20-Day EMA (₹{_safe_str(ema20_val)}) ke bilkul healthy zone me hai ({ema20_dist}% distance).")

    lines.append(f"- **Risk Per Share:** ₹{_safe_str(risk_ps)} per share. Position size strictly 1% portfolio risk ke anusaar rakhein.")
    lines.append("")

    # Section 3 Final Actionable Verdict
    lines.append("### 3. Final Verdict: Kya Ise Abhi Buy Karna Chahiye?")
    lines.append("")
    lines.append("Yeh aapke trading horizon aur profile par depend karta hai:")
    lines.append("")

    # Option A: Intraday
    lines.append("#### **Option A: Agar aap Intraday / Quick Momentum Trader hain**")
    if n_alerts > 0:
        latest_alert = sc.get("alerts_today", [{}])[0]
        alert_entry = _safe_round(latest_alert.get("entry_ref"))
        alert_stop = _safe_round(latest_alert.get("stop"))
        lines.append(f"- **Scanner Early Entry:** Aaj scanner ne 15m chart par ₹{_safe_str(alert_entry)} par entry aur Stop ₹{_safe_str(alert_stop)} trigger kiya tha.")
    day_high = p.get("high") or ltp
    lines.append(f"- **Fresh Entry Rule:** Abhi chase na karein jab tak stock Day's High (**₹{_safe_str(day_high)}**) ke upar 15-minute candle close na de.")
    lines.append(f"- **Intraday Stop-Loss:** Today's VWAP ya pivot support (₹{_safe_str(p.get('low'))}) ke niche tight stop rakhein. 15:20 IST se pehle position close karein.")
    lines.append("")

    # Option B: Swing Trader
    lines.append("#### **Option B: Agar aap Swing Trader hain (1 se 2 Weeks Holding)**")
    if chg >= 2.5:
        lines.append(f"- **Do NOT Chase the Day-1 Spike:** Aaj stock already **+{chg}%** bhaag chuka hai. Peak par buy karne par unfavorable risk-to-reward banega.")
        if lv.get("support_top"):
            lines.append(f"- **Ideal Buy Zone (Retest Dip):** Breakout ke baad stock aksar retest karta hai. Best entry **₹{_safe_str(lv.get('support_bottom'))} – ₹{_safe_str(lv.get('support_top'))}** (ya breakout level) par pullback aane par banegi.")
        else:
            lines.append(f"- **Ideal Buy Zone:** EMA20 (₹{_safe_str(ema20_val)}) ke paas aane par ya ₹{_safe_str(stop_val)} ke thoda upar reversal candle par enter karein.")
        lines.append(f"- **Swing Stop-Loss & Target:** Stop ₹{_safe_str(stop_val)} ke niche daily closing basis par, aur target ₹{_safe_str(tgt_val)} (Upside: +{tgt_dist_pct}%).")
    else:
        lines.append(f"- **Accumulation Zone:** Current price zone me risk manageable hai. Stop ₹{_safe_str(stop_val)} aur target ₹{_safe_str(tgt_val)}.")
        lines.append(f"- **Validation:** Ensure karein ki daily candle support level hold kar rahi hai.")

    lines.append("")
    lines.append("---")
    lines.append("*(Note: Yeh quantitative decision support model hai. Paper trading & discipline first. Apni risk capacity ke according trade karein.)*")

    return "\n".join(lines)


async def call_bedrock(facts: dict[str, Any], prompt: str) -> tuple[str, str]:
    """Calls Amazon Bedrock Converse API with Nova Pro model using boto3."""
    import boto3
    from botocore.config import Config

    st = get_settings()
    model_id = st.BEDROCK_MODEL_ID or "amazon.nova-pro-v1:0"
    region = st.BEDROCK_REGION or "us-east-1"
    token = st.AWS_BEARER_TOKEN_BEDROCK

    if token:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token

    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"mode": "adaptive", "max_attempts": 3}, connect_timeout=8, read_timeout=35),
    )

    def _invoke() -> tuple[str, str]:
        resp = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 2048, "temperature": 0.2},
        )
        msg_text = resp["output"]["message"]["content"][0]["text"]
        return msg_text, model_id

    return await asyncio.to_thread(_invoke)


def build_bedrock_prompt(facts: dict[str, Any]) -> str:
    """Builds a rich, precision-targeted system prompt for Bedrock Nova Pro."""
    facts_json = json.dumps(facts, indent=2, default=str)
    return f"""You are Drishti AI Trading Intelligence, an elite quantitative analyst and price-action specialist for the Indian stock market (NSE).
Analyze the stock using the EXACT JSON facts below.

Provide a comprehensive, high-conviction 360-degree breakdown in fluent Hinglish (Hindi + English) with clear markdown tables, bullet points, and actionable trading execution plans.

Structure your response into these 3 sections:
### 1. {facts.get('symbol')} Kyun Strong / Weak Hai? (Major Positives & Strengths)
- Provide a clear markdown table comparing: Multi-Timeframe Trend (1D, 1H, 15M), 52-Week High Proximity, Today's Price Action & Volume, Live Scanner Alerts, and Rule Compliance Verdict (pf-v1).
- Explain the key technical strengths, momentum characteristics, and structural pivot levels.

### 2. Iska Catch Kya Hai? (Time Machine Reality & Statistical Odds)
- Break down the 5-session Time Machine probabilities: Target first % vs Stop first % vs Neither %.
- Explain clearly WHY the "Neither" probability is what it is (e.g. wide structural stop-loss based on support requiring an oversized move for 2R target within 5 sessions, leading to neither barrier being hit).
- Address EMA20 distance (is the stock stretched or over-extended?), RSI14 level, and risk per share.

### 3. Final Verdict: Kya Ise Abhi Buy Karna Chahiye? (Actionable Playbook)
- Provide specific, tailored advice for:
  * Option A: Intraday / Quick Momentum Trader (exact entry trigger e.g. Day's High breakout, stop-loss, and exit rule before 15:20).
  * Option B: Swing Trader with 1-2 weeks holding (Do not chase Day-1 extended moves; identify specific pullback/retest zones, conservative stop-loss, and multi-week upside targets).
- Add a brief risk management checklist reminding that this is paper decision support.

DATA FACTS:
{facts_json}
"""


def _cache_path(key: str) -> pathlib.Path:
    day = now().astimezone(IST).date().isoformat()
    safe = "".join(ch for ch in key if ch.isalnum() or ch in "-_")
    return CACHE_DIR / day / f"{safe}.json"


async def get_ai_analysis(s, ps, instrument_key: str, live, force: bool = False) -> dict[str, Any]:
    """Returns AI Trading Intelligence breakdown for the stock.

    Uses Amazon Bedrock (Nova Pro) when available, falling back gracefully
    to the Quantitative Analysis Engine if Bedrock credentials require rotation.
    """
    cache_file = _cache_path(instrument_key)
    if not force and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            gen_time = cached.get("generated_at_ts", 0)
            if time.time() - gen_time < CACHE_TTL_S:
                return cached
        except Exception:
            pass

    facts = await compile_stock_facts(s, ps, instrument_key, live)
    if "error" in facts:
        return {"error": facts["error"], "key": instrument_key}

    # Attempt Bedrock call
    engine_name = "Bedrock (amazon.nova-pro-v1:0)"
    markdown_content = ""
    error_msg = None

    prompt = build_bedrock_prompt(facts)
    try:
        markdown_content, model_id = await asyncio.wait_for(
            call_bedrock(facts, prompt), timeout=25.0
        )
        engine_name = f"Bedrock ({model_id})"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)[:150]}"
        # Graceful fallback to deterministic quantitative engine
        markdown_content = generate_quantitative_markdown(facts)
        engine_name = f"Drishti Quantitative AI (Bedrock offline: {type(e).__name__})"

    result = {
        "key": instrument_key,
        "symbol": facts["symbol"],
        "as_of": now().isoformat(),
        "generated_at_ts": time.time(),
        "engine": engine_name,
        "bedrock_error": error_msg,
        "markdown": markdown_content,
        "facts": facts,
    }

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, indent=2))
    except Exception:
        pass

    return result
