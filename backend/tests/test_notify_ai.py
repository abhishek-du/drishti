import datetime as dt
import pytest
from drishti.scanner import notify, notify_ai


def test_format_alert_ai_html_and_emojis():
    a = {
        "id": 101,
        "tf": "15m",
        "symbol": "SGMART",
        "instrument_key": "NSE_EQ|INE385F01024",
        "detector": "BREAKOUT_52W",
        "session_date": dt.date(2026, 11, 13),
        "bar_time": dt.datetime(2026, 11, 13, 5, 15, tzinfo=dt.timezone.utc),
        "entry_ref": 950.37,
        "stop": 944.42,
        "r1": None,
        "r2": None,
        "qty": 168,
        "context": {"regime": "ON", "spec_gate": "ON", "source": "radar"},
    }
    card = notify_ai.format_alert_ai(a, None)
    assert "SGMART" in card
    assert "BREAKOUT_52W" in card
    assert "950.37" in card
    assert "944.42" in card
    assert "962.27" in card
    assert "🚨" in card
    assert "🛑" in card
    assert "🎯" in card
    assert "Live Radar" in card
    assert notify.LABEL in card


def test_format_digest_15m_ai_clean_structure():
    bt = dt.datetime(2026, 11, 13, 5, 15, tzinfo=dt.timezone.utc)
    al = [
        {
            "id": 1,
            "tf": "15m",
            "symbol": "SGMART",
            "detector": "BREAKOUT_52W",
            "entry_ref": 950.37,
            "stop": 944.42,
            "r1": None,
            "r2": None,
            "qty": 168,
            "context": {},
        },
        {
            "id": 2,
            "tf": "15m",
            "symbol": "JYOTICNC",
            "detector": "BREAKOUT_52W",
            "entry_ref": 1109.20,
            "stop": 1089.27,
            "r1": 1149.07,
            "r2": None,
            "qty": 50,
            "context": {},
        },
    ]
    digest = notify_ai.format_digest_15m_ai(bt, al)
    assert "15m ALERTS DIGEST" in digest
    assert "SGMART" in digest
    assert "JYOTICNC" in digest
    assert "950.37" in digest
    assert "1109.20" in digest
    assert "⚡" in digest
    assert "Trigger Orders Only" in digest
    assert notify.LABEL in digest


def test_format_outcome_ai_variations():
    a = {
        "id": 201,
        "tf": "15m",
        "symbol": "SGMART",
        "detector": "BREAKOUT_52W",
        "session_date": dt.date(2026, 11, 13),
        "stop": 944.42,
        "entry_ref": 950.37,
    }
    # 1. Target hit
    o1 = {
        "outcome": "R1",
        "target_used": 962.28,
        "exit_price": 962.28,
        "r_net": 1.45,
        "exit_bar": dt.datetime(2026, 11, 13, 5, 30, tzinfo=dt.timezone.utc),
    }
    t1 = notify_ai.format_outcome_ai(a, o1, None, None)
    assert "🎯" in t1
    assert "target 962.28 reached" in t1

    # 2. Stop hit
    o2 = {
        "outcome": "STOP",
        "target_used": 962.28,
        "exit_price": 944.0,
        "r_net": -1.05,
        "exit_bar": dt.datetime(2026, 11, 13, 6, 0, tzinfo=dt.timezone.utc),
    }
    t2 = notify_ai.format_outcome_ai(a, o2, None, None)
    assert "🛑" in t2
    assert "stop 944.42 hit" in t2

    # 3. No fill
    o3 = {"outcome": "NO_FILL", "exit_bar": None}
    t3 = notify_ai.format_outcome_ai(a, o3, None, None)
    assert "⚪" in t3
    assert "not triggered" in t3


def test_telegram_send_with_parse_mode_fallback(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text

    def fake_post(url, json, timeout):
        calls.append(json.copy())
        if "parse_mode" in json:
            # Simulate HTML parse error on first attempt
            return FakeResponse(400, "Bad Request: can't parse entities")
        # Plain text succeeds
        return FakeResponse(200, "OK")

    class FakeCfg:
        DRISHTI_TELEGRAM_BOT_TOKEN = "fake_token"
        DRISHTI_TELEGRAM_CHAT_ID = "fake_chat"

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    monkeypatch.setattr(notify, "get_settings", lambda: FakeCfg)
    monkeypatch.setattr(notify.time, "sleep", lambda x: None)

    res = notify.send("<b>Hello</b>")
    assert res == "sent"
    assert len(calls) == 2
    assert calls[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1]
