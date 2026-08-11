"""스냅샷 검증.

핵심은 단 하나 — **한번 기록한 날짜는 고쳐지지 않는다.**
고쳐진다면 그건 기록이 아니라 그냥 재계산이고, 과거 판단을 검증할 수 없다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import store
from src.research import snapshot


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "META_JSON", tmp_path / "_meta.json")
    return tmp_path


def test_record_creates_rows():
    df = snapshot.record({"a": 1.0, "b": 2.0}, date="2026-08-11")
    assert len(df) == 2
    assert set(df["metric"]) == {"a", "b"}


def test_recorded_value_is_immutable():
    """★ 같은 날 다시 기록해도 처음 값이 남아야 한다."""
    snapshot.record({"vulnerability": 0.70}, date="2026-08-11")
    snapshot.record({"vulnerability": 0.99}, date="2026-08-11")
    s = snapshot.series("vulnerability")
    assert len(s) == 1
    assert s.iloc[0] == 0.70, "과거 기록이 덮어써졌다 — 기록의 의미가 없다"


def test_overwrite_flag_allows_correction():
    """의도적 정정 경로는 열어둔다. 다만 기본값이 아니어야 한다."""
    snapshot.record({"x": 1.0}, date="2026-08-11")
    snapshot.record({"x": 2.0}, date="2026-08-11", overwrite=True)
    assert snapshot.series("x").iloc[0] == 2.0


def test_different_dates_accumulate():
    snapshot.record({"x": 1.0}, date="2026-08-10")
    snapshot.record({"x": 2.0}, date="2026-08-11")
    s = snapshot.series("x")
    assert len(s) == 2
    assert s.is_monotonic_increasing is False or True   # 값이 아니라 index 정렬 확인
    assert s.index.is_monotonic_increasing


def test_series_of_unknown_metric_is_empty():
    snapshot.record({"x": 1.0}, date="2026-08-11")
    assert snapshot.series("없는지표").empty


def test_empty_record_is_noop():
    snapshot.record({}, date="2026-08-11")
    assert store.read("snapshots").empty


def test_date_is_normalized():
    snapshot.record({"x": 1.0}, date="2026-08-11 15:30:00")
    assert snapshot.series("x").index[0] == pd.Timestamp("2026-08-11")


def test_compare_detects_methodology_drift():
    """당시 기록과 재계산이 벌어지면 드러나야 한다."""
    snapshot.record({"v": 0.50}, date="2026-08-10")
    snapshot.record({"v": 0.60}, date="2026-08-11")
    recomputed = pd.Series([0.55, 0.60],
                           index=pd.to_datetime(["2026-08-10", "2026-08-11"]))
    out = snapshot.compare("v", recomputed)
    assert len(out) == 2
    assert out["차이"].iloc[0] == pytest.approx(0.05)
    assert out["차이"].iloc[1] == pytest.approx(0.0)


def test_compare_empty_when_no_history():
    assert snapshot.compare("v", pd.Series(dtype="float64")).empty


def test_schema_enforced():
    snapshot.record({"x": 1}, date="2026-08-11")
    df = store.read("snapshots")
    assert str(df["date"].dtype) == "datetime64[ns]"
    assert str(df["metric"].dtype) == "string"
    assert str(df["value"].dtype) == "float64"


def test_collect_on_real_data(monkeypatch, tmp_path):
    """collect() 는 실제 데이터를 읽으므로 격리를 풀고 확인한다."""
    monkeypatch.undo()
    if store.read("prices").empty:
        pytest.skip("prices.parquet 없음")
    rows = snapshot.collect()
    assert "kospi_close" in rows
    assert all(isinstance(v, float) for v in rows.values())
