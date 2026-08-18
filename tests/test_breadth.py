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


# --- 실제 데이터: 검증 결과를 회귀로 고정 ---------------------------------
@pytest.fixture(scope="module")
def real():
    from config import settings
    from src import store

    p = store.read("prices")
    if p.empty:
        pytest.skip("prices 없음")
    sect = [f"KRX.{i['ticker']}" for i in settings.series_for("krx_sector")
            if i.get("group") == "sector"]
    mat = br.sector_matrix(p, sect)
    if mat.shape[1] < 40:
        pytest.skip(f"업종 {mat.shape[1]}개 — 44개 필요")
    k = p[p.ticker == "KRX.1001"].set_index("date")["close"].sort_index().astype("float64")
    return p, mat, k


def test_all_44_sectors_present(real):
    _, mat, _ = real
    assert mat.shape[1] == 44
    assert mat.shape[0] > 5000


def test_breadth_metrics_are_sane(real):
    p, mat, k = real
    assert br.pct_above_ma(mat, 200).dropna().between(0, 1).all()
    assert br.advance_ratio(mat, 20).dropna().between(0, 1).all()
    assert (br.dispersion(mat, 20).dropna() >= 0).all()
    assert br.divergence(k, br.pct_above_ma(mat, 200)).dropna().between(-1, 1).all()


def test_breadth_does_not_improve_index(real):
    """★ 검증 구간에서 고른 breadth 를 넣으면 테스트 구간이 나빠진다.

    47개 업종지수를 받으려고 KRX 차단까지 맞아가며 붙였지만, 3분할 검증의
    답은 '넣지 마라' 였다(-0.636 → -0.619). 이미 있는 모멘텀·이격도와 축이
    겹치기 때문으로 보인다.

    이 테스트가 깨지면(=개선되면) 좋은 소식이지만, 그땐 절차가 지켜졌는지
    부터 확인할 것. 테스트 구간을 보고 고르면 무엇이든 좋아진다.
    """
    from src.indicators import liquidity as lq
    from src import store
    from src.research import vulnerability as vu

    p, mat, k = real
    m, f = store.read("macro"), store.read("flows")

    def ms(x):
        return m[m.series_id == x].set_index("date")["value"].sort_index().astype("float64")

    fk = f[(f.market == "KOSPI") & (f.investor == "외국인합계")] \
        .set_index("date")["net_value"].sort_index() / 1e12
    nl = lq.us_net_liquidity(*[ms(x) for x in
                               ["fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"]])
    base = vu.build_components(
        k, turnover=ms("ecos.kospi_value"), foreign_flow=fk,
        market_cap=ms("ecos.kospi_marcap"), net_liquidity=nl,
        yield_curve=(ms("ecos.ktb10y") - ms("ecos.ktb3y")),
        exports=ms("ecos.exports"), margin_debt=ms("ecos.margin_debt"),
        pbr=ms("krx.1001_pbr"),
        credit_spread=(ms("ecos.corp_aa") - ms("ecos.ktb3y")))

    def al(s):
        s = s.dropna()
        return s.astype("float64").reindex(k.index.union(s.index)).ffill().reindex(k.index)

    tgt = vu.forward_max_drawdown(k, 60)
    nh = vu.near_high(k)
    tst = (k.index >= pd.Timestamp("2021-01-01")) & nh.reindex(k.index).fillna(False)

    def ic(cols):
        c = base.copy()
        for n, s in cols.items():
            c[n] = s
        r = vu.walk_forward(c, k, split="2016-01-01", condition=nh)
        return vu.spearman(r["index"][tst], tgt[tst])

    base_ic = ic({})
    with_breadth = ic({"200일선 위 업종비율": al(br.pct_above_ma(mat, 200)),
                       "소형-대형": al(br.small_vs_large(p, 60))})
    assert with_breadth > base_ic, (
        f"breadth 를 넣으니 테스트가 개선됐다 ({with_breadth:+.3f} vs "
        f"{base_ic:+.3f}) — 절차가 지켜졌는지 먼저 확인할 것")
