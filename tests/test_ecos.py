"""ECOS 파서 + 수집 결과 정합성.

네트워크가 필요한 테스트는 data/macro.parquet 을 읽는 방식으로 대체한다
(앱과 마찬가지로 테스트도 API를 두드리지 않는다).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import store
from src.etl import ecos


# --- 날짜 포맷 (§5.4 의 핵심 함정) ----------------------------------------
def test_fmt_time_by_cycle():
    ts = pd.Timestamp("2026-03-15")
    assert ecos.fmt_time(ts, "D") == "20260315"
    assert ecos.fmt_time(ts, "M") == "202603"
    assert ecos.fmt_time(ts, "Q") == "2026Q1"
    assert ecos.fmt_time(ts, "A") == "2026"


def test_fmt_time_rejects_unknown_cycle():
    with pytest.raises(ValueError):
        ecos.fmt_time(pd.Timestamp("2026-01-01"), "W")


def test_parse_time_daily():
    assert ecos.parse_time("20260315", "D") == pd.Timestamp("2026-03-15")


def test_parse_time_monthly_is_month_end():
    """월 데이터를 1일로 찍으면 일간 계열과 섞을 때 한 달 앞선 것처럼 보인다."""
    assert ecos.parse_time("202602", "M") == pd.Timestamp("2026-02-28")
    assert ecos.parse_time("202412", "M") == pd.Timestamp("2024-12-31")


def test_parse_time_quarterly_is_quarter_end():
    assert ecos.parse_time("2026Q1", "Q") == pd.Timestamp("2026-03-31")
    assert ecos.parse_time("2026Q4", "Q") == pd.Timestamp("2026-12-31")


def test_parse_time_annual():
    assert ecos.parse_time("2026", "A") == pd.Timestamp("2026-12-31")


def test_roundtrip_monthly():
    """fmt → parse 가 같은 달로 돌아와야 한다."""
    ts = pd.Timestamp("2026-06-30")
    assert ecos.parse_time(ecos.fmt_time(ts, "M"), "M") == ts


# --- 수집 결과 정합성 ------------------------------------------------------
@pytest.fixture(scope="module")
def macro() -> pd.DataFrame:
    df = store.read("macro")
    if df.empty or not df["series_id"].str.startswith("ecos.").any():
        pytest.skip("macro.parquet 에 ecos 계열 없음 — ETL 먼저 실행")
    return df


def series(macro: pd.DataFrame, sid: str) -> pd.Series:
    return macro[macro["series_id"] == sid].set_index("date")["value"].sort_index()


def test_expected_series_present(macro):
    have = set(macro["series_id"])
    for key in ["m2", "base_rate", "usdkrw", "investor_deposit",
                "kospi_marcap", "kospi_value", "foreign_net_kospi"]:
        assert f"ecos.{key}" in have, f"ecos.{key} 누락"


def test_units_are_in_jo_won(macro):
    """조원 통일이 깨지면 예탁금 지표가 조용히 틀린다 (설계원칙 4)."""
    assert 1 < series(macro, "ecos.investor_deposit").iloc[-1] < 500
    assert 100 < series(macro, "ecos.kospi_marcap").iloc[-1] < 20000
    assert 0 < series(macro, "ecos.kospi_value").iloc[-1] < 200
    assert 1000 < series(macro, "ecos.m2").iloc[-1] < 100000


def test_rates_and_fx_ranges(macro):
    assert 0 <= series(macro, "ecos.base_rate").iloc[-1] <= 25
    assert 500 < series(macro, "ecos.usdkrw").iloc[-1] < 3000


def test_foreign_flow_never_exceeds_trading_value(macro):
    """순매수 절대값이 그날 거래대금보다 클 수는 없다.
    단위가 어긋나면 여기서 바로 걸린다."""
    df = pd.concat({"net": series(macro, "ecos.foreign_net_kospi"),
                    "value": series(macro, "ecos.kospi_value")}, axis=1).dropna()
    assert not df.empty
    over = df[df["net"].abs() > df["value"]]
    assert over.empty, f"거래대금 초과 {len(over)}건: {over.head().to_dict()}"


def test_monthly_series_land_on_month_end(macro):
    for sid in ("ecos.investor_deposit", "ecos.m2"):
        d = series(macro, sid).index
        assert (d == d + pd.offsets.MonthEnd(0)).all(), f"{sid}: 월말 정규화 안 됨"


def test_deposit_ratio_is_plausible(macro):
    from src.indicators import liquidity as lq

    dep = series(macro, "ecos.investor_deposit")
    mcap = series(macro, "ecos.kospi_marcap").resample("ME").last()
    r = lq.deposit_ratio(dep, mcap)
    assert not r.empty
    assert r.between(0.1, 20).all(), f"예탁금/시총 범위 이탈: {r.min()}~{r.max()}"
