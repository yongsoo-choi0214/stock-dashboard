"""KRX 수급 데이터 정합성.

가장 강력한 검증은 회계 항등식이다: 하루의 투자자별 순매수를 모두 더하면
정확히 0이어야 한다. 한쪽이 사면 다른 쪽이 판다. 0이 아니면 구분이 빠졌거나
집계 단위가 섞인 것이다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import store


@pytest.fixture(scope="module")
def flows() -> pd.DataFrame:
    df = store.read("flows")
    if df.empty:
        pytest.skip("flows.parquet 비어 있음 — KRX 계정 필요")
    return df


def test_schema(flows):
    assert list(flows.columns) == ["date", "market", "investor", "net_value"]
    assert str(flows["date"].dtype) == "datetime64[ns]"
    assert str(flows["net_value"].dtype) == "float64"
    assert not flows.duplicated(["date", "market", "investor"]).any()


def test_markets_present(flows):
    assert {"KOSPI", "KOSDAQ"} <= set(flows["market"])


def test_investors_present(flows):
    """차트가 기대하는 구분 이름이 실제로 있는지.
    '외국인' vs '외국인합계' 처럼 어긋나면 계열이 조용히 사라진다."""
    assert {"개인", "외국인합계", "기관합계", "기타법인"} <= set(flows["investor"])


def test_net_values_sum_to_zero(flows):
    """★ 회계 항등식. 부동소수 오차를 감안해도 거래대금 대비 무시할 수준이어야 한다."""
    for market in flows["market"].unique():
        w = (flows[flows["market"] == market]
             .pivot(index="date", columns="investor", values="net_value"))
        residual = w.sum(axis=1).abs()
        assert residual.max() < 1.0, (
            f"{market}: 순매수 합이 0이 아님 (최대 {residual.max():,.0f}원) — "
            "투자자 구분이 빠졌거나 집계 단위가 섞였다")


def test_is_daily_not_aggregated(flows):
    """구간 합계 API를 잘못 쓰면 연 1행이 된다 — 그걸 잡는다."""
    kospi = flows[(flows["market"] == "KOSPI") & (flows["investor"] == "개인")]
    span_years = (kospi["date"].max() - kospi["date"].min()).days / 365.25
    assert len(kospi) / max(span_years, 1) > 200, (
        f"연 {len(kospi) / span_years:.0f}행 — 일간이 아니다. "
        "get_market_trading_value_by_investor(구간합계) 를 쓰고 있지 않은지 확인"
    )


def test_magnitudes_are_won(flows):
    """단위가 원인지. 억원으로 들어오면 자릿수가 8자리 작아진다."""
    big = flows["net_value"].abs().max()
    assert big > 1e11, f"최대 순매수가 {big:,.0f} — 원 단위가 맞는지 확인"
