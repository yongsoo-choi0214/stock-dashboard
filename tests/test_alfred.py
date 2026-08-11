"""ALFRED 시점 데이터 검증.

핵심: available_from(공표일)이 date(관측일)보다 앞설 수 없다.
그게 깨지면 백테스트에 미래 정보가 들어간다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import store
from src.etl import alfred


def test_scrub_removes_api_key(monkeypatch):
    """에러 메시지에 키가 남으면 로그·티켓으로 새어나간다."""
    monkeypatch.setattr(alfred.settings, "FRED_API_KEY", "SECRET123")
    assert "SECRET123" not in alfred._scrub("url?api_key=SECRET123&x=1")
    assert "***" in alfred._scrub("url?api_key=SECRET123&x=1")


def test_scrub_tolerates_empty_key(monkeypatch):
    monkeypatch.setattr(alfred.settings, "FRED_API_KEY", "")
    assert alfred._scrub("no key here") == "no key here"


@pytest.fixture(scope="module")
def vintages() -> pd.DataFrame:
    df = store.read("vintages")
    if df.empty:
        pytest.skip("vintages.parquet 없음 — `run_all --only alfred` 먼저")
    return df


def test_schema(vintages):
    assert list(vintages.columns) == ["date", "series_id", "value", "available_from"]
    assert str(vintages["available_from"].dtype) == "datetime64[ns]"
    assert not vintages.duplicated(["date", "series_id"]).any()


def test_publication_never_precedes_observation(vintages):
    """★ 공표일이 관측일보다 빠르면 미래 정보다."""
    bad = vintages[vintages["available_from"] < vintages["date"]]
    assert bad.empty, f"공표일이 관측일보다 앞선 행 {len(bad)}건"


def test_expected_series(vintages):
    have = set(vintages["series_id"])
    assert {"fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"} <= have


def test_units_match_macro(vintages):
    """macro 와 같은 단위(십억 USD)로 정규화돼 있어야 비교가 성립한다."""
    w = vintages[vintages["series_id"] == "fred.WALCL"]["value"]
    assert 500 < w.max() < 12000, f"WALCL 최대 {w.max():,.0f} — scale 확인"


def test_point_in_time_indexed_by_publication(vintages):
    s = alfred.point_in_time(vintages, "fred.WALCL")
    assert not s.empty
    assert s.index.is_monotonic_increasing
    assert not s.index.duplicated().any()


def test_point_in_time_missing_series_is_empty(vintages):
    assert alfred.point_in_time(vintages, "fred.NOPE").empty


def test_publication_lag_is_small_but_nonzero(vintages):
    """FRED 주간 계열은 하루 뒤 공표된다. 0이면 시점 처리가 안 된 것이다."""
    w = vintages[vintages["series_id"] == "fred.WALCL"]
    lag = (w["available_from"] - w["date"]).dt.days
    assert lag.median() >= 1
    assert lag.max() < 60
