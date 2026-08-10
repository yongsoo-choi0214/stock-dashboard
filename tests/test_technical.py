"""기술적 지표 검증 (CLAUDE.md §5.6 검증 결과를 테스트로 고정).

DoD의 'HTS/TradingView 대조'는 외부 화면이 필요해 자동화할 수 없으므로,
Wilder RSI의 정의를 재귀식으로 직접 손계산한 참조 구현과 대조한다.
§7.1(alpha=1/n vs span=n) 오답을 잡는 것이 이 파일의 핵심 목적이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators import technical as ta


@pytest.fixture(scope="module")
def walk() -> pd.Series:
    """재현 가능한 랜덤워크 600 영업일."""
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2022-01-03", periods=600)
    return pd.Series(100.0 + np.cumsum(rng.normal(0, 1, 600)), index=idx,
                     name="close")


# --- index 보존 (설계원칙 2) ----------------------------------------------
def test_index_preserved(walk):
    assert ta.macd(walk).index.equals(walk.index)
    assert ta.rsi(walk).index.equals(walk.index)
    assert ta.disparity(walk).index.equals(walk.index)
    assert ta.bollinger(walk).index.equals(walk.index)
    assert ta.zscore(walk).index.equals(walk.index)


def test_no_mutation(walk):
    """순수함수: 입력을 건드리지 않는다."""
    before = walk.copy()
    ta.macd(walk); ta.rsi(walk); ta.bollinger(walk)
    pd.testing.assert_series_equal(walk, before)


# --- RSI ------------------------------------------------------------------
def test_rsi_range(walk):
    r = ta.rsi(walk).dropna()
    assert r.between(0, 100).all()


def test_rsi_warmup(walk):
    assert ta.rsi(walk, 14).isna().sum() == 14


def test_rsi_monotonic_up():
    s = pd.Series(np.arange(1.0, 60.0))
    assert ta.rsi(s, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_monotonic_down():
    s = pd.Series(np.arange(60.0, 1.0, -1.0))
    assert ta.rsi(s, 14).iloc[-1] == pytest.approx(0.0)


def _rsi_reference(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder 원 정의를 루프로 직접 구현한 참조값.

    최초 n개 평균은 단순평균, 이후 avg = (avg*(n-1) + x) / n.
    pandas ewm(adjust=False) 는 첫 값을 시드로 쓰므로 완전히 같지는 않지만
    충분히 긴 구간에서는 수렴한다 → 후반부만 비교한다.
    """
    d = close.diff()
    gain = d.clip(lower=0.0).to_numpy()
    loss = (-d).clip(lower=0.0).to_numpy()
    ag = np.full(len(close), np.nan)
    al = np.full(len(close), np.nan)
    ag[n] = np.nanmean(gain[1:n + 1])
    al[n] = np.nanmean(loss[1:n + 1])
    for i in range(n + 1, len(close)):
        ag[i] = (ag[i - 1] * (n - 1) + gain[i]) / n
        al[i] = (al[i - 1] * (n - 1) + loss[i]) / n
    rs = ag / np.where(al == 0, np.nan, al)
    out = 100.0 - 100.0 / (1.0 + rs)
    return pd.Series(np.where(al == 0, 100.0, out), index=close.index)


def test_rsi_matches_wilder_reference(walk):
    """§7.1: 우리 구현이 Wilder 정의로 수렴하는지."""
    ours = ta.rsi(walk, 14)
    ref = _rsi_reference(walk, 14)
    tail = slice(200, None)   # 시드 차이가 소멸한 구간
    assert np.abs(ours[tail] - ref[tail]).max() < 0.01


def test_rsi_span_variant_is_materially_different(walk):
    """span=n 오답이 실제로 판정을 뒤집는 크기인지 (§7.1의 근거)."""
    d = walk.diff()
    g = d.clip(lower=0.0).ewm(span=14, adjust=False).mean()
    l = (-d).clip(lower=0.0).ewm(span=14, adjust=False).mean()
    wrong = 100 - 100 / (1 + g / l.replace(0.0, np.nan))
    diff = (ta.rsi(walk, 14) - wrong).abs().dropna()
    assert diff.max() > 5.0, "span=n 오답과 구분되지 않으면 구현이 잘못된 것"


# --- MACD -----------------------------------------------------------------
def test_macd_identity(walk):
    m = ta.macd(walk)
    assert np.abs(m["hist"] - (m["macd"] - m["signal"])).max() == pytest.approx(0.0)


def test_macd_constant_series_is_zero():
    s = pd.Series([100.0] * 100)
    m = ta.macd(s)
    assert np.abs(m["macd"]).max() == pytest.approx(0.0)


# --- 이격도 / 볼린저 -------------------------------------------------------
def test_disparity_flat_is_100():
    s = pd.Series([50.0] * 60)
    assert ta.disparity(s, 20).dropna().eq(100.0).all()


def test_disparity_warmup(walk):
    assert ta.disparity(walk, 20).isna().sum() == 19


def test_bollinger_ordering(walk):
    b = ta.bollinger(walk).dropna()
    assert (b["lower"] <= b["mid"]).all()
    assert (b["mid"] <= b["upper"]).all()


def test_bollinger_pct_b_at_bands(walk):
    """pct_b 는 하단에서 0, 상단에서 1 이어야 한다."""
    b = ta.bollinger(walk).dropna()
    recomputed = (walk[b.index] - b["lower"]) / (b["upper"] - b["lower"])
    pd.testing.assert_series_equal(b["pct_b"], recomputed, check_names=False)


# --- 정규화 ----------------------------------------------------------------
def test_zscore_constant_is_nan():
    """표준편차 0 이면 0으로 나누지 않고 NaN 이어야 한다."""
    s = pd.Series([7.0] * 300, name="x")
    assert ta.zscore(s, 252).dropna().empty


def test_pct_rank_range(walk):
    pr = ta.pct_rank(walk, 252).dropna()
    assert pr.between(0, 1).all()


# --- 실제 KOSPI 데이터 -----------------------------------------------------
def test_on_real_kospi():
    """data/prices.parquet 의 실제 KOSPI 종가로 회귀 확인."""
    from src import store

    df = store.read("prices")
    kospi = df[df["ticker"] == "KRX.1001"]
    if kospi.empty:
        pytest.skip("prices.parquet 에 KOSPI 없음 — ETL 먼저 실행")

    close = kospi.set_index("date")["close"].sort_index()
    r = ta.rsi(close, 14).dropna()
    assert r.between(0, 100).all()
    assert len(r) == len(close) - 14

    m = ta.macd(close)
    assert np.abs(m["hist"] - (m["macd"] - m["signal"])).max() < 1e-9
    assert ta.disparity(close, 20).dropna().between(50, 200).all()
