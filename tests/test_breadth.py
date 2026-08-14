"""시장 폭 · 분산 매도일 검증. 합성 데이터로 정의를 고정한다."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research import breadth as br


def _mat(n_days=400, n_series=20, seed=1, drift=0.0):
    idx = pd.bdate_range("2024-01-01", periods=n_days)
    rng = np.random.default_rng(seed)
    data = {f"KRX.{1005+i}": 100 * np.exp(np.cumsum(
        rng.normal(drift, 0.01, n_days))) for i in range(n_series)}
    return pd.DataFrame(data, index=idx)


# --- pct_above_ma ----------------------------------------------------------
def test_all_rising_gives_one():
    """전부 상승 추세면 100% 가 이동평균 위."""
    idx = pd.bdate_range("2024-01-01", periods=400)
    mat = pd.DataFrame({f"s{i}": np.linspace(100, 200, 400) for i in range(20)},
                       index=idx)
    out = br.pct_above_ma(mat, window=200).dropna()
    assert out.iloc[-1] == pytest.approx(1.0)


def test_all_falling_gives_zero():
    idx = pd.bdate_range("2024-01-01", periods=400)
    mat = pd.DataFrame({f"s{i}": np.linspace(200, 100, 400) for i in range(20)},
                       index=idx)
    out = br.pct_above_ma(mat, window=200).dropna()
    assert out.iloc[-1] == pytest.approx(0.0)


def test_half_rising_gives_half():
    idx = pd.bdate_range("2024-01-01", periods=400)
    cols = {}
    for i in range(10):
        cols[f"up{i}"] = np.linspace(100, 200, 400)
        cols[f"dn{i}"] = np.linspace(200, 100, 400)
    out = br.pct_above_ma(pd.DataFrame(cols, index=idx), window=200).dropna()
    assert out.iloc[-1] == pytest.approx(0.5)


def test_pct_above_ma_is_bounded():
    out = br.pct_above_ma(_mat(), window=200).dropna()
    assert out.between(0, 1).all()


def test_needs_minimum_series():
    """계열이 몇 개 안 되면 폭이라 부를 수 없다."""
    assert br.pct_above_ma(_mat(n_series=3), window=200,
                           min_series=10).dropna().empty


def test_empty_input_is_empty():
    assert br.pct_above_ma(pd.DataFrame()).empty
    assert br.advance_ratio(pd.DataFrame()).empty
    assert br.dispersion(pd.DataFrame()).empty


# --- advance_ratio / dispersion --------------------------------------------
def test_advance_ratio_bounded():
    out = br.advance_ratio(_mat(), window=20).dropna()
    assert out.between(0, 1).all()


def test_dispersion_zero_when_identical():
    """모든 업종이 똑같이 움직이면 분산은 0."""
    idx = pd.bdate_range("2024-01-01", periods=200)
    same = np.linspace(100, 150, 200)
    mat = pd.DataFrame({f"s{i}": same for i in range(20)}, index=idx)
    assert br.dispersion(mat, window=20).dropna().max() == pytest.approx(0.0)


def test_dispersion_positive_when_mixed():
    assert br.dispersion(_mat(), window=20).dropna().max() > 0


# --- small_vs_large --------------------------------------------------------
def test_small_vs_large_sign():
    idx = pd.bdate_range("2024-01-01", periods=200)
    rows = []
    for tk, path in [("KRX.1004", np.linspace(100, 150, 200)),   # 소형 강세
                     ("KRX.1002", np.linspace(100, 110, 200))]:  # 대형 약세
        rows.append(pd.DataFrame({"date": idx, "ticker": tk, "close": path}))
    prices = pd.concat(rows, ignore_index=True)
    out = br.small_vs_large(prices, window=60).dropna()
    assert out.iloc[-1] > 0, "소형주가 강한데 값이 음수다"


def test_small_vs_large_missing_input():
    empty = pd.DataFrame({"date": [], "ticker": [], "close": []})
    assert br.small_vs_large(empty).empty


# --- divergence ------------------------------------------------------------
def test_divergence_detects_index_up_breadth_down():
    """지수는 오르는데 폭은 꺾이는 상태에서 양수가 커야 한다."""
    idx = pd.bdate_range("2024-01-01", periods=300)
    px = pd.Series(np.linspace(100, 200, 300), index=idx)
    bd = pd.Series(np.linspace(0.9, 0.3, 300), index=idx)
    out = br.divergence(px, bd, window=100).dropna()
    assert out.iloc[-1] > 0.5


# --- distribution days -----------------------------------------------------
def test_distribution_day_definition():
    """하락 + 거래량 증가 = 분산 매도일."""
    idx = pd.bdate_range("2026-01-01", periods=5)
    close = pd.Series([100.0, 99.0, 99.5, 98.0, 98.1], index=idx)
    vol = pd.Series([100.0, 200.0, 150.0, 300.0, 100.0], index=idx)
    out = br.distribution_days(close, vol, window=5)
    # 2일차(-1%, 거래량↑)와 4일차(-1.5%, 거래량↑) 두 번
    assert out.iloc[-1] == 2


def test_rising_day_is_not_distribution():
    idx = pd.bdate_range("2026-01-01", periods=3)
    close = pd.Series([100.0, 105.0, 110.0], index=idx)
    vol = pd.Series([100.0, 300.0, 500.0], index=idx)
    assert br.distribution_days(close, vol, window=3).iloc[-1] == 0


def test_falling_on_lower_volume_is_not_distribution():
    """거래량이 줄며 빠지는 건 분산 매도가 아니다."""
    idx = pd.bdate_range("2026-01-01", periods=3)
    close = pd.Series([100.0, 98.0, 96.0], index=idx)
    vol = pd.Series([300.0, 200.0, 100.0], index=idx)
    assert br.distribution_days(close, vol, window=3).iloc[-1] == 0


def test_zero_volume_days_excluded():
    """업종지수처럼 거래량이 0 인 계열은 세지 않는다."""
    idx = pd.bdate_range("2026-01-01", periods=3)
    close = pd.Series([100.0, 98.0, 96.0], index=idx)
    vol = pd.Series([0.0, 0.0, 0.0], index=idx)
    assert br.distribution_days(close, vol, window=3).iloc[-1] == 0
