"""store 계약 검증: §3 스키마/dtype 강제, upsert 멱등성, 정정분 우선."""
from __future__ import annotations

import pandas as pd
import pytest

from src import store


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    """실제 data/ 를 건드리지 않도록 임시 디렉터리로 격리."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "META_JSON", tmp_path / "_meta.json")
    return tmp_path


def _macro(dates, sid="fred.X", vals=None):
    vals = vals if vals is not None else list(range(len(dates)))
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "series_id": sid,
        "value": [float(v) for v in vals],
    })


# --- 스키마 ---------------------------------------------------------------
def test_read_missing_returns_empty_schema():
    df = store.read("macro")
    assert df.empty
    assert list(df.columns) == ["date", "series_id", "value"]


def test_write_enforces_dtypes():
    store.write("macro", _macro(["2026-01-01", "2026-01-02"]))
    df = store.read("macro")
    assert str(df["date"].dtype) == "datetime64[ns]"
    assert str(df["series_id"].dtype) == "string"
    assert str(df["value"].dtype) == "float64"
    assert isinstance(df.index, pd.RangeIndex)


def test_missing_column_raises():
    with pytest.raises(ValueError, match="필수 컬럼 누락"):
        store.write("macro", pd.DataFrame({"date": [], "value": []}))


def test_extra_column_dropped():
    df = _macro(["2026-01-01"])
    df["junk"] = 1
    store.write("macro", df)
    assert list(store.read("macro").columns) == ["date", "series_id", "value"]


def test_date_normalized_to_midnight():
    df = _macro(["2026-01-01 15:30:00"])
    store.write("macro", df)
    assert store.read("macro")["date"].iloc[0] == pd.Timestamp("2026-01-01")


def test_tz_aware_date_stripped():
    df = _macro(["2026-01-01"])
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize("Asia/Seoul")
    store.write("macro", df)
    assert str(store.read("macro")["date"].dtype) == "datetime64[ns]"


# --- upsert ---------------------------------------------------------------
def test_upsert_is_idempotent():
    """같은 데이터를 두 번 넣어도 행 수가 늘지 않는다 (Phase 2 DoD)."""
    df = _macro(["2026-01-01", "2026-01-02", "2026-01-03"])
    store.upsert("macro", df)
    n1 = len(store.read("macro"))
    store.upsert("macro", df)
    assert len(store.read("macro")) == n1 == 3


def test_upsert_new_value_wins():
    """사후 정정(revision): 나중에 받은 값이 이겨야 한다."""
    store.upsert("macro", _macro(["2026-01-01"], vals=[10.0]))
    store.upsert("macro", _macro(["2026-01-01"], vals=[99.0]))
    df = store.read("macro")
    assert len(df) == 1
    assert df["value"].iloc[0] == 99.0


def test_upsert_appends_new_dates():
    store.upsert("macro", _macro(["2026-01-01", "2026-01-02"]))
    store.upsert("macro", _macro(["2026-01-03"]))
    assert len(store.read("macro")) == 3


def test_upsert_separates_series():
    store.upsert("macro", _macro(["2026-01-01"], sid="fred.A"))
    store.upsert("macro", _macro(["2026-01-01"], sid="fred.B"))
    assert len(store.read("macro")) == 2


def test_upsert_empty_is_noop():
    store.upsert("macro", _macro(["2026-01-01"]))
    store.upsert("macro", store.empty("macro"))
    assert len(store.read("macro")) == 1


def test_sorted_by_key():
    store.upsert("macro", _macro(["2026-03-01", "2026-01-01", "2026-02-01"]))
    d = store.read("macro")["date"]
    assert d.is_monotonic_increasing


# --- last_date ------------------------------------------------------------
def test_last_date_none_when_empty():
    assert store.last_date("macro") is None


def test_last_date_with_filter():
    store.upsert("macro", _macro(["2026-01-01", "2026-01-05"], sid="fred.A"))
    store.upsert("macro", _macro(["2026-01-09"], sid="fred.B"))
    assert store.last_date("macro") == pd.Timestamp("2026-01-09")
    assert store.last_date("macro", series_id="fred.A") == pd.Timestamp("2026-01-05")


# --- prices / flows 스키마 -------------------------------------------------
def test_prices_schema():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"]), "ticker": ["KRX.1001"],
        "open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [100],
    })
    store.write("prices", df)
    out = store.read("prices")
    assert str(out["volume"].dtype) == "float64"   # int 로 들어와도 float 강제
    assert str(out["ticker"].dtype) == "string"


def test_flows_schema():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"]), "market": ["KOSPI"],
        "investor": ["외국인"], "net_value": [-1234.0],
    })
    store.write("flows", df)
    assert store.read("flows")["investor"].iloc[0] == "외국인"


# --- _meta.json -----------------------------------------------------------
def test_update_meta_roundtrip():
    store.update_meta("fred", "ok", rows=100, max_date="2026-08-07")
    meta = store.read_meta()
    assert meta["fred"]["status"] == "ok"
    assert meta["fred"]["rows"] == 100
    assert "last_run" in meta["fred"]


def test_update_meta_preserves_other_sources():
    store.update_meta("fred", "ok", rows=1)
    store.update_meta("krx", "fail", error="HTTPError 429")
    meta = store.read_meta()
    assert set(meta) == {"fred", "krx"}
    assert meta["krx"]["status"] == "fail"
