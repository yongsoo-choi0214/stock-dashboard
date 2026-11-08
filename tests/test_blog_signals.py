"""블로그 매매 규칙 신호 검증.

1) 오실레이터: 정의상 성질(범위, index 보존, 단조 추세 방향)
2) 재현: 실제 SOXL 데이터(fixtures/soxl_2024_2025.csv)에서 블로그 글의 매매일이 나오는가
   - 2025-04-04: 3중 웅덩이 O, OBV gold X   (블로그: "OBV가 아직 gold가 안 나서 1차만")
   - 2025-04-07: OBV gold O                 (블로그: "OBV gold까지 나서 더 큰 수량")
   - 2025-08-04: 주봉 매도 신호              (블로그 매도 2025-08-08, 같은 주)
   fixture 출처: Yahoo 일봉(분할 조정 종가) — 블로그 매수가 8.7 / 7.9 와 일치.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.indicators import oscillators as osc
from src.research import blog_signals as bs

FIX = Path(__file__).parent / "fixtures" / "soxl_2024_2025.csv"


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2022-01-03", periods=400)
    c = pd.Series(50 + np.cumsum(rng.normal(0, 1, 400)), index=idx).clip(lower=5)
    spread = rng.uniform(0.2, 1.5, 400)
    return pd.DataFrame({"open": c.shift().bfill(), "high": c + spread,
                         "low": c - spread, "close": c,
                         "volume": rng.uniform(1e6, 2e6, 400)}, index=idx)


@pytest.fixture(scope="module")
def soxl() -> pd.DataFrame:
    return pd.read_csv(FIX, parse_dates=["date"]).set_index("date")


# --- 1. 오실레이터 성질 -------------------------------------------------------
def test_index_preserved(ohlcv):
    h, l, c, v = ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume
    for out in [osc.stoch_slow(h, l, c), osc.cci(h, l, c), osc.dmi(h, l, c), osc.obv(c, v)]:
        assert out.index.equals(ohlcv.index)
    assert bs.features(ohlcv).index.equals(ohlcv.index)


def test_stoch_range(ohlcv):
    st = osc.stoch_slow(ohlcv.high, ohlcv.low, ohlcv.close).dropna()
    assert st.min().min() >= 0 and st.max().max() <= 100


def test_dmi_direction():
    idx = pd.bdate_range("2023-01-02", periods=80)
    up = pd.Series(np.linspace(10, 50, 80), index=idx)
    d = osc.dmi(up + 0.5, up - 0.5, up).dropna()
    assert (d.pdi > d.mdi).all()                        # 단조 상승 → PDI 우위
    dn = up[::-1].set_axis(idx)
    d2 = osc.dmi(dn + 0.5, dn - 0.5, dn).dropna()
    assert (d2.mdi > d2.pdi).all()


def test_obv_sign():
    idx = pd.bdate_range("2023-01-02", periods=4)
    c = pd.Series([10, 11, 10, 10], index=idx, dtype=float)
    v = pd.Series([5, 7, 3, 9], index=idx, dtype=float)
    assert osc.obv(c, v).tolist() == [0, 7, 4, 4]


def test_cci_manual(ohlcv):
    h, l, c = ohlcv.high, ohlcv.low, ohlcv.close
    tp = ((h + l + c) / 3).iloc[-20:]
    ref = (tp.iloc[-1] - tp.mean()) / (0.015 * np.abs(tp - tp.mean()).mean())
    assert osc.cci(h, l, c, 20).iloc[-1] == pytest.approx(ref)


# --- 2. 블로그 매매일 재현 -----------------------------------------------------
def test_blog_buy_dates(soxl):
    f = bs.features(soxl)
    assert f.loc["2025-04-04", "pit"]
    assert not f.loc["2025-04-04", "obv_gold"]
    assert f.loc["2025-04-07", "obv_gold"]
    assert f.loc["2025-04-03":"2025-04-04", "mouth_max"].any()   # 입 최대
    assert f.loc["2025-04-04", "close"] == pytest.approx(8.73)


def test_blog_sell_week(soxl):
    s = bs.daily_sell(soxl)
    hits = s.loc["2025-07-21":"2025-08-15"]
    assert hits[hits].index.tolist() == [pd.Timestamp("2025-08-04")]


def test_no_lookahead_in_weekly_sell(soxl):
    """주봉 신호는 그 주가 끝난 다음 거래일에만 나타나야 한다."""
    s = bs.daily_sell(soxl)
    for day in s[s].index:
        prev = soxl.index[soxl.index < day][-1]          # 직전 거래일은 지난주여야 한다
        assert prev.isocalendar()[:2] != day.isocalendar()[:2]


def test_signals_frame(soxl):
    s = bs.signals(soxl)
    assert list(s.columns) == [k for k, *_ in bs.STAGES]
    assert s.dtypes.eq(bool).all()


def test_event_study_runs(soxl):
    es = bs.event_study(soxl, start="2024-03-01", n_perm=50)
    assert len(es) == 3 and (es["N"] >= 0).all()


def test_episodes_cooldown():
    idx = pd.bdate_range("2023-01-02", periods=50)
    s = pd.Series(False, index=idx)
    s.iloc[[0, 1, 2, 30, 31]] = True
    assert bs.episodes(s, 20)[lambda x: x].index.tolist() == [idx[0], idx[30]]


# --- 3. 텔레그램 알림 규칙 -----------------------------------------------------
def test_alert_on_blog_buy_day(soxl):
    from src.alerts import rules
    al = rules.blog_signals(soxl.loc[:"2025-04-07"], "SOXL", "YF.SOXL", provisional=True)
    keys = {a.key for a in al}
    assert {"blog:YF.SOXL:pit", "blog:YF.SOXL:obv_gold"} <= keys
    assert all("잠정" in a.title for a in al)
    # Telegram legacy Markdown 을 깨는 문자가 본문에 없어야 한다
    for a in al:
        assert not any(ch in a.body for ch in "_*[`")


def test_alert_quiet_in_uptrend(soxl):
    from src.alerts import rules
    al = rules.blog_signals(soxl.loc[:"2025-07-15"], "SOXL", "YF.SOXL")
    assert not [a for a in al if a.key.endswith((":pit", ":obv_gold"))]
