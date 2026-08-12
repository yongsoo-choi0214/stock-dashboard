"""계절성 · 변동성 레짐 · 상관 검증."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import store
from src.research import seasonality as sea


def test_monthly_returns_uses_month_end():
    idx = pd.bdate_range("2026-01-01", "2026-03-31")
    c = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    r = sea.monthly_returns(c)
    assert len(r) == 2                       # 2월, 3월 (1월은 기준)
    assert (r > 0).all()


def test_monthly_stats_has_twelve_rows(real_close):
    st = sea.monthly_stats(real_close)
    assert len(st) == 12
    assert list(st.index) == sea.MONTHS
    assert (st["표본"] > 10).all()
    assert st["상승확률%"].between(0, 100).all()


def test_monthly_stats_empty_input():
    assert sea.monthly_stats(pd.Series(dtype="float64",
                                       index=pd.DatetimeIndex([]))).empty


def test_realized_vol_is_annualized():
    """일간 std 1% → 연율 약 15.9%."""
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2020-01-01", periods=300)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))), index=idx)
    rv = sea.realized_vol(c, 20).dropna()
    assert 0.10 < rv.median() < 0.25


def test_vol_regime_monotonic_buckets(real_close):
    v = sea.vol_regime_stats(real_close)
    assert len(v) == 5
    # 분위가 올라갈수록 변동성 구간도 올라가야 한다
    assert v["변동성 하한%"].is_monotonic_increasing
    assert (v["일수"] > 100).all()


def test_vol_regime_needs_enough_data():
    idx = pd.bdate_range("2026-01-01", periods=30)
    assert sea.vol_regime_stats(pd.Series(range(30), index=idx)).empty


def test_current_vol_percentile_range(real_close):
    rv, pr = sea.current_vol_percentile(real_close)
    assert rv > 0
    assert 0 <= pr <= 1


# --- 상관 ------------------------------------------------------------------
@pytest.fixture(scope="module")
def prices():
    p = store.read("prices")
    if p.empty:
        pytest.skip("prices.parquet 없음")
    return p


@pytest.fixture(scope="module")
def real_close(prices):
    return prices[prices.ticker == "KRX.1001"].set_index("date")["close"] \
        .sort_index().astype("float64")


def test_correlation_is_square_and_bounded(prices):
    tk = sorted(prices["ticker"].unique())
    c = sea.correlation(prices, tk, start=pd.Timestamp("2021-01-01"))
    assert c.shape == (len(tk), len(tk))
    assert np.allclose(np.diag(c), 1.0)
    assert c.values.min() >= -1.0 and c.values.max() <= 1.0


def test_session_alignment_raises_us_correlation(prices):
    """★ 한국장이 닫힌 뒤 거래되는 자산은 같은 날로 비교하면 관계가 과소평가된다."""
    tk = sorted(prices["ticker"].unique())
    kw = dict(start=pd.Timestamp("2021-01-01"))
    raw = sea.correlation(prices, tk, align_sessions=False, **kw)
    adj = sea.correlation(prices, tk, align_sessions=True, **kw)
    for us in ["YF.^GSPC", "YF.^SOX", "YF.^IXIC"]:
        if us in raw.columns:
            assert adj.loc["KRX.1001", us] > raw.loc["KRX.1001", us] + 0.1, (
                f"{us}: 정렬 후 상관이 오르지 않았다")


def test_session_alignment_leaves_same_timezone_alone(prices):
    """같은 시간대 자산(닛케이·코스닥)은 정렬해도 변하지 않아야 한다."""
    tk = sorted(prices["ticker"].unique())
    kw = dict(start=pd.Timestamp("2021-01-01"))
    raw = sea.correlation(prices, tk, align_sessions=False, **kw)
    adj = sea.correlation(prices, tk, align_sessions=True, **kw)
    for asia in ["YF.^N225", "KRX.2001"]:
        if asia in raw.columns:
            assert abs(adj.loc["KRX.1001", asia]
                       - raw.loc["KRX.1001", asia]) < 0.05


def test_correlation_requires_min_obs(prices):
    tk = sorted(prices["ticker"].unique())
    assert sea.correlation(prices, tk, start=pd.Timestamp("2026-08-10"),
                           min_obs=60).empty
