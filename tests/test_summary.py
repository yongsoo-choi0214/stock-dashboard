"""오늘 요약 · 데이터 카탈로그 검증."""
from __future__ import annotations

import pandas as pd
import pytest

from src.research import catalog, summary


def _idx(n):
    return pd.bdate_range("2026-08-01", periods=n)


# --- 요약 ------------------------------------------------------------------
def test_empty_inputs_produce_nothing():
    assert summary.build() == []


def test_kospi_direction():
    up = pd.Series([100.0, 105.0], index=_idx(2))
    assert summary.build(close=up)[0][0] == "ok"
    down = pd.Series([100.0, 95.0], index=_idx(2))
    assert summary.build(close=down)[0][0] == "bad"


def test_vulnerability_hidden_when_not_applicable():
    """★ 적용 밖이면 숫자를 내밀지 않는다 — 요약이 확신을 과장하면 안 된다."""
    v = pd.Series([0.9], index=_idx(1))
    rows = summary.build(vulnerability=v, applicable=False)
    assert rows[0][2] == "적용 밖 (이미 조정 진행 중)"
    assert "0.9" not in rows[0][2]


def test_vulnerability_shown_when_applicable():
    v = pd.Series([0.9], index=_idx(1))
    lvl, _, val = summary.build(vulnerability=v, applicable=True)[0]
    assert lvl == "bad" and val == "0.90"


def test_streak_uses_last_day_sign():
    """5일 합계가 순매도여도 마지막 날이 순매수면 '연속 순매수' 로 센다.
    합계 부호로 세면 '0일 연속' 같은 문구가 나온다."""
    f = pd.Series([-100.0, -100.0, -100.0, 10.0, 20.0], index=_idx(5))
    _, _, val = summary.build(foreign_flow=f)[0]
    assert "2일 연속 순매수" in val


def test_streak_counts_consecutive_sells():
    f = pd.Series([50.0, -10.0, -20.0, -30.0], index=_idx(4))
    _, _, val = summary.build(foreign_flow=f)[0]
    assert "3일 연속 순매도" in val


def test_inverted_curve_is_flagged():
    yc = pd.Series([-0.3], index=_idx(1))
    lvl, _, val = summary.build(yield_curve=yc)[0]
    assert lvl == "bad" and "역전" in val


def test_normal_curve_not_flagged():
    yc = pd.Series([0.5], index=_idx(1))
    lvl, _, val = summary.build(yield_curve=yc)[0]
    assert lvl == "ok" and "역전" not in val


def test_bsi_below_100_is_warn():
    lvl, _, val = summary.build(bsi=pd.Series([77.0], index=_idx(1)))[0]
    assert lvl == "warn" and "비관" in val


def test_failed_source_surfaces():
    rows = summary.build(meta={"krx": {"status": "fail"}, "fred": {"status": "ok"}})
    assert rows[-1][0] == "bad" and "krx" in rows[-1][2]


def test_drawdown_levels():
    for dd, lvl in [(-0.01, "ok"), (-0.10, "warn"), (-0.30, "bad")]:
        s = pd.Series([dd], index=_idx(1))
        assert summary.build(drawdown=s)[0][0] == lvl


# --- 카탈로그 ---------------------------------------------------------------
@pytest.fixture(scope="module")
def cat():
    c = catalog.build()
    if c.empty:
        pytest.skip("데이터 없음 — ETL 먼저 실행")
    return c


def test_catalog_covers_all_stores(cat):
    assert set(cat["저장소"]) >= {"macro", "prices", "flows"}
    assert len(cat) > 50


def test_catalog_has_no_unnamed_series(cat):
    """이름이 '—' 인 계열이 있으면 series.yaml 과 코드가 갈라진 것이다."""
    unnamed = cat[cat["이름"] == "—"]
    assert unnamed.empty, f"이름 없는 계열: {list(unnamed['계열'])}"


def test_catalog_dates_are_sane(cat):
    assert (cat["시작"] <= cat["최신"]).all()
    assert (cat["관측"] > 0).all()


def test_staleness_flags_old_series(cat):
    old = catalog.staleness(cat, today=pd.Timestamp("2026-08-12"), warn_days=7)
    assert "지연(일)" in old.columns
    assert (old["지연(일)"] > 7).all()


def test_staleness_empty_when_tolerant(cat):
    assert catalog.staleness(cat, today=pd.Timestamp("2026-08-12"),
                             warn_days=100000).empty
