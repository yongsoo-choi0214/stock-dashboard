"""유동성 지표 검증. §7.3(후행 stale 구간)이 실제로 막히는지가 핵심."""
from __future__ import annotations

import pandas as pd
import pytest

from src.indicators import liquidity as lq


@pytest.fixture
def mixed():
    """주간(WALCL/TGA, 수요일) + 일간(ON RRP) 혼합 — 실제 FRED와 같은 구조."""
    wk = pd.date_range("2026-01-07", periods=8, freq="W-WED")
    dl = pd.bdate_range("2026-01-01", "2026-03-06")
    walcl = pd.Series(range(6000, 6000 + len(wk)), index=wk, dtype="float64")
    tga = pd.Series([700.0] * len(wk), index=wk)
    onrrp = pd.Series([10.0] * len(dl), index=dl)
    return walcl, tga, onrrp


def test_net_liquidity_value(mixed):
    walcl, tga, onrrp = mixed
    nl = lq.us_net_liquidity(walcl, tga, onrrp)
    # 첫 주: 6000 - 700 - 10
    assert nl.iloc[0] == pytest.approx(5290.0)


def test_no_stale_tail(mixed):
    """§7.3: 일간 계열이 더 최근까지 있어도 주간 계열의 마지막 값이
    앞으로 복사되어 존재하지 않는 관측치를 만들면 안 된다."""
    walcl, tga, onrrp = mixed
    nl = lq.us_net_liquidity(walcl, tga, onrrp)
    assert nl.index.max() <= walcl.index.max(), (
        f"stale 꼬리 발생: net_liquidity 마지막 {nl.index.max().date()} > "
        f"WALCL 마지막 {walcl.index.max().date()}"
    )


def test_no_future_dates(mixed):
    walcl, tga, onrrp = mixed
    nl = lq.us_net_liquidity(walcl, tga, onrrp)
    valid_until = min(walcl.index.max(), tga.index.max(), onrrp.index.max())
    assert (nl.index <= valid_until).all()


def test_deposit_ratio_alignment():
    """index 가 어긋나도 교집합만 사용한다."""
    d = pd.Series([50.0, 60.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    m = pd.Series([1000.0], index=pd.to_datetime(["2026-01-02"]))
    out = lq.deposit_ratio(d, m)
    assert len(out) == 1
    assert out.iloc[0] == pytest.approx(6.0)


def test_to_change():
    s = pd.Series([1.0, 3.0, 6.0], name="x")
    assert lq.to_change(s).tolist()[1:] == [2.0, 3.0]
    assert lq.to_change(s).name == "x_d1"


def test_on_real_fred():
    """실제 FRED 데이터로 순유동성이 상식 범위인지."""
    from src import store

    w = store.read("macro").pivot(index="date", columns="series_id", values="value")
    need = ["fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"]
    if not all(c in w.columns for c in need):
        pytest.skip("macro.parquet 에 FRED 유동성 3종 없음")

    nl = lq.us_net_liquidity(*[w[c].dropna() for c in need])
    # 단위가 십억 USD 로 맞으면 이 범위를 벗어날 수 없다 (§7.4 회귀 방지)
    assert nl.min() > 0, "순유동성이 음수 — WTREGEN scale 을 의심하라"
    assert nl.max() < 12000, "순유동성 과대 — 단위 정규화 실패"
    assert nl.index.max() <= w["fred.WALCL"].dropna().index.max()
