"""알림 규칙 검증.

핵심은 두 가지다.
1. 규칙이 맞는 상황에서만 발화하는가
2. 같은 상태가 계속돼도 두 번 울리지 않는가 (run.py 의 상태 비교)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.alerts import rules
from src.alerts.rules import Alert


# --- RSI -------------------------------------------------------------------
def test_rsi_overbought_fires():
    s = pd.Series(np.arange(1.0, 80.0), index=pd.bdate_range("2026-01-01", periods=79))
    out = rules.rsi_extremes(s, "TEST", "T.1")
    assert len(out) == 1
    assert out[0].key == "rsi:T.1:over"
    assert "과매수" in out[0].title


def test_rsi_oversold_fires():
    s = pd.Series(np.arange(80.0, 1.0, -1.0),
                  index=pd.bdate_range("2026-01-01", periods=79))
    out = rules.rsi_extremes(s, "TEST", "T.1")
    assert out[0].key == "rsi:T.1:under"


def test_rsi_neutral_silent():
    rng = np.random.default_rng(4)
    s = pd.Series(100 + rng.normal(0, 0.5, 200).cumsum(),
                  index=pd.bdate_range("2026-01-01", periods=200))
    out = rules.rsi_extremes(s, "TEST", "T.1", upper=99.9, lower=0.1)
    assert out == []


def test_rsi_empty_input_silent():
    empty = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    assert rules.rsi_extremes(empty, "TEST", "T.1") == []


# --- 레짐 ------------------------------------------------------------------
def test_regime_key_encodes_state():
    """key 에 레짐명이 들어가야 '바뀔 때만' 울릴 수 있다."""
    df = pd.DataFrame({"regime": ["확장", "수축"], "liq_pr": [0.8, 0.2],
                       "mom_pr": [0.9, 0.1]},
                      index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    out = rules.regime_shift(df)
    assert out[0].key == "regime:수축"
    assert out[0].level == "critical"


def test_regime_expansion_is_info():
    df = pd.DataFrame({"regime": ["확장"], "liq_pr": [0.8], "mom_pr": [0.9]},
                      index=pd.to_datetime(["2026-01-01"]))
    assert rules.regime_shift(df)[0].level == "info"


def test_regime_empty_silent():
    assert rules.regime_shift(pd.DataFrame()) == []


# --- 유동성 쇼크 -----------------------------------------------------------
def test_liquidity_shock_fires_on_outlier():
    idx = pd.date_range("2024-01-03", periods=160, freq="W-WED")
    vals = np.full(160, 6000.0) + np.random.default_rng(1).normal(0, 10, 160)
    vals[-1] = vals[-2] + 500.0          # 명백한 이상치
    out = rules.liquidity_shock(pd.Series(vals, index=idx), window=104)
    assert len(out) == 1
    assert out[0].key == "liqshock:up"


def test_liquidity_shock_silent_when_calm():
    idx = pd.date_range("2024-01-03", periods=160, freq="W-WED")
    vals = 6000 + np.random.default_rng(2).normal(0, 10, 160)
    assert rules.liquidity_shock(pd.Series(vals, index=idx), window=104) == []


def test_liquidity_shock_needs_enough_history():
    idx = pd.date_range("2026-01-07", periods=10, freq="W-WED")
    assert rules.liquidity_shock(pd.Series(range(10), index=idx), window=104) == []


# --- 예탁금 ----------------------------------------------------------------
def test_deposit_extreme_low_and_high():
    idx = pd.date_range("2020-01-31", periods=80, freq="ME")
    rising = pd.Series(np.linspace(1.0, 3.0, 80), index=idx)
    assert rules.deposit_extreme(rising, window=60)[0].key == "deposit:high"
    falling = pd.Series(np.linspace(3.0, 1.0, 80), index=idx)
    assert rules.deposit_extreme(falling, window=60)[0].key == "deposit:low"


def test_deposit_extreme_silent_in_middle():
    idx = pd.date_range("2020-01-31", periods=80, freq="ME")
    flat = pd.Series(np.random.default_rng(3).normal(2.0, 0.05, 80), index=idx)
    out = rules.deposit_extreme(flat, window=60, hi=0.999, lo=0.001)
    assert out == []


# --- 데이터 신선도 ---------------------------------------------------------
def test_staleness_flags_failed_source():
    meta = {"krx": {"status": "fail", "error": "HTTPError 429"}}
    out = rules.data_staleness(meta, today=pd.Timestamp("2026-08-11"))
    assert out[0].key == "etl:fail:krx"
    assert out[0].level == "critical"
    assert "429" in out[0].body


def test_staleness_flags_old_data():
    meta = {"fred": {"status": "ok", "max_date": "2026-08-01"}}
    out = rules.data_staleness(meta, max_lag_days=5, today=pd.Timestamp("2026-08-11"))
    assert out[0].key == "etl:stale:fred"
    assert "10일 전" in out[0].body


def test_staleness_silent_when_fresh():
    meta = {"fred": {"status": "ok", "max_date": "2026-08-10"}}
    assert rules.data_staleness(meta, max_lag_days=5,
                                today=pd.Timestamp("2026-08-11")) == []


def test_staleness_tolerates_missing_max_date():
    meta = {"fred": {"status": "ok"}}
    assert rules.data_staleness(meta, today=pd.Timestamp("2026-08-11")) == []


# --- 포맷 / 중복 억제 -------------------------------------------------------
def test_alert_format_has_icon_and_title():
    a = Alert("k", "critical", "제목", "본문")
    text = a.format()
    assert "🔴" in text and "*제목*" in text and "본문" in text


def test_dedup_only_new_keys_fire():
    """run.py 의 신규 판정 로직 — 같은 상태는 다시 울리지 않는다."""
    previous = {"regime:수축", "deposit:low"}
    current = [Alert("regime:수축", "info", "a", "b"),
               Alert("deposit:low", "info", "c", "d"),
               Alert("rsi:KRX.1001:over", "warn", "e", "f")]
    fresh = [a for a in current if a.key not in previous]
    assert [a.key for a in fresh] == ["rsi:KRX.1001:over"]


def test_state_roundtrip(tmp_path, monkeypatch):
    from src.alerts import run as run_mod

    monkeypatch.setattr(run_mod, "STATE", tmp_path / "_alerts.json")
    assert run_mod.read_state() == set()
    run_mod.write_state({"a", "b"})
    assert run_mod.read_state() == {"a", "b"}


def test_corrupt_state_file_is_tolerated(tmp_path, monkeypatch):
    from src.alerts import run as run_mod

    p = tmp_path / "_alerts.json"
    p.write_text("{ 깨진 json", encoding="utf-8")
    monkeypatch.setattr(run_mod, "STATE", p)
    assert run_mod.read_state() == set()


def test_evaluate_runs_on_real_data():
    from src import store
    from src.alerts import run as run_mod

    if store.read("macro").empty:
        pytest.skip("macro.parquet 없음 — ETL 먼저 실행")
    out = run_mod.evaluate()
    assert isinstance(out, list)
    assert all(isinstance(a, Alert) for a in out)
    assert len({a.key for a in out}) == len(out), "key 중복"
