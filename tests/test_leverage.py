"""레버리지 위험 검증. 수식이 틀리면 사람이 돈을 잃는다."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import store
from src.research import flows_view as fv
from src.research import leverage as lv


# --- 반대매매 임계 ----------------------------------------------------------
@pytest.mark.parametrize("lev,expected", [
    (2.0, 0.30),      # 담보유지 140%: 1 - 1.4*(1/2) = 0.30
    (1.5, 1 - 1.4 * (0.5 / 1.5)),
    (3.0, 1 - 1.4 * (2 / 3)),
])
def test_margin_call_formula(lev, expected):
    assert lv.margin_call_drawdown(lev, 1.40) == pytest.approx(expected, abs=1e-9)


def test_no_leverage_no_margin_call():
    assert lv.margin_call_drawdown(1.0) == 1.0
    assert lv.margin_call_drawdown(0.8) == 1.0


def test_higher_leverage_lower_threshold():
    """레버리지가 높을수록 더 적게 빠져도 청산된다."""
    xs = [lv.margin_call_drawdown(L) for L in (1.5, 2.0, 2.5, 3.0)]
    assert xs == sorted(xs, reverse=True)


def test_higher_maintenance_is_stricter():
    assert (lv.margin_call_drawdown(2.0, 1.70)
            < lv.margin_call_drawdown(2.0, 1.30))


def test_threshold_never_negative():
    """극단 조합에서도 음수가 나오면 안 된다."""
    assert lv.margin_call_drawdown(10.0, 1.70) >= 0.0


def test_leverage_table_columns():
    t = lv.leverage_table()
    assert {"레버리지", "융자 비중%", "반대매매 하락률%"} <= set(t.columns)
    assert len(t) == 6


# --- 실제 데이터 ------------------------------------------------------------
@pytest.fixture(scope="module")
def kospi():
    p = store.read("prices")
    s = p[p.ticker == "KRX.1001"].set_index("date")["close"]
    if s.empty:
        pytest.skip("KOSPI 없음")
    return s.sort_index().astype("float64")


def test_hit_rate_rises_with_leverage(kospi):
    t = lv.survival_by_leverage(kospi)
    probs = t.iloc[:, -1].tolist()
    assert probs == sorted(probs), "레버리지가 높은데 확률이 낮으면 계산이 틀렸다"


def test_two_x_survives_more_than_three_x(kospi):
    from src.research import vulnerability as vu

    ep = vu.episodes(kospi)
    k2 = (ep["depth"] <= -lv.margin_call_drawdown(2.0)).sum()
    k3 = (ep["depth"] <= -lv.margin_call_drawdown(3.0)).sum()
    assert k2 < k3 or (k2 == k3 == len(ep))


def test_local_drawdown_is_shallower_than_alltime(kospi):
    """1년 롤링 고점 대비 낙폭은 전고점 대비보다 얕거나 같다."""
    from src.research import vulnerability as vu

    a = vu.drawdown(kospi)
    b = lv.local_drawdown(kospi)
    both = pd.concat({"a": a, "b": b}, axis=1).dropna()
    assert (both["b"] >= both["a"] - 1e-9).all()


def test_local_episodes_find_more_than_alltime(kospi):
    """★ 전고점 기준은 2022~2025 를 하나로 묶어 조정을 놓친다."""
    from src.research import vulnerability as vu

    n_all = len(vu.episodes(kospi, threshold=-0.10))
    n_loc = len(lv.local_episodes(kospi, threshold=-0.10))
    assert n_loc > n_all, "롤링 고점 기준이 더 많이 잡아야 한다"


def test_conditional_risk_shape(kospi):
    from src.research import vulnerability as vu

    idx = pd.Series(np.linspace(0, 1, len(kospi)), index=kospi.index)
    out = lv.conditional_risk(idx, kospi, leverage=2.5,
                              condition=vu.near_high(kospi))
    if out.empty:
        pytest.skip("표본 부족")
    assert len(out) == 5
    assert out.iloc[:, -1].between(0, 100).all()


def test_checklist_hides_vulnerability_when_not_applicable():
    """이미 조정 중이면 취약성 숫자를 내밀지 않는다."""
    out = lv.deleverage_checklist(drawdown=-0.30, vulnerability=0.9,
                                  applicable=False, margin_pr=0.5, leverage=2.0)
    vuln = [v for lvl, lab, v in out if lab == "취약성"][0]
    assert "적용 밖" in vuln and "0.9" not in vuln


def test_checklist_flags_deep_drawdown():
    out = lv.deleverage_checklist(drawdown=-0.30, vulnerability=None,
                                  applicable=False, margin_pr=None, leverage=2.0)
    assert any(lvl == "bad" for lvl, _, _ in out)


# --- 누적 수급 --------------------------------------------------------------
@pytest.fixture(scope="module")
def flows():
    f = store.read("flows")
    if f.empty:
        pytest.skip("flows 없음")
    return f


def test_cumulative_sums_to_zero_across_investors(flows):
    """순매수 합이 0이므로 누적합도 모든 시점에서 0이어야 한다."""
    c = fv.cumulative(flows, "KOSPI")
    assert not c.empty
    assert c.sum(axis=1).abs().max() < 1e-6


def test_cumulative_start_resets(flows):
    late = fv.cumulative(flows, "KOSPI", start=pd.Timestamp("2024-01-01"))
    assert abs(late.iloc[0].sum()) < 1e-6
    assert late.index.min() >= pd.Timestamp("2024-01-01")


def test_deposit_vs_margin_ratio():
    idx = pd.date_range("2026-01-31", periods=3, freq="ME")
    d = pd.Series([100.0, 100.0, 100.0], index=idx)
    m = pd.Series([20.0, 30.0, 40.0], index=idx)
    out = fv.deposit_vs_margin(d, m)
    assert out["신용/예탁금%"].tolist() == [20.0, 30.0, 40.0]
