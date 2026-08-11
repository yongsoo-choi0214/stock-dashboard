"""IC / 레짐 검증.

이 파일의 핵심은 §7.2 look-ahead bias 가 실제로 막히는지다.
'미래를 아는 신호'를 넣었을 때 IC 가 1.0 이 나오고, 발표 시차를 적용하면
그게 무너지는지 확인한다. 무너지지 않으면 시차 로직이 죽어 있는 것이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research import ic, regime


@pytest.fixture(scope="module")
def px() -> pd.Series:
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2018-01-01", periods=1200)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1200))),
                     index=idx, name="close")


# --- 발표 시차 (§7.2) ------------------------------------------------------
def test_publication_lag_shifts_forward():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-01-31", "2026-02-28"]))
    out = ic.apply_publication_lag(s, 60)
    assert out.index[0] == pd.Timestamp("2026-01-31") + pd.Timedelta(days=60)
    assert out.iloc[0] == 1.0            # 값은 그대로, 알게 된 시점만 미룬다


def test_publication_lag_zero_is_identity():
    s = pd.Series([1.0], index=pd.to_datetime(["2026-01-31"]))
    pd.testing.assert_series_equal(ic.apply_publication_lag(s, 0), s)


def test_lag_for_reads_yaml():
    """series.yaml 에 선언된 시차를 코드가 실제로 읽는지."""
    assert ic.lag_for("ecos.m2") == 60
    assert ic.lag_for("fred.M2SL") == 30
    assert ic.lag_for("ecos.base_rate") == 0
    assert ic.lag_for("없음.XXX") == 0


def test_lag_reduces_lookahead_ic(px):
    """미래를 아는 신호는 IC 1.0, 시차를 걸면 무너져야 한다."""
    cheat = ic.forward_return(px, 20)          # 정의상 미래 정보
    assert ic.ic(cheat, px, 20) == pytest.approx(1.0, abs=1e-6)

    lagged = ic.ic(cheat, px, 20, lag_days=90)
    assert lagged < 0.5, f"시차를 걸었는데도 IC가 {lagged:.3f} — 시차 로직 확인 필요"


# --- forward_return -------------------------------------------------------
def test_forward_return_math():
    s = pd.Series([100.0, 110.0, 121.0], index=pd.bdate_range("2026-01-01", periods=3))
    f = ic.forward_return(s, 1)
    assert f.iloc[0] == pytest.approx(0.10)
    assert f.iloc[1] == pytest.approx(0.10)
    assert np.isnan(f.iloc[-1])              # 마지막은 미래가 없다


def test_forward_return_tail_is_nan(px):
    assert ic.forward_return(px, 20).tail(20).isna().all()


# --- align_signal ---------------------------------------------------------
def test_align_uses_ffill_not_interpolate():
    """보간은 미래 값을 섞는다. 반드시 '마지막으로 알려진 값'이어야 한다."""
    idx = pd.bdate_range("2026-01-01", periods=10)
    close = pd.Series(1.0, index=idx)
    sparse = pd.Series([10.0, 20.0], index=[idx[0], idx[9]])
    out = ic.align_signal(sparse, close)
    assert (out.iloc[0:9] == 10.0).all(), "중간이 보간되면 미래 정보 유입"
    assert out.iloc[9] == 20.0


def test_align_no_leading_fill():
    idx = pd.bdate_range("2026-01-01", periods=5)
    close = pd.Series(1.0, index=idx)
    sparse = pd.Series([7.0], index=[idx[3]])
    out = ic.align_signal(sparse, close)
    assert out.iloc[:3].isna().all(), "신호가 생기기 전 구간이 채워지면 안 된다"


# --- IC 계산 --------------------------------------------------------------
def test_spearman_equals_rank_pearson(px):
    """scipy 없이 계산한 값이 정의와 일치하는지."""
    a = pd.Series(np.random.default_rng(1).normal(size=200))
    b = pd.Series(np.random.default_rng(2).normal(size=200))
    assert ic._corr(a, b, "spearman") == pytest.approx(a.rank().corr(b.rank()))


def test_ic_of_noise_is_near_zero(px):
    noise = pd.Series(np.random.default_rng(99).normal(size=len(px)), index=px.index)
    assert abs(ic.ic(noise, px, 20)) < 0.15


def test_ic_handles_constant_signal(px):
    const = pd.Series(1.0, index=px.index)
    assert np.isnan(ic.ic(const, px, 20))


def test_ic_rejects_bad_method(px):
    with pytest.raises(ValueError):
        ic.ic(px, px, 20, method="kendall")


def test_rolling_ic_window_local_ranks(px):
    r = ic.rolling_ic(ic.forward_return(px, 20), px, 20, window=252)
    valid = r.dropna()
    assert not valid.empty
    assert valid.between(-1, 1).all()
    assert valid.iloc[-1] == pytest.approx(1.0, abs=1e-6)


def test_rolling_ic_short_input_is_empty(px):
    assert ic.rolling_ic(px, px, 20, window=99999).empty


def test_ic_table_shape(px):
    t = ic.ic_table({"a": px, "b": px.diff()}, px, horizons=(5, 20))
    assert list(t.index) == ["a", "b"]
    for c in ("IC_5d", "hit_5d", "IC_20d", "hit_20d", "n", "lag_days"):
        assert c in t.columns


def test_level_vs_change_diverge():
    """추세가 같은 두 레벨 계열은 허수 IC 를 만든다 — 변화량이 필요한 이유."""
    idx = pd.bdate_range("2010-01-01", periods=2000)
    trend = pd.Series(np.linspace(100, 300, 2000), index=idx)
    noise = np.random.default_rng(5).normal(0, 1, 2000).cumsum()
    price = pd.Series(np.linspace(1000, 3000, 2000) + noise * 5, index=idx)

    level_ic = ic.ic(trend, price, 60)
    change_ic = ic.ic(trend.diff(20), price, 60)
    # 부호가 아니라 크기가 문제다. 수익률은 레벨이 커질수록 작아지므로
    # 상승 추세끼리 붙여도 IC 부호는 음수로 나올 수 있다.
    assert abs(level_ic) > 0.3, f"추세 계열 레벨은 큰 허수 IC 를 낸다 (={level_ic:.3f})"
    assert abs(change_ic) < abs(level_ic) / 2, (
        f"변화량 IC({change_ic:.3f})가 레벨 IC({level_ic:.3f})만큼 크면 "
        "허수 여부를 구분할 수 없다")


# --- 레짐 -----------------------------------------------------------------
@pytest.fixture(scope="module")
def reg(px) -> pd.DataFrame:
    liq = pd.Series(np.linspace(100, 140, 60),
                    index=pd.date_range(px.index[0], periods=60, freq="W-WED"))
    liq = liq + np.random.default_rng(3).normal(0, 3, 60)
    return regime.classify(liq, px, window=120)


def test_regime_labels_are_known(reg):
    assert set(reg["regime"]).issubset(set(regime.ORDER))


def test_regime_quadrant_logic(reg):
    """라벨이 두 백분위의 사분면과 일치해야 한다."""
    for _, r in reg.iterrows():
        expect = regime.LABELS[(r["liq_pr"] > 0.5, r["mom_pr"] > 0.5)]
        assert r["regime"] == expect


def test_regime_percentiles_in_range(reg):
    assert reg["liq_pr"].between(0, 1).all()
    assert reg["mom_pr"].between(0, 1).all()


def test_summarize_weights_sum_to_100(reg, px):
    out = regime.summarize(reg, px, horizon=20)
    if out.empty:
        pytest.skip("표본 부족")
    assert out["비중%"].sum() == pytest.approx(100.0, abs=0.5)
    assert (out["상승확률%"].between(0, 100)).all()


def test_current_reports_streak(reg):
    cur = regime.current(reg)
    assert cur["regime"] in regime.ORDER
    assert cur["streak_days"] >= 1
    assert cur["date"] == reg.index[-1]


def test_empty_inputs_do_not_crash():
    empty = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    assert regime.classify(empty, empty).empty
    assert regime.current(pd.DataFrame()) == {}
